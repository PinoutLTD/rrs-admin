"""rrs-admin: site keys and site setup for Pinout Report Service.

  new-site-key <client_id>     integrator: generate the site's key into Proton Pass
  site-info <client_id>        anyone: what the site's item holds (no secrets shown)
  provision-site <client_id>   engineer: set up the integration on the site's HA
"""

import argparse
import getpass
import sys
from pathlib import Path

from rrs_admin.config import ConfigError, config_path, load_config
from rrs_admin.ha import HaError, HomeAssistant
from rrs_admin.proton_pass import PassClient, PassError
from rrs_admin.provision import Plan, ProvisionError, provision
from rrs_admin.redact import REDACT
from rrs_admin.registry import TOKEN_FIELD, RegistryError, find_ha_site
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


def cmd_new_site_key(config, args) -> int:
    passes = PassClient(REASON_KEY)
    client_id = check_client_id(args.client_id)
    pinata = (
        pinata_from_item(passes, config.sites_vault, args.pinata_from)
        if args.pinata_from
        else ask_pinata()
    )
    if not args.skip_pinata_check:
        check_pinata(pinata)
        say("Pinata accepted the keys.")
    address, title = create_site_key(passes, config.sites_vault, client_id, pinata)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rrs-admin", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, help="default: config/rrs-admin.toml or $RRS_ADMIN_CONFIG")
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new-site-key", help="generate a site key into Proton Pass")
    new.add_argument("client_id")
    new.add_argument("--pinata-from", metavar="ITEM", help="take Pinata keys from this item (same vault)")
    new.add_argument("--skip-pinata-check", action="store_true")

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
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(config_path(args.config))
        return COMMANDS[args.command](config, args)
    except (ConfigError, PassError, SiteError, RegistryError, HaError, ProvisionError) as e:
        print(f"rrs-admin: {REDACT(e)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
