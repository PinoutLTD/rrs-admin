"""The RWS pallet over substrate-interface: read a subscription, set its devices.

This is the one place in rrs-admin that talks to the chain, and the one that
uses substrate-interface. The integration dropped that dependency because it
has to install on ARM and musl inside Home Assistant; this tool runs on a
laptop, where the library's storage-map queries, event decoding and error
messages save a lot of code. Keeping it behind this module leaves the door
open to the integration's own chain code later.

The pool's seed is read from Proton Pass into memory and never printed.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from rrs_admin.redact import REDACT

RWS = "RWS"
SET_DEVICES = "set_devices"


class ChainError(RuntimeError):
    pass


@dataclass(frozen=True)
class Subscription:
    """What the chain says about a pool right now."""

    address: str
    devices: tuple[str, ...]
    kind: str
    issued: datetime | None
    expires: datetime | None
    free_weight: int
    balance: float

    def describe(self) -> list[str]:
        expiry = (
            f"{self.expires:%Y-%m-%d %H:%M} UTC"
            + (" — ИСТЕКЛА" if self.expires < datetime.now(UTC) else "")
            if self.expires
            else "—"
        )
        return [
            f"subscription: {self.kind}, до {expiry}",
            f"free weight:  {self.free_weight}",
            f"balance:      {self.balance:.6f} XRT",
        ]


def _client(url: str):
    # Imported here so the rest of the tool works without the chain library.
    from substrateinterface import SubstrateInterface

    return SubstrateInterface(url=url)


def _keypair(seed: str):
    from substrateinterface import Keypair, KeypairType

    return Keypair.create_from_mnemonic(
        seed, crypto_type=KeypairType.ED25519, ss58_format=32
    )


def read_subscription(url: str, pool: str) -> Subscription:
    chain = _client(url)
    devices = chain.query(RWS, "Devices", [pool]).value or []
    ledger = chain.query(RWS, "Ledger", [pool]).value
    account = chain.query("System", "Account", [pool]).value

    issued = expires = None
    kind = "нет подписки"
    free_weight = 0
    if ledger:
        free_weight = ledger.get("free_weight", 0)
        issued = datetime.fromtimestamp(ledger["issue_time"] / 1000, tz=UTC)
        kind_value = ledger.get("kind") or {}
        name, params = next(iter(kind_value.items()), ("?", {}))
        days = (params or {}).get("days")
        kind = f"{name}{f' {days} days' if days else ''}"
        if days:
            expires = issued + timedelta(days=days)
    return Subscription(
        address=pool,
        devices=tuple(devices),
        kind=kind,
        issued=issued,
        expires=expires,
        free_weight=free_weight,
        balance=(account["data"]["free"] if account else 0) / 1e9,
    )


def account_exists(url: str, address: str) -> bool:
    """An account with nothing on it cannot send even a free transaction."""

    account = _client(url).query("System", "Account", [address]).value
    return bool(account and (account["data"]["free"] or account["nonce"]))


def _addresses_in(chain, call) -> list[str]:
    """Decode the composed call back: set_devices replaces the whole list, so
    what actually got encoded is worth checking before it is signed."""

    decoded = chain.create_scale_object("Call", metadata=chain.get_block_metadata())
    decoded.decode(call.data)
    value = decoded.value["call_args"][0]["value"]
    inner = value[0] if value and isinstance(value[0], list) else value
    return list(inner)


def set_devices(url: str, pool_seed: str, devices: list[str]) -> str:
    """Write the whole device list; returns the block hash that holds it."""

    REDACT.add(pool_seed)
    chain = _client(url)
    keypair = _keypair(pool_seed)
    # `devices` is a BoundedVec, which scalecodec models as a struct holding one
    # field — the inner Vec — so the list goes in wrapped in another list.
    call = chain.compose_call(
        call_module=RWS, call_function=SET_DEVICES, call_params={"devices": [devices]}
    )
    if _addresses_in(chain, call) != list(devices):
        raise ChainError(
            "the encoded call does not carry the device list we planned; nothing was sent"
        )
    extrinsic = chain.create_signed_extrinsic(call=call, keypair=keypair)
    try:
        receipt = chain.submit_extrinsic(extrinsic, wait_for_inclusion=True)
    except Exception as e:
        raise ChainError(REDACT(f"the chain refused set_devices: {e}")) from e
    if not receipt.is_success:
        raise ChainError(f"set_devices failed: {receipt.error_message}")
    return receipt.block_hash


def pool_address_of(seed: str) -> str:
    """The address the pool's seed derives, to prove the item matches the pool."""

    REDACT.add(seed)
    return _keypair(seed).ss58_address
