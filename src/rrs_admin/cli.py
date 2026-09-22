"""rrs-admin: site keys and site setup for Pinout Report Service.

  new-site-key <client_id>     integrator: the site's key and Pinata keys into Proton Pass
  site-info <client_id>        anyone: what the site's item holds (no secrets shown)
  provision-site <client_id>   engineer: set up the integration on the site's HA
  pinata-keys <client_id>      integrator: list, and with --revoke revoke, a site's Pinata keys
  pool <name>                  integrator: a pool's subscription and its devices
  pool-add <address>           integrator: add a device to a pool (dry run by default)
  pool-remove <address>        integrator: remove a device from a pool
"""

import argparse
import getpass
import sys
from pathlib import Path

from rrs_admin.config import ConfigError, config_path, load_config
from rrs_admin.ha import HaError, HomeAssistant
from rrs_admin.pinata import PinataError, PinataIssuer, key_name, wait_until_accepted
from rrs_admin.pools import MAX_DEVICES, PoolError, plan_add, plan_remove
from rrs_admin.proton_pass import PassClient, PassError
from rrs_admin.provision import Plan, ProvisionError, provision
from rrs_admin.redact import REDACT
from rrs_admin.registry import TOKEN_FIELD, RegistryError, find_ha_site
from rrs_admin.rws import (
    ChainError,
    account_exists,
    pool_address_of,
    read_subscription,
    set_devices,
)
from rrs_admin.sites import (
    FIELD_ADDRESS,
    PinataKeys,
    SiteError,
    check_client_id,
    check_pinata,
    create_site_key,
    find_site_titles,
    pinata_from_item,
    read_site,
)

REASON_KEY = "rrs-admin: create a site key for Report Service"
REASON_SETUP = "rrs-admin: set up Report Service on a site"
REASON_PINATA = "rrs-admin: manage Pinata keys of Report Service sites"
REASON_POOL = "rrs-admin: manage devices of a Report Service subscription pool"
DEFAULT_POOL = "pool-01"


def say(text: str) -> None:
    print(REDACT(text))


def ask_pinata() -> PinataKeys:
    if not sys.stdin.isatty():
        raise SiteError(
            "no Pinata keys: pass --pinata-from <item title>, or run this in a terminal "
            "to type them (input is hidden)"
        )
    key = getpass.getpass("Pinata API Key (hidden): ").strip()
    secret = getpass.getpass("Pinata API Secret (hidden, not the JWT): ").strip()
    REDACT.add(key, secret)
    return PinataKeys(key, secret)


def issuer(config, passes: PassClient) -> PinataIssuer:
    jwt = passes.field(config.issuer_vault, config.issuer_item, "JWT")
    if not jwt:
        raise PassError(f"'{config.issuer_item}' in '{config.issuer_vault}' has no JWT")
    return PinataIssuer(jwt)


def cmd_new_site_key(config, args) -> int:
    passes = PassClient(REASON_KEY)
    client_id = check_client_id(args.client_id)
    existing = find_site_titles(passes.titles(config.sites_vault), client_id)
    if existing:
        raise SiteError(f"site '{client_id}' already has a key: {', '.join(existing)}")

    issued_by = None
    if args.pinata_from:
        pinata = pinata_from_item(passes, config.sites_vault, args.pinata_from)
    elif args.pinata_manual:
        pinata = ask_pinata()
    else:
        issued_by = issuer(config, passes)
        pinata = issued_by.issue_site_key(client_id)
        say(f"Pinata key '{key_name(client_id)}' issued: upload and unpin only.")
    if issued_by:
        wait_until_accepted(pinata)
    elif not args.skip_pinata_check:
        check_pinata(pinata)
    say("Pinata accepted the keys.")

    try:
        address, title = create_site_key(passes, config.sites_vault, client_id, pinata)
    except Exception:
        if issued_by:
            # A key nobody holds any more is only a liability.
            issued_by.revoke(pinata.key)
            say("Setting up the site item failed; the Pinata key just issued is revoked.")
        raise
    say(f"Site key created: {title}")
    say(f"Address: {address}")
    say("")
    say("Next, for the integrator:")
    say(f"  1. add {address} to the devices of {config.pool} (set_devices replaces the whole list)")
    say("  2. send it the existential deposit, 0.000001 XRT")
    say(f"  3. add {client_id} with this address to the connector's senders.yaml")
    say(f"Then the engineer runs: rrs-admin provision-site {client_id}")
    if args.pinata_from:
        say(f"The draft item '{args.pinata_from}' is no longer needed; delete it in Proton Pass.")
    return 0


