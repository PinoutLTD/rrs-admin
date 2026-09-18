import pytest
from conftest import SITE_ADDRESS, FakePass, site_fields

from rrs_admin import cli
from rrs_admin.pinata import PinataIssuer, key_name
from rrs_admin.proton_pass import PassError

JWT = "eyJhbGciOiJIUzI1NiJ9.issuer-payload.signature-part"
NEW_KEY, NEW_SECRET = "0123456789abcdef0123", "s" * 64


class FakePinata:
    def __init__(self):
        self.calls = []
        self.keys = [
            {"key": "k-old", "name": "rrs-site oscar-home", "createdAt": "2026-09-18T10:00:00Z",
             "revoked": False, "scopes": {"admin": True}},
            {"key": "k-other", "name": "rrs-site oscar-home-2", "createdAt": "2026-09-18T10:00:00Z",
             "revoked": False, "scopes": {}},
        ]

    def __call__(self, issuer, method, url, body=None):
        self.calls.append((method, url, body))
        if method == "POST":
            return {"JWT": "eyJnew.jwt.value", "pinata_api_key": NEW_KEY, "pinata_api_secret": NEW_SECRET}
        if method == "GET":
            return {"keys": self.keys, "count": len(self.keys)}
        if method == "DELETE":
            return "Revoked"
        raise AssertionError(method)


@pytest.fixture
def fake_pinata(monkeypatch):
    fake = FakePinata()
    monkeypatch.setattr(PinataIssuer, "_request", lambda self, method, url, body=None: fake(self, method, url, body))
    return fake


def test_site_keys_are_issued_with_upload_and_unpin_only(fake_pinata):
    keys = PinataIssuer(JWT).issue_site_key("oscar-home")
    assert (keys.key, keys.secret) == (NEW_KEY, NEW_SECRET)
    method, _, body = fake_pinata.calls[0]
    assert method == "POST"
    assert body == {
        "keyName": "rrs-site oscar-home",
        "permissions": {"admin": False,
                        "endpoints": {"pinning": {"pinFileToIPFS": True, "unpin": True}}},
    }


def test_keys_are_matched_by_exact_name(fake_pinata):
    found = PinataIssuer(JWT).list_keys(name=key_name("oscar-home"))
    assert [k.id for k in found] == ["k-old"]


@pytest.fixture
def run_cli(monkeypatch, tmp_path):
    config = tmp_path / "rrs-admin.toml"
    config.write_text(
        '[addresses]\nrecipient = "4R"\npool = "4P"\n'
        '[proton]\nsites_vault = "Report Service"\n'
        '[fotis]\nregistry = "registry.yaml"\n'
    )
    passes = FakePass({"rrs-pinata-issuer": {"JWT": JWT}})
    monkeypatch.setattr(cli, "PassClient", lambda reason: passes)
    monkeypatch.setattr(cli, "wait_until_accepted", lambda keys: None)

    def run(*argv):
        return cli.main(["--config", str(config), *argv])

    run.passes = passes
    return run


def test_new_site_key_issues_pinata_keys_into_the_site_item(fake_pinata, run_cli, capsys):
    assert run_cli("new-site-key", "oscar-home") == 0
    [title] = [t for t in run_cli.passes.items if t.startswith("rrs-site oscar-home - ")]
    item = run_cli.passes.items[title]
    assert (item["API Key"], item["API Secret"]) == (NEW_KEY, NEW_SECRET)
    output = capsys.readouterr()
    for secret in (NEW_SECRET, JWT, "eyJnew.jwt.value", item["Seed Phrase"]):
        assert secret not in output.out + output.err


def test_an_issued_key_is_revoked_when_the_item_cannot_be_created(fake_pinata, run_cli, monkeypatch):
    def fail(vault, item):
        raise PassError("vault is read-only")

    monkeypatch.setattr(run_cli.passes, "create_custom", fail)
    assert run_cli("new-site-key", "oscar-home") == 1
    assert ("DELETE", f"https://api.pinata.cloud/v3/api_keys/{NEW_KEY}", None) in fake_pinata.calls


def test_a_site_with_a_key_gets_no_new_pinata_key(fake_pinata, run_cli):
    run_cli.passes.items[f"rrs-site oscar-home - {SITE_ADDRESS}"] = site_fields()
    assert run_cli("new-site-key", "oscar-home") == 1
    assert not [c for c in fake_pinata.calls if c[0] == "POST"]


def test_pinata_keys_lists_and_revokes_only_on_request(fake_pinata, run_cli, capsys):
    assert run_cli("pinata-keys", "oscar-home") == 0
    assert "k-old" in capsys.readouterr().out
    assert not [c for c in fake_pinata.calls if c[0] == "DELETE"]

    assert run_cli("pinata-keys", "oscar-home", "--revoke") == 0
    deletes = [c[1] for c in fake_pinata.calls if c[0] == "DELETE"]
    assert deletes == ["https://api.pinata.cloud/v3/api_keys/k-old"]


def test_a_key_is_found_by_its_name_as_well_as_its_id(fake_pinata, run_cli, capsys):
    fake_pinata.keys.append({"key": "k-hand", "name": "oscar-home", "createdAt": "2026-09-18",
                             "revoked": False, "scopes": {"admin": True}})
    assert run_cli("pinata-keys", "--key", "oscar-home", "--revoke") == 0
    assert [c[1] for c in fake_pinata.calls if c[0] == "DELETE"] == [
        "https://api.pinata.cloud/v3/api_keys/k-hand"]
    assert run_cli("pinata-keys", "--key", "k-other") == 0
    assert "k-other" in capsys.readouterr().out
