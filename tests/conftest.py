import json
from pathlib import Path

import pytest
from robonomicsinterface import Keypair

from rrs_admin.proton_pass import PassError

FIXTURES = Path(__file__).parent / "fixtures"
# Public test mnemonics from rrs-ha-integration's fixtures, never used for real accounts.
ACCOUNTS = json.loads((FIXTURES / "accounts.json").read_text())["accounts"]
SITE_MNEMONIC = ACCOUNTS[0]["mnemonic"]
SITE_ADDRESS = ACCOUNTS[0]["account_address"]
PINATA_KEY = "pinata-key-0123456789"
PINATA_SECRET = "pinata-secret-" + "f" * 50


class FakePass:
    """Proton Pass in memory: items are {title: {field: value}}."""

    def __init__(self, items: dict[str, dict[str, str]] | None = None) -> None:
        self.items = items or {}
        self.created: list[dict] = []

    def titles(self, vault):
        return list(self.items)

    def field(self, vault, title, name):
        if title not in self.items or name not in self.items[title]:
            raise PassError(f"cannot read field '{name}' of '{title}'")
        return self.items[title][name]

    def custom_template(self):
        return {"title": "", "note": "", "sections": []}

    def create_custom(self, vault, item):
        self.created.append(item)
        fields = {f["field_name"]: f["value"] for s in item["sections"] for f in s["fields"]}
        self.items[item["title"]] = fields


def site_fields(mnemonic=SITE_MNEMONIC, address=SITE_ADDRESS):
    return {
        "Address": address,
        "Seed Phrase": mnemonic,
        "API Key": PINATA_KEY,
        "API Secret": PINATA_SECRET,
    }


@pytest.fixture
def site_pass():
    return FakePass({f"rrs-site oscar-home - {SITE_ADDRESS}": site_fields()})


def test_fixture_account_is_what_the_integration_derives():
    assert Keypair.from_mnemonic(SITE_MNEMONIC).address == SITE_ADDRESS