def cmd_pinata_keys(config, args) -> int:
    passes = PassClient(REASON_PINATA)
    pinata = issuer(config, passes)
    if args.key:
        # Accept what a person sees in Pinata's web app: the key's name, or its id.
        keys = [k for k in pinata.list_keys() if args.key in (k.id, k.name)]
        if not keys:
            say(f"No active key with the id or name '{args.key}'.")
            return 1
    else:
        keys = pinata.list_keys(name=key_name(check_client_id(args.client_id)))
        if not keys:
            say(f"No active Pinata keys named '{key_name(args.client_id)}'.")
            return 0
    for key in keys:
        scope = "admin" if key.scopes.get("admin") else "scoped"
        say(f"{key.id}  {key.name}  created {key.created}  {scope}")
    if not args.revoke:
        say("Listed only. Add --revoke to revoke them; a revoked key stops working at once.")
        return 0
    for key in keys:
        pinata.revoke(key.id)
        say(f"Revoked {key.id}")
    return 0


def cmd_site_info(config, args) -> int:
    passes = PassClient(REASON_SETUP)
    client_id = check_client_id(args.client_id)
    titles = find_site_titles(passes.titles(config.sites_vault), client_id)
    if not titles:
        say(f"No key for '{client_id}' in vault '{config.sites_vault}'.")
        return 1
    for title in titles:
        say(f"{title}: address {passes.field(config.sites_vault, title, FIELD_ADDRESS) or '—'}")
    site = read_site(passes, config.sites_vault, client_id)
    say(f"Checked: seed derives {site.address}, Pinata keys present.")
    return 0


def cmd_provision_site(config, args) -> int:
    passes = PassClient(REASON_SETUP)
    client_id = check_client_id(args.client_id)
    site = read_site(passes, config.sites_vault, client_id)

    if args.ha_url and args.token_item:
        ha_url, token_vault, token_item = args.ha_url, args.token_vault, args.token_item
    else:
        ha_site = find_ha_site(config.fotis_registry, client_id, remote=args.remote)
        ha_url = args.ha_url or ha_site.url
        token_vault, token_item = ha_site.pass_vault, ha_site.pass_item
    token = passes.field(token_vault, token_item, TOKEN_FIELD)
    if not token:
        raise PassError(f"'{token_item}' in '{token_vault}' has no '{TOKEN_FIELD}'")
    REDACT.add(token)

    plan = Plan(
        site=site,
        network=args.network or config.network,
        recipient=args.recipient or config.recipient,
        pool=args.pool or config.pool,
        email=args.email,
    )
    ha = HomeAssistant(ha_url, token)
    say(f"Home Assistant {ha.check()} at {ha_url}")
    if ha_url.startswith("http://"):
        say("Note: plain HTTP. The seed crosses the site's network the same way it would")
        say("      from a browser on the same URL; prefer a trusted network or VPN.")
    for line in plan.describe():
        say(line)
    if args.dry_run:
        existing = ha.entries("robonomics_report_service")
        say(f"Existing entries: {existing or 'none'}")
        say("Dry run: nothing was changed.")
        return 0

    entry_id = provision(ha, plan, replace=args.replace, say=say)
    say(f"Set up: entry {entry_id}, loaded.")
    say(f"Site address: {site.address}")
    say("Until the integrator confirms the pool and the deposit, sending may fail with")
    say("InvalidTransaction::Payment; that is expected.")
    return 0




def pool_settings(config, name: str) -> dict:
    pool = config.pools.get(name)
    if not pool:
        known = ", ".join(sorted(config.pools)) or "нет ни одного"
        raise PoolError(f"пул '{name}' не описан в конфиге (есть: {known})")
    if not pool.get("address"):
        raise PoolError(f"у пула '{name}' в конфиге нет адреса")
    return pool


def site_labels(passes: PassClient, vault: str) -> dict[str, str]:
    """Address → site, so a device list reads as sites and not as hashes."""

    labels = {}
    for title in passes.titles(vault):
        if title.startswith("rrs-site ") and " - " in title:
            body = title[len("rrs-site "):]
            client_id, _, address = body.rpartition(" - ")
            if address.strip():
                labels[address.strip()] = client_id.strip()
    return labels


def show_pool(config, name: str, passes: PassClient) -> None:
    pool = pool_settings(config, name)
    subscription = read_subscription(config.chain_url, pool["address"])
    labels = site_labels(passes, config.sites_vault)
    say(f"{name}: {pool['address']}")
    for line in subscription.describe():
        say(f"  {line}")
    say(f"  devices:      {len(subscription.devices)} of {MAX_DEVICES}")
    for address in subscription.devices:
        say(f"    {address}  {labels.get(address, '— объект неизвестен')}")


def cmd_pool(config, args) -> int:
    show_pool(config, args.name, PassClient(REASON_POOL))
    return 0


