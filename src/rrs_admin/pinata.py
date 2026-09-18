"""Pinata keys for sites, issued and revoked through Pinata's API.

A site gets a key that can do exactly what the integration does: upload a report
(`pinFileToIPFS`) and unpin it when publishing fails (`unpin`). Such a key, leaked
from a client's machine, cannot list the account's files or issue keys.

Keys are issued with the issuer: an account key with `org:write`, kept in the
human-only vault next to the pool keys. The JWT Pinata returns with a new key is
as strong as the key pair and is dropped at once: the integration uses the pair.

Every site key is named `rrs-site <client_id>`, so a site's keys can be found and
revoked by name.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from rrs_admin.redact import REDACT
from rrs_admin.sites import PinataKeys, SiteError, check_pinata

API = "https://api.pinata.cloud/v3/api_keys"
SITE_SCOPE = {"pinning": {"pinFileToIPFS": True, "unpin": True}}


class PinataError(RuntimeError):
    pass


def key_name(client_id: str) -> str:
    return f"rrs-site {client_id}"


@dataclass(frozen=True)
class IssuedKey:
    id: str
    name: str
    created: str
    revoked: bool
    scopes: dict


class PinataIssuer:
    def __init__(self, jwt: str, timeout: float = 30) -> None:
        self._jwt = jwt
        self.timeout = timeout
        REDACT.add(jwt)

    def _request(self, method: str, url: str, body: dict | None = None):
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Authorization": f"Bearer {self._jwt}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise PinataError(
                    "Pinata refused the issuer key: it needs org:write (or admin) and must "
                    "not be revoked"
                ) from e
            raise PinataError(f"Pinata answered {e.code} to {method} {url}") from e
        except urllib.error.URLError as e:
            raise PinataError(f"cannot reach Pinata: {e.reason}") from e
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return raw.decode(errors="replace")

    def list_keys(self, name: str | None = None, include_revoked: bool = False) -> list[IssuedKey]:
        found, offset = [], 0
        while True:
            query = {"offset": offset}
            if name:
                query["name"] = name
            if not include_revoked:
                query["revoked"] = "false"
            data = self._request("GET", f"{API}?{urllib.parse.urlencode(query)}") or {}
            page = data.get("keys", [])
            for key in page:
                found.append(IssuedKey(
                    id=key.get("key", ""),
                    name=key.get("name", ""),
                    created=str(key.get("createdAt", ""))[:10],
                    revoked=bool(key.get("revoked")),
                    scopes=key.get("scopes") or {},
                ))
            if not page or len(page) < 10:
                break
            offset += len(page)
        # `name` filters with ilike, so "rrs-site a" would also match "rrs-site a-2".
        return [k for k in found if name is None or k.name == name]

    def issue_site_key(self, client_id: str) -> PinataKeys:
        data = self._request("POST", API, {
            "keyName": key_name(client_id),
            "permissions": {"admin": False, "endpoints": SITE_SCOPE},
        }) or {}
        key, secret = data.get("pinata_api_key"), data.get("pinata_api_secret")
        data.pop("JWT", None)
        if not key or not secret:
            raise PinataError("Pinata did not return a key pair")
        REDACT.add(key, secret)
        return PinataKeys(key, secret)

    def revoke(self, api_key: str) -> None:
        self._request("DELETE", f"{API}/{urllib.parse.quote(api_key)}")


def wait_until_accepted(pinata: PinataKeys, attempts: int = 5, delay: float = 2) -> None:
    """A fresh key can take a moment to be honoured; give it a few tries."""

    for attempt in range(attempts):
        try:
            check_pinata(pinata)
            return
        except SiteError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
