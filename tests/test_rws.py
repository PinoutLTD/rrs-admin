"""The chain calls, against a client that stands in for robonomics-interface."""

from datetime import UTC, datetime

import pytest
from conftest import ACCOUNTS
from robonomicsinterface import (
    XRT,
    AccountInfo,
    ExtrinsicFailed,
    ExtrinsicOutcomeUnknown,
    Ledger,
)

from rrs_admin import rws
from rrs_admin.redact import REDACT

# Accounts of the published test mnemonics in tests/fixtures, not real sites.
POOL_MNEMONIC = ACCOUNTS[1]["mnemonic"]
POOL = ACCOUNTS[1]["account_address"]
A = ACCOUNTS[0]["account_address"]
ISSUED_MS = 1_789_000_000_000


class FakeRWS:
    def __init__(self, devices, ledger, error=None):
        self._devices, self._ledger, self.error = devices, ledger, error
        self.written = []

    def devices(self, address):
        return list(self._devices)

    def ledger(self, address):
        return self._ledger

    def set_devices(self, owner, devices):
        self.written.append((owner.address, devices))
        if self.error:
            raise self.error
        return type("Result", (), {"block_hash": "0xblock"})()


class FakeSystem:
    def __init__(self, free):
        self.free = free

    def account(self, address):
        return AccountInfo(
            nonce=0, consumers=0, providers=1, sufficients=0,
            free=self.free, reserved=0, frozen=0,
        )


class FakeChain:
    def __init__(self, devices=(), ledger=None, free=0, error=None):
        self.rws = FakeRWS(devices, ledger, error)
        self.system = FakeSystem(free)


def daily(days):
    return Ledger(
        kind="Daily", days=days, tps=None,
        issue_time_ms=ISSUED_MS, last_update_ms=ISSUED_MS, free_weight=7,
    )


def test_a_daily_subscription_reads_with_its_end_date():
    chain = FakeChain(devices=[A], ledger=daily(30), free=3 * XRT // 2)

    sub = rws.read_subscription(chain, POOL)

    assert sub.devices == (A,)
    assert sub.kind == "Daily 30 days"
    assert sub.issued == datetime.fromtimestamp(ISSUED_MS / 1000, tz=UTC)
    assert (sub.expires - sub.issued).days == 30
    assert sub.free_weight == 7
    assert sub.balance == 1.5


def test_a_pool_without_a_subscription_says_so():
    sub = rws.read_subscription(FakeChain(), POOL)

    assert sub.kind == "нет подписки"
    assert sub.expires is None
    assert sub.describe()[0] == "subscription: нет подписки, до —"


def test_the_device_list_is_written_flat_and_signed_by_the_pool():
    chain = FakeChain(devices=[A])

    block = rws.set_devices(chain, POOL_MNEMONIC, [A, POOL])

    # No [[...]] wrapping: the library encodes the BoundedVec itself.
    assert chain.rws.written == [(POOL, [A, POOL])]
    assert block == "0xblock"


def test_a_call_that_failed_in_the_block_is_an_error():
    failed = ExtrinsicFailed(
        "RWS.NotLinkedDevice: the signer is not a device of this subscription",
        pallet="RWS", error="NotLinkedDevice", docs="", result=None,
    )
    chain = FakeChain(error=failed)

    with pytest.raises(rws.ChainError, match="NotLinkedDevice"):
        rws.set_devices(chain, POOL_MNEMONIC, [A])


def test_an_unknown_outcome_says_not_to_send_again():
    chain = FakeChain(error=ExtrinsicOutcomeUnknown("0xabc", "connection lost"))

    with pytest.raises(rws.ChainError, match="Do not send it again") as error:
        rws.set_devices(chain, POOL_MNEMONIC, [A])
    assert "0xabc" in str(error.value)


def test_the_pool_seed_is_redacted_and_derives_the_pool():
    assert rws.pool_address_of(POOL_MNEMONIC) == POOL
    assert POOL_MNEMONIC not in REDACT(f"failed with {POOL_MNEMONIC}")
