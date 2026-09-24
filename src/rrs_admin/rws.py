"""The RWS pallet through robonomics-interface: read a subscription, set its devices.

This is the one place in rrs-admin that talks to the chain. A command opens one
connection (`connect`) and passes it to every call here, so the runtime
metadata is loaded once per command rather than once per read.

The pool's seed is read from Proton Pass into memory and never printed.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from robonomicsinterface import (
    XRT,
    ExtrinsicOutcomeUnknown,
    Keypair,
    RobonomicsSync,
    TransactionError,
    TransportError,
)

from rrs_admin.redact import REDACT


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


def connect(url: str) -> RobonomicsSync:
    """One connection for a command; use it as a context manager."""

    try:
        return RobonomicsSync(url).connect()
    except TransportError as e:
        raise ChainError(f"no Robonomics node answered at {url}: {e}") from e


def read_subscription(chain: RobonomicsSync, pool: str) -> Subscription:
    devices = chain.rws.devices(pool)
    ledger = chain.rws.ledger(pool)
    account = chain.system.account(pool)

    if ledger is None:
        kind, issued, expires, free_weight = "нет подписки", None, None, 0
    else:
        kind = f"{ledger.kind} {ledger.days} days" if ledger.days else ledger.kind
        issued, expires = ledger.issued_at, ledger.expires_at
        free_weight = ledger.free_weight
    return Subscription(
        address=pool,
        devices=tuple(devices),
        kind=kind,
        issued=issued,
        expires=expires,
        free_weight=free_weight,
        balance=account.free / XRT,
    )


def account_exists(chain: RobonomicsSync, address: str) -> bool:
    """An account with nothing on it cannot send even a free transaction."""

    return chain.system.exists(address)


def set_devices(chain: RobonomicsSync, pool_seed: str, devices: list[str]) -> str:
    """Write the whole device list; returns the block hash that holds it.

    The list goes as it is, flat: the library encodes the BoundedVec itself.
    Raises when the call failed inside the block, not only when it was refused.
    """

    REDACT.add(pool_seed)
    pool = Keypair.from_secret(pool_seed)
    try:
        result = chain.rws.set_devices(pool, devices)
    except ExtrinsicOutcomeUnknown as e:
        raise ChainError(
            "set_devices was sent, but whether it landed is unknown "
            f"(extrinsic {e.extrinsic_hash}). Do not send it again: check the "
            "devices with `rrs-admin pool` first"
        ) from e
    except TransactionError as e:
        raise ChainError(REDACT(f"the chain refused set_devices: {e}")) from e
    return result.block_hash


def pool_address_of(seed: str) -> str:
    """The address the pool's seed derives, to prove the item matches the pool."""

    REDACT.add(seed)
    return Keypair.from_secret(seed).address
