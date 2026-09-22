"""Settings: public addresses and where things live. No secrets here."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "rrs-admin.toml"
CONFIG_ENV = "RRS_ADMIN_CONFIG"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    recipient: str
    pool: str
    network: str
    sites_vault: str
    fotis_registry: Path
    issuer_vault: str
    issuer_item: str
    chain_url: str
    pools: dict[str, dict]


def config_path(explicit: Path | None) -> Path:
    if explicit:
        return explicit
    if os.environ.get(CONFIG_ENV):
        return Path(os.environ[CONFIG_ENV])
    return DEFAULT_CONFIG


def load_config(path: Path) -> Config:
    if not path.is_file():
        raise ConfigError(
            f"no config at {path}: copy config/rrs-admin.example.toml to "
            "config/rrs-admin.toml and fill in the addresses"
        )
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    base = path.resolve().parent
    try:
        addresses, proton, fotis = data["addresses"], data["proton"], data["fotis"]
        pinata = data.get("pinata", {})
        chain = data.get("chain", {})
        return Config(
            recipient=addresses["recipient"],
            pool=addresses["pool"],
            network=addresses.get("network", "polkadot"),
            sites_vault=proton["sites_vault"],
            fotis_registry=(base / fotis["registry"]).resolve(),
            issuer_vault=pinata.get("issuer_vault", "Robonomics Pools"),
            issuer_item=pinata.get("issuer_item", "rrs-pinata-issuer"),
            chain_url=chain.get("url", "wss://polkadot.rpc.robonomics.network/"),
            pools=dict(data.get("pools", {})),
        )
    except KeyError as e:
        raise ConfigError(f"{path}: missing setting {e}") from e
