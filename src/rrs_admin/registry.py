"""Where a site's Home Assistant is, from Fotis's registry (fotis-agent/registry.yaml).

The registry holds no secrets: the HA token sits in Proton Pass, in the item the
registry names, field `ha_token` — the same one Fotis's `ha-open` uses.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

TOKEN_FIELD = "ha_token"


class RegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class HaSite:
    name: str
    url: str
    pass_vault: str
    pass_item: str


def normalize(slug: str) -> str:
    """Slugs drift between repositories: evergreen-a-201 vs evergreen-a201."""

    return re.sub(r"[^a-z0-9]", "", slug.lower())


def find_ha_site(registry: Path, client_id: str, remote: bool = False) -> HaSite:
    if not registry.is_file():
        raise RegistryError(f"Fotis's registry not found at {registry}")
    clients = (yaml.safe_load(registry.read_text(encoding="utf-8")) or {}).get("clients", [])
    exact = [c for c in clients if c.get("name") == client_id]
    loose = [c for c in clients if normalize(str(c.get("name", ""))) == normalize(client_id)]
    matches = exact or loose
    if len(matches) != 1:
        raise RegistryError(
            f"site '{client_id}' is {'not' if not matches else 'ambiguously'} in {registry}"
        )
    entry = matches[0]
    if remote:
        host, port = entry.get("remote_host"), entry.get("remote_port") or entry.get("port")
        if not host:
            raise RegistryError(f"site '{entry['name']}' has no remote_host in the registry")
    else:
        host, port = entry.get("host"), entry.get("port")
    if not host or not entry.get("pass_vault") or not entry.get("pass_item"):
        raise RegistryError(f"site '{entry['name']}' lacks host or pass_vault/pass_item")
    return HaSite(
        name=entry["name"],
        url=f"http://{host}:{port or 8123}",
        pass_vault=entry["pass_vault"],
        pass_item=entry["pass_item"],
    )
