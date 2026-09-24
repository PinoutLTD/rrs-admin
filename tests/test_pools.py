import pytest

from rrs_admin.pools import MAX_DEVICES, PoolError, plan_add, plan_remove

# Accounts of the published test mnemonics in tests/fixtures, not real sites.
POOL = "4GuDRQsfH71yHM9aL4Kfu3SCzW6cFDGi45k9VYGNNis9v76D"  # from the raw seed 0x11…11
A = "4GEGoUCyDw2FhnFtniYfo1xTqxBUhKZYRWkWyJ8BamqadVxp"
B = "4DNteL1am4a3JBt2XE4cwrZ4YYxQztjMFUUdkrv24EgSvXfz"
NEW = "4FpgsePLu44Qvwxcd1yGyuL7RLxnETCjNcdvqdjvGQT3eA38"  # from the raw seed 0x22…22


def test_adding_keeps_every_device_already_in_the_subscription():
    plan = plan_add(POOL, [A, B], NEW)

    # set_devices replaces the whole list: a plan that forgets a device
    # silently drops that site out of the subscription.
    assert plan.devices == (A, B, NEW)
    assert plan.added == (NEW,) and plan.removed == ()
    assert plan.free_slots == MAX_DEVICES - 3


def test_removing_leaves_the_others_untouched():
    plan = plan_remove(POOL, [A, B, NEW], B)

    assert plan.devices == (A, NEW)
    assert plan.removed == (B,)


@pytest.mark.parametrize(
    ("current", "address", "message"),
    [
        ([A, NEW], NEW, "already a device"),
        ([A], POOL, "does not need a slot"),
        ([A], "not-an-address", "not a Robonomics address"),
        # A itself in the generic Substrate format: the same key, but the chain
        # lists devices in format 32, so it would look like a new device.
        ([A], "5G6sPPd14qmXGNJXr3Lua2ciJocRF9rkJvHq88ZCDKnTSWZe", "not a Robonomics address"),
    ],
)
def test_a_write_that_would_change_nothing_or_waste_a_slot_is_refused(current, address, message):
    with pytest.raises(PoolError, match=message):
        plan_add(POOL, current, address)


def test_the_limit_of_32_devices_is_the_pools_limit():
    full = [f"4{'x' * 47}"] * MAX_DEVICES
    with pytest.raises(PoolError, match="over the limit"):
        plan_add(POOL, full, NEW)


def test_removing_an_address_that_is_not_there_is_refused():
    with pytest.raises(PoolError, match="not a device"):
        plan_remove(POOL, [A], NEW)
