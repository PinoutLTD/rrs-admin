"""Home Assistant's config flow over its REST API, one step at a time.

The flow is walked step by step on purpose. A tool that submits "everything it
has" in one go also passes steps that only show something — which is how a
site's generated seed was lost once. Here each step is checked by its id before
anything is sent, and the only thing kept from a step is what this tool needs:
type, step id, errors, abort reason, entry id. `description_placeholders` — where
Home Assistant puts what a step shows, a generated seed included — is dropped.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from rrs_admin.redact import REDACT


class HaError(RuntimeError):
    pass


@dataclass(frozen=True)
class Step:
    type: str
    flow_id: str | None = None
    step_id: str | None = None
    errors: dict = field(default_factory=dict)
    reason: str | None = None
    entry_id: str | None = None

    @classmethod
    def from_result(cls, result: dict) -> "Step":
        entry = result.get("result")
        entry_id = entry.get("entry_id") if isinstance(entry, dict) else entry
        return cls(
            type=result.get("type", ""),
            flow_id=result.get("flow_id"),
            step_id=result.get("step_id"),
            errors=dict(result.get("errors") or {}),
            reason=result.get("reason"),
            entry_id=entry_id if isinstance(entry_id, str) else None,
        )


class HomeAssistant:
    def __init__(self, url: str, token: str, timeout: float = 60) -> None:
        self.url = url.rstrip("/")
        self._token = token
        self.timeout = timeout
        REDACT.add(token)

    def _request(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{self.url}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read() or b"{}").get("message", "")
            except (ValueError, AttributeError):
                pass
            raise HaError(REDACT(f"{method} {path}: HTTP {e.code} {detail}".strip())) from e
        except urllib.error.URLError as e:
            raise HaError(f"cannot reach Home Assistant at {self.url}: {e.reason}") from e
        return json.loads(raw) if raw else None

    def check(self) -> str:
        """The API answers and the token is accepted; returns HA's version."""

        config = self._request("GET", "/api/config")
        return str((config or {}).get("version", "unknown"))

    def entries(self, domain: str) -> list[dict]:
        entries = self._request("GET", "/api/config/config_entries/entry") or []
        return [
            {k: e.get(k) for k in ("entry_id", "title", "state", "domain")}
            for e in entries
            if e.get("domain") == domain
        ]

    def delete_entry(self, entry_id: str) -> None:
        self._request("DELETE", f"/api/config/config_entries/entry/{entry_id}")

    def start_flow(self, domain: str) -> Step:
        return Step.from_result(
            self._request(
                "POST",
                "/api/config/config_entries/flow",
                {"handler": domain, "show_advanced_options": False},
            )
        )

    def submit(self, flow_id: str, data: dict) -> Step:
        return Step.from_result(
            self._request("POST", f"/api/config/config_entries/flow/{flow_id}", data)
        )

    def abort_flow(self, flow_id: str) -> None:
        try:
            self._request("DELETE", f"/api/config/config_entries/flow/{flow_id}")
        except HaError:
            pass
