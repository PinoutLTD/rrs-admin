"""Site keys: the account a site's Home Assistant publishes its reports from.

A site key is born in Proton Pass, not in Home Assistant: the integrator
creates it before the installation, so the seed never has to be shown to
whoever — or whatever — installs the integration. The item carries all the
site's secrets together: the seed and the site's own Pinata keys.

Item: vault `Report Service`, title `rrs-site <client_id> - <address>`.
"""

import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from rrs_admin.chain import Keypair, SS58Error, ss58_decode
from rrs_admin.proton_pass import PassClient, PassError
from rrs_admin.redact import REDACT

TITLE_PREFIX = "rrs-site "
SECTION = "Robonomics"
ROLE = "site"
NETWORK_LABEL = "Robonomics (Polkadot)"

FIELD_ADDRESS = "Address"
FIELD_SEED = "Seed Phrase"
FIELD_NETWORK = "Network"
FIELD_PINATA_KEY = "API Key"
FIELD_PINATA_SECRET = "API Secret"
FIELD_CLIENT_ID = "client_id"
FIELD_ROLE = "Role"

CLIENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
PINATA_CHECK_URL = "https://api.pinata.cloud/data/testAuthentication"


class SiteError(RuntimeError):
    pass


@dataclass(frozen=True)
class PinataKeys:
    key: str
    secret: str

    def __repr__(self) -> str:
        return "PinataKeys(‹hidden›)"


@dataclass(frozen=True)
class SiteSecrets:
    client_id: str
    title: str
    address: str
    seed: str
    pinata: PinataKeys

    def __repr__(self) -> str:
        return f"SiteSecrets({self.client_id}, {self.address}, ‹hidden›)"


def check_client_id(client_id: str) -> str:
    if not CLIENT_ID.match(client_id):
        raise SiteError(
            f"'{client_id}' is not a site slug: lowercase letters, digits and hyphens, "
            "the same as the site's folder in fotis-agent/clients/"
        )
    return client_id


def site_title(client_id: str, address: str) -> str:
    return f"{TITLE_PREFIX}{client_id} - {address}"


def find_site_titles(titles: list[str], client_id: str) -> list[str]:
    """Items of this site that already hold an address."""

    prefix = f"{TITLE_PREFIX}{client_id} - "
    found = []
    for title in titles:
        if title.startswith(prefix):
            address = title[len(prefix):].strip()
            try:
                ss58_decode(address)
            except (SS58Error, ValueError):
                continue  # a draft item without a real address yet
            found.append(title)
    return found


def site_item(
    template: dict, client_id: str, address: str, seed: str, pinata: PinataKeys
) -> dict:
    item = dict(template)
    item["title"] = site_title(client_id, address)
    item["sections"] = [
        {
            "section_name": SECTION,
            "fields": [
                {"field_name": FIELD_ADDRESS, "field_type": "text", "value": address},
                {"field_name": FIELD_SEED, "field_type": "hidden", "value": seed},
                {"field_name": FIELD_NETWORK, "field_type": "text", "value": NETWORK_LABEL},
                {"field_name": FIELD_PINATA_KEY, "field_type": "hidden", "value": pinata.key},
                {"field_name": FIELD_PINATA_SECRET, "field_type": "hidden", "value": pinata.secret},
                {"field_name": FIELD_CLIENT_ID, "field_type": "text", "value": client_id},
                {"field_name": FIELD_ROLE, "field_type": "text", "value": ROLE},
            ],
        }
    ]
    return item


