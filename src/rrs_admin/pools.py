"""Devices of an RWS subscription: what the new list should be, and why.

`RWS.set_devices` **replaces the whole list**, so adding one site means
reading the current list, changing it, and writing all of it back. An error
in that list silently drops sites out of the subscription, which is why the
planning is a pure function with its own tests, separate from signing.

A subscription holds at most `MaxDevicesAmount` (32) devices, and the pool's
own address does not need a slot: the pool itself publishes nothing.
"""

from dataclasses import dataclass

from robonomicsinterface import is_valid_address

MAX_DEVICES = 32


class PoolError(RuntimeError):
    pass


@dataclass(frozen=True)
class DevicePlan:
    """The write that `set_devices` will perform, and how it differs."""

    pool: str
    current: tuple[str, ...]
    devices: tuple[str, ...]
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()

    @property
    def free_slots(self) -> int:
        return MAX_DEVICES - len(self.devices)

    def describe(self) -> list[str]:
        lines = [
            f"pool:      {self.pool}",
            (
                f"devices:   {len(self.current)} → {len(self.devices)} "
                f"(free slots after: {self.free_slots} of {MAX_DEVICES})"
            ),
        ]
        lines += [f"  + {address}" for address in self.added]
        lines += [f"  - {address}" for address in self.removed]
        lines += [f"    {address}" for address in self.devices if address not in self.added]
        return lines


def check_address(address: str) -> str:
    # Robonomics format only: the chain returns the list in format 32, so a `5…`
    # spelling of a listed device would slip past the duplicate check.
    if not is_valid_address(address):
        raise PoolError(f"'{address}' is not a Robonomics address")
    return address


def plan_add(pool: str, current: list[str], address: str) -> DevicePlan:
    check_address(address)
    if address in current:
        raise PoolError(f"{address} is already a device of {pool}: nothing to write")
    if address == pool:
        raise PoolError(
            "the pool's own address does not need a slot: the pool publishes nothing"
        )
    devices = (*current, address)
    if len(devices) > MAX_DEVICES:
        raise PoolError(
            f"{pool} would hold {len(devices)} devices, over the limit of {MAX_DEVICES}: "
            "the site belongs in another pool"
        )
    return DevicePlan(pool, tuple(current), devices, added=(address,))


def plan_remove(pool: str, current: list[str], address: str) -> DevicePlan:
    check_address(address)
    if address not in current:
        raise PoolError(f"{address} is not a device of {pool}: nothing to write")
    devices = tuple(a for a in current if a != address)
    return DevicePlan(pool, tuple(current), devices, removed=(address,))