def change_devices(config, args, make_plan) -> int:
    passes = PassClient(REASON_POOL)
    pool = pool_settings(config, args.pool)
    subscription = read_subscription(config.chain_url, pool["address"])
    plan = make_plan(pool["address"], list(subscription.devices), args.address)

    labels = site_labels(passes, config.sites_vault)
    for line in plan.describe():
        say(line)
    known = labels.get(args.address)
    say(f"объект:    {known}" if known else
        "объект:    неизвестен — в Proton Pass нет айтема rrs-site с этим адресом")
    if plan.added and not account_exists(config.chain_url, args.address):
        say("ВНИМАНИЕ: этого аккаунта нет в цепи. Пока на него не отправлен")
        say("          экзистенциальный депозит 0.000001 XRT, его отчёты будут")
        say("          отклоняться с InvalidTransaction::Payment.")
    if not args.send:
        say("Пробный прогон: ничего не записано. Для записи добавьте --send.")
        return 0

    seed = passes.field(
        pool.get("vault", "Robonomics Pools"),
        pool["item"],
        pool.get("seed_field", "Seed Phrase"),
    )
    if not seed:
        raise PoolError(f"в айтеме '{pool['item']}' нет сида пула")
    derived = pool_address_of(seed)
    if derived != pool["address"]:
        raise PoolError(
            f"сид из '{pool['item']}' даёт {derived}, а пул — {pool['address']}: "
            "подписывать нечем, проверьте айтем"
        )

    block = set_devices(config.chain_url, seed, list(plan.devices))
    say(f"Записано в блоке {block}")
    after = read_subscription(config.chain_url, pool["address"])
    if set(after.devices) != set(plan.devices):
        raise ChainError(
            "список устройств в цепи не совпал с тем, что мы записали — проверьте вручную"
        )
    say(f"Проверено: у пула {len(after.devices)} устройств, свободно "
        f"{MAX_DEVICES - len(after.devices)}.")
    if plan.added:
        say(f"Дальше: отправить 0.000001 XRT на {args.address} и завести объект в senders.yaml.")
    return 0


def cmd_pool_add(config, args) -> int:
    return change_devices(config, args, plan_add)


def cmd_pool_remove(config, args) -> int:
    return change_devices(config, args, plan_remove)



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rrs-admin", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, help="default: config/rrs-admin.toml or $RRS_ADMIN_CONFIG")
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new-site-key", help="generate a site key into Proton Pass")
    new.add_argument("client_id")
    source = new.add_mutually_exclusive_group()
    source.add_argument("--pinata-from", metavar="ITEM",
                        help="take Pinata keys from this item (same vault) instead of issuing")
    source.add_argument("--pinata-manual", action="store_true",
                        help="type Pinata keys (hidden) instead of issuing")
    new.add_argument("--skip-pinata-check", action="store_true",
                     help="with --pinata-from/--pinata-manual: do not ask Pinata")

    pool = sub.add_parser("pool", help="a pool's subscription and its devices")
    pool.add_argument("name", nargs="?", default=DEFAULT_POOL)

    for verb, help_text in (("pool-add", "add a device to a pool"),
                            ("pool-remove", "remove a device from a pool")):
        change = sub.add_parser(verb, help=f"{help_text} (dry run by default)")
        change.add_argument("address")
        change.add_argument("--pool", default=DEFAULT_POOL)
        change.add_argument("--send", action="store_true", help="really write the list")

    keys = sub.add_parser("pinata-keys", help="list or revoke a site's Pinata keys")
    keys.add_argument("client_id", nargs="?", default="-")
    keys.add_argument("--key", help="a key's id or exact name, e.g. one made by hand")
    keys.add_argument("--revoke", action="store_true", help="revoke what is listed")

    info = sub.add_parser("site-info", help="show and check the site's item, no secrets")
    info.add_argument("client_id")

    prov = sub.add_parser("provision-site", help="set up the integration on the site's HA")
    prov.add_argument("client_id")
    prov.add_argument("--remote", action="store_true", help="use remote_host from the registry")
    prov.add_argument("--ha-url", help="override the HA URL")
    prov.add_argument("--token-vault", default="Smart Home Agent")
    prov.add_argument("--token-item", help="with --ha-url: item holding ha_token")
    prov.add_argument("--recipient", help="override the recipient address")
    prov.add_argument("--pool", help="override the subscription owner (pool) address")
    prov.add_argument("--network", help="override the network (default from config)")
    prov.add_argument("--email", help="optional e-mail shown in reports")
    prov.add_argument("--replace", action="store_true",
                      help="remove an existing entry first (its stored seed is deleted)")
    prov.add_argument("--dry-run", action="store_true", help="check everything, change nothing")
    return parser


COMMANDS = {
    "new-site-key": cmd_new_site_key,
    "site-info": cmd_site_info,
    "provision-site": cmd_provision_site,
    "pinata-keys": cmd_pinata_keys,
    "pool": cmd_pool,
    "pool-add": cmd_pool_add,
    "pool-remove": cmd_pool_remove,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(config_path(args.config))
        return COMMANDS[args.command](config, args)
    except (ConfigError, PassError, SiteError, RegistryError, HaError, ProvisionError,
            PinataError, PoolError, ChainError) as e:
        print(f"rrs-admin: {REDACT(e)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