def check_pinata(pinata: PinataKeys, timeout: float = 20) -> None:
    """Pinata's own key check; it uploads nothing. Raises on a rejected pair."""

    request = urllib.request.Request(
        PINATA_CHECK_URL,
        headers={"pinata_api_key": pinata.key, "pinata_secret_api_key": pinata.secret},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            return
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SiteError(
                "Pinata rejected these keys: check the API Key and the API Secret "
                "(not the JWT), and that the key is not revoked"
            ) from e
        raise SiteError(f"Pinata answered {e.code} to the key check") from e
    except urllib.error.URLError as e:
        raise SiteError(f"could not reach Pinata to check the keys: {e.reason}") from e


def create_site_key(
    passes: PassClient,
    vault: str,
    client_id: str,
    pinata: PinataKeys,
) -> tuple[str, str]:
    """Generate the site's key straight into Proton Pass. Returns (address, title)."""

    check_client_id(client_id)
    existing = find_site_titles(passes.titles(vault), client_id)
    if existing:
        raise SiteError(
            f"site '{client_id}' already has a key: {', '.join(existing)}. "
            "A second key would leave two addresses for one site; use the existing one"
        )

    mnemonic = Keypair.generate_mnemonic()
    REDACT.add(mnemonic, pinata.key, pinata.secret)
    address = Keypair.create_from_mnemonic(mnemonic).ss58_address
    title = site_title(client_id, address)
    passes.create_custom(vault, site_item(passes.custom_template(), client_id, address, mnemonic, pinata))
    del mnemonic

    # Read back and derive again: an item that exists but holds a mangled seed
    # would only surface when the site fails to publish.
    try:
        stored = passes.field(vault, title, FIELD_SEED)
    except PassError as e:
        raise SiteError(f"the item was created but cannot be read back: {e}") from e
    REDACT.add(stored)
    derived = Keypair.create_from_secret(stored).ss58_address
    if derived != address:
        raise SiteError(f"the stored seed derives {derived}, not {address}; do not use this item")
    return address, title


def read_site(passes: PassClient, vault: str, client_id: str) -> SiteSecrets:
    """The site's item, checked: seed, address and title must agree."""

    check_client_id(client_id)
    titles = find_site_titles(passes.titles(vault), client_id)
    if not titles:
        raise SiteError(
            f"no key for site '{client_id}' in vault '{vault}': the integrator creates it "
            f"with `rrs-admin new-site-key {client_id}`"
        )
    if len(titles) > 1:
        raise SiteError(f"site '{client_id}' has several keys: {', '.join(titles)}")
    title = titles[0]
    seed = passes.field(vault, title, FIELD_SEED)
    pinata = PinataKeys(
        passes.field(vault, title, FIELD_PINATA_KEY),
        passes.field(vault, title, FIELD_PINATA_SECRET),
    )
    REDACT.add(seed, pinata.key, pinata.secret)
    if not seed:
        raise SiteError(f"'{title}' has no '{FIELD_SEED}'")
    if not pinata.key or not pinata.secret:
        raise SiteError(f"'{title}' lacks the site's Pinata keys ('{FIELD_PINATA_KEY}', '{FIELD_PINATA_SECRET}')")

    address = Keypair.create_from_secret(seed).ss58_address
    stored_address = passes.field(vault, title, FIELD_ADDRESS)
    title_address = title.rsplit(" - ", 1)[1].strip()
    if not (address == stored_address == title_address):
        raise SiteError(
            f"'{title}' does not agree with itself: the seed derives {address}, the "
            f"'{FIELD_ADDRESS}' field says {stored_address or 'nothing'}. Stop and check the item"
        )
    return SiteSecrets(client_id, title, address, seed, pinata)


def pinata_from_item(passes: PassClient, vault: str, title: str) -> PinataKeys:
    """Pinata keys the integrator already put into another item (e.g. a draft one)."""

    pinata = PinataKeys(
        passes.field(vault, title, FIELD_PINATA_KEY),
        passes.field(vault, title, FIELD_PINATA_SECRET),
    )
    REDACT.add(pinata.key, pinata.secret)
    if not pinata.key or not pinata.secret:
        raise SiteError(f"'{title}' has no '{FIELD_PINATA_KEY}' / '{FIELD_PINATA_SECRET}'")
    return pinata

