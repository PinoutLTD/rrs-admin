# rrs-admin

Site keys and site setup for Pinout Report Service, for the people who run it.

The Report Service integration publishes a site's reports from the site's own
Robonomics account. Its setup form shows a freshly generated seed once and moves
on. A person copies it; an automated installer does not, and the seed is lost.
`rrs-admin` turns this around: **the site key is born in Proton Pass**, before the
installation, and goes from there straight into Home Assistant. Whoever installs
the integration — a person or an agent — sees the site's address, never a secret.

## Roles

| Step | Who | Command |
| --- | --- | --- |
| Generate the site key and issue the site's Pinata key | integrator | `rrs-admin new-site-key <client_id>` |
| Add the address to a pool | integrator | `rrs-admin pool-add <address>` |
| Send the existential deposit, register the site in the connector | integrator | printed by `new-site-key` |
| Set up the integration on the site's Home Assistant | engineer | `rrs-admin provision-site <client_id>` |

`client_id` is the site's slug, the same as its folder in `fotis-agent/clients/`.

## Commands

```bash
uv run rrs-admin new-site-key oscar-home                      # issues the site's Pinata key itself
uv run rrs-admin new-site-key oscar-home --pinata-manual      # or: type an existing pair, hidden
uv run rrs-admin new-site-key oscar-home --pinata-from "draft item title"
uv run rrs-admin pinata-keys oscar-home                       # the site's Pinata keys
uv run rrs-admin pinata-keys oscar-home --revoke              # revoke them
uv run rrs-admin pinata-keys --key <name or id> --revoke     # revoke one key, e.g. made by hand
uv run rrs-admin site-info oscar-home                         # what the item holds, no secrets
uv run rrs-admin provision-site oscar-home --dry-run          # check everything, change nothing
uv run rrs-admin provision-site oscar-home                    # set it up
uv run rrs-admin provision-site oscar-home --replace          # remove an existing entry first
```

`new-site-key` issues the site's Pinata key, generates an ED25519 key, creates the
item `rrs-site <client_id> - <address>` in the vault `Report Service` through
`pass-cli` stdin, reads it back and derives the address again. A site that already
has a key is refused. If the item cannot be created, the Pinata key just issued is
revoked.

### Pinata keys

A site's Pinata key is issued through Pinata's API, named `rrs-site <client_id>`,
with exactly the permissions the integration uses: `pinFileToIPFS` and `unpin`.
Leaked from a client's machine, it cannot list the account's files or issue keys
(checked live on 2026-09-18: `testAuthentication` passes, uploading is allowed,
`pinList` answers 403). The JWT Pinata returns with the pair is dropped unread.

Keys are issued by the *issuer*: an account key with `org:write`, whose JWT sits in
Proton Pass, vault `Robonomics Pools`, item `rrs-pinata-issuer`, field `JWT` — the
human-only vault, next to the pool keys.

`provision-site` finds the site's Home Assistant in Fotis's registry (host, port,
the Proton Pass item with `ha_token`), reads the site's item, checks that its seed,
`Address` field and title agree, then walks the integration's config flow step by
step over Home Assistant's REST API: the addresses and Pinata keys, then the seed.
It waits until the entry is loaded.

## Pools

A site publishes through an RWS subscription — a pool. `RWS.set_devices` **replaces the
whole device list**, so adding one site means reading the current list, changing it and
writing all of it back; an error there silently drops other sites out of the subscription.
These commands do that read-modify-write, and refuse anything that would lose a site:

```bash
uv run rrs-admin pool                            # subscription, expiry, devices by site
uv run rrs-admin pool-add <address>              # dry run
uv run rrs-admin pool-add <address> --send       # sign with the pool key and write
uv run rrs-admin pool-remove <address> [--send]
```

What is checked before anything is signed:

- the new list is built from the list the chain holds now, never from assumptions;
- an address already in the list, the pool's own address, and anything past the limit of
  32 devices are refused;
- the composed call is **decoded back** and its addresses compared with the plan;
- the seed in Proton Pass must derive the pool's own address;
- after the write the subscription is read again and compared with what was written.

Device lists read as sites: the addresses are matched against the `rrs-site …` items in
Proton Pass. An added account that does not exist on chain yet is called out — until it
receives the existential deposit, its reports are refused with
`InvalidTransaction::Payment`. Sending that deposit is a person's job, not this tool's.

Pools live in `config/rrs-admin.toml`; the pool's seed stays in Proton Pass, in the
human-only vault.

## How secrets are handled

- Everything secret is read from Proton Pass into memory and sent on; nothing is
  written to disk, printed or passed as a command-line argument.
- Items are **created** through stdin and never updated with a secret:
  `pass-cli item update` takes values as arguments, visible to other processes.
- The flow is walked one step at a time and only the step's type, id, errors and
  entry id are kept. Home Assistant puts what a step *shows* — including a seed the
  integration generates — into `description_placeholders`; that is dropped unread.
- Every line printed goes through a redactor that masks any secret the tool has
  seen, including in error messages that echo a server response.
- Home Assistant on a LAN is usually plain HTTP. The seed then crosses the site's
  network exactly as it would from a browser on the same URL; use a trusted
  network or the site's VPN.

## Setup

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/), and a logged-in
`pass-cli` (a personal session, not a server token).

```bash
cp config/rrs-admin.example.toml config/rrs-admin.toml   # public addresses only
uv run rrs-admin --help
```

`config/rrs-admin.toml` holds the current recipient and pool addresses and the
path to Fotis's registry. It is not committed.

## The chain library

Keys, addresses and the chain all come from
[robonomics-interface](https://github.com/airalab/robonomics-interface) 3, the same
library the connector and the integration use, so a key made here is derived exactly as
the integration derives it. The tests pin this with the integration's vectors
(`tests/fixtures/accounts.json`).

`src/rrs_admin/rws.py` is the only module that talks to the chain. A command opens one
connection and does all its reads and its write through it. `set_devices` takes the
device list flat, and fails when the call failed inside the block, not only when the
node refused it. After a write, the list is read back from the chain and compared with
the plan that was shown.
