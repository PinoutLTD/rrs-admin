import pytest
from conftest import (
    ACCOUNTS,
    PINATA_KEY,
    PINATA_SECRET,
    SITE_ADDRESS,
    FakePass,
    site_fields,
)
from robonomicsinterface import Keypair

from rrs_admin.sites import (
    PinataKeys,
    SiteError,
    check_client_id,
    create_site_key,
    find_site_titles,
    read_site,
)

PINATA = PinataKeys(PINATA_KEY, PINATA_SECRET)


def test_every_fixture_account_derives_its_robonomics_address():
    for account in ACCOUNTS:
        assert Keypair.from_mnemonic(account["mnemonic"]).address == account["account_address"]


def test_new_site_key_lands_in_proton_pass_and_agrees_with_itself():
    passes = FakePass()
    address, title = create_site_key(passes, "Report Service", "oscar-home", PINATA)

    assert title == f"rrs-site oscar-home - {address}"
    stored = passes.items[title]
    assert Keypair.from_secret(stored["Seed Phrase"]).address == address
    assert stored["Address"] == address
    assert (stored["API Key"], stored["API Secret"]) == (PINATA_KEY, PINATA_SECRET)
    assert stored["client_id"] == "oscar-home" and stored["Role"] == "site"
    hidden = {f["field_name"] for s in passes.created[0]["sections"] for f in s["fields"]
              if f["field_type"] == "hidden"}
    assert hidden == {"Seed Phrase", "API Key", "API Secret"}


def test_a_site_gets_one_key_only(site_pass):
    with pytest.raises(SiteError, match="already has a key"):
        create_site_key(site_pass, "Report Service", "oscar-home", PINATA)


def test_draft_items_without_an_address_do_not_count():
    titles = ["rrs-site oscar-home - ", "rrs-site oscar-home - not-an-address",
              f"rrs-site oscar-home-2 - {SITE_ADDRESS}", f"rrs-site oscar-home - {SITE_ADDRESS}"]
    assert find_site_titles(titles, "oscar-home") == [f"rrs-site oscar-home - {SITE_ADDRESS}"]


def test_read_site_returns_checked_secrets(site_pass):
    site = read_site(site_pass, "Report Service", "oscar-home")
    assert site.address == SITE_ADDRESS
    assert "frozen" not in repr(site) and PINATA_SECRET not in repr(site)


def test_read_site_refuses_an_item_whose_seed_and_address_disagree():
    other = ACCOUNTS[1]
    passes = FakePass({f"rrs-site oscar-home - {SITE_ADDRESS}": site_fields(mnemonic=other["mnemonic"])})
    with pytest.raises(SiteError, match="does not agree"):
        read_site(passes, "Report Service", "oscar-home")


def test_read_site_requires_pinata_keys():
    fields = site_fields()
    fields["API Secret"] = ""
    passes = FakePass({f"rrs-site oscar-home - {SITE_ADDRESS}": fields})
    with pytest.raises(SiteError, match="Pinata"):
        read_site(passes, "Report Service", "oscar-home")


@pytest.mark.parametrize("slug", ["Oscar-Home", "oscar home", "-oscar", "a"])
def test_client_id_is_a_slug(slug):
    with pytest.raises(SiteError):
        check_client_id(slug)
