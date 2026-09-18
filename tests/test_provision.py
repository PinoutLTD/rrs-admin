"""The config flow against a fake Home Assistant that behaves like the real one."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from conftest import PINATA_SECRET, SITE_MNEMONIC

from rrs_admin.ha import HomeAssistant
from rrs_admin.provision import Plan, ProvisionError, provision
from rrs_admin.redact import REDACT
from rrs_admin.sites import read_site

TOKEN = "ha-long-lived-token-abcdefghijklmnop"
GENERATED = "abandon ability able about above absent absorb abstract absurd abuse access accident"


class FakeHA:
    def __init__(self, entries=None, user_errors=None):
        self.entries = entries or []
        self.user_errors = user_errors or {}
        self.requests: list[tuple[str, str, dict | None]] = []
        self.aborted: list[str] = []
        self.deleted: list[str] = []

    def handle(self, method, path, body):
        self.requests.append((method, path, body))
        if path == "/api/config":
            return {"version": "2026.7.0"}
        if path == "/api/config/config_entries/entry" and method == "GET":
            return self.entries
        if path.startswith("/api/config/config_entries/entry/") and method == "DELETE":
            entry_id = path.rsplit("/", 1)[1]
            self.deleted.append(entry_id)
            self.entries = [e for e in self.entries if e["entry_id"] != entry_id]
            return {"require_restart": False}
        if path == "/api/config/config_entries/flow":
            return {"type": "form", "flow_id": "f1", "step_id": "user", "errors": {}}
        if path == "/api/config/config_entries/flow/f1" and method == "DELETE":
            self.aborted.append("f1")
            return None
        if path == "/api/config/config_entries/flow/f1":
            if "sender_seed" not in body:
                if self.user_errors:
                    return {"type": "form", "flow_id": "f1", "step_id": "user",
                            "errors": self.user_errors}
                # Like the integration: the seed step shows a generated seed.
                return {"type": "form", "flow_id": "f1", "step_id": "seed", "errors": {},
                        "description_placeholders": {"generated_seed": GENERATED,
                                                     "generated_address": "4XYZ"}}
            self.entries.append({"entry_id": "e1", "domain": "robonomics_report_service",
                                 "title": "Robonomics Report Service", "state": "loaded"})
            return {"type": "create_entry", "flow_id": "f1",
                    "result": {"entry_id": "e1", "domain": "robonomics_report_service"}}
        raise AssertionError(f"unexpected {method} {path}")


@pytest.fixture
def fake_ha():
    state = {"ha": FakeHA()}

    class Handler(BaseHTTPRequestHandler):
        def _serve(self):
            assert self.headers["Authorization"] == f"Bearer {TOKEN}"
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            result = state["ha"].handle(self.command, self.path, body)
            payload = json.dumps(result).encode() if result is not None else b""
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_DELETE = _serve

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()


def make_plan(site_pass):
    return Plan(site=read_site(site_pass, "Report Service", "oscar-home"),
                network="polkadot", recipient="4GsB", pool="4G6Z", email=None)


def run(fake_ha, site_pass, **kwargs):
    lines = []
    ha = HomeAssistant(fake_ha["url"], TOKEN)
    entry = provision(ha, make_plan(site_pass), say=lines.append, wait_seconds=2, **kwargs)
    return entry, lines


def test_the_flow_is_walked_step_by_step_with_the_site_seed(fake_ha, site_pass):
    entry, _ = run(fake_ha, site_pass)
    assert entry == "e1"
    posts = [(p, b) for m, p, b in fake_ha["ha"].requests if m == "POST"]
    assert posts[0] == ("/api/config/config_entries/flow",
                        {"handler": "robonomics_report_service", "show_advanced_options": False})
    user = posts[1][1]
    assert user["problem_service_robonomics_address"] == "4GsB"
    assert user["subscription_owner_robonomics_address"] == "4G6Z"
    assert "sender_email" not in user
    assert posts[2][1] == {"sender_seed": SITE_MNEMONIC}


def test_nothing_secret_is_printed(fake_ha, site_pass):
    _, lines = run(fake_ha, site_pass)
    output = "\n".join(lines)
    for secret in (SITE_MNEMONIC, GENERATED, PINATA_SECRET, TOKEN):
        assert secret not in output


def test_a_rejected_step_aborts_the_flow(fake_ha, site_pass):
    fake_ha["ha"] = FakeHA(user_errors={"base": "invalid_pinata_keys"})
    with pytest.raises(ProvisionError, match="Pinata rejected"):
        run(fake_ha, site_pass)
    assert fake_ha["ha"].aborted == ["f1"]


def test_an_existing_entry_is_kept_unless_replace_is_asked(fake_ha, site_pass):
    old = {"entry_id": "old", "domain": "robonomics_report_service",
           "title": "Robonomics Report Service", "state": "loaded"}
    fake_ha["ha"] = FakeHA(entries=[dict(old)])
    with pytest.raises(ProvisionError, match="--replace"):
        run(fake_ha, site_pass)
    assert fake_ha["ha"].deleted == []

    fake_ha["ha"] = FakeHA(entries=[dict(old)])
    entry, _ = run(fake_ha, site_pass, replace=True)
    assert fake_ha["ha"].deleted == ["old"] and entry == "e1"


def test_redactor_masks_secrets_in_any_text():
    REDACT.add("a-secret-value-123")
    assert REDACT("error: a-secret-value-123 rejected") == "error: ‹hidden› rejected"
