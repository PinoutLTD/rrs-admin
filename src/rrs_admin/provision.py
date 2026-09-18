"""Set up the Report Service integration on a site's Home Assistant.

Everything secret comes from Proton Pass and goes straight to Home Assistant:
the site's seed and Pinata keys from the site's item, the HA token from the
item Fotis's registry names. Whoever runs this — a person or an agent — sees
the site's address and the outcome, never a secret.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from rrs_admin.ha import HaError, HomeAssistant, Step
from rrs_admin.sites import SiteSecrets

DOMAIN = "robonomics_report_service"
FORM = "form"
CREATED = "create_entry"
ABORT = "abort"

# Field names of the integration's config flow (rrs-ha-integration const.py).
F_NETWORK = "network"
F_RECIPIENT = "problem_service_robonomics_address"
F_PINATA_PUBLIC = "pinata_public"
F_PINATA_SECRET = "pinata_secret"
F_EMAIL = "sender_email"
F_OWNER = "subscription_owner_robonomics_address"
F_SEED = "sender_seed"

FLOW_ERRORS = {
    "invalid_pinata_keys": "Pinata rejected the site's keys (checked by Home Assistant)",
    "pinata_unavailable": "Home Assistant could not reach Pinata to check the keys",
    "invalid_seed": "the integration refused the seed",
    "storage_save_failed": "the integration could not save its credentials",
    "seed_generation_failed": "the integration could not prepare the seed step",
    "invalid_address": "an address was refused",
}


class ProvisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Plan:
    site: SiteSecrets
    network: str
    recipient: str
    pool: str
    email: str | None

    def user_step(self) -> dict:
        data = {
            F_NETWORK: self.network,
            F_RECIPIENT: self.recipient,
            F_PINATA_PUBLIC: self.site.pinata.key,
            F_PINATA_SECRET: self.site.pinata.secret,
            F_OWNER: self.pool,
        }
        if self.email:
            data[F_EMAIL] = self.email
        return data

    def describe(self) -> list[str]:
        return [
            f"site:        {self.site.client_id}",
            f"address:     {self.site.address}",
            f"network:     {self.network}",
            f"recipient:   {self.recipient}",
            f"pool:        {self.pool}",
            f"e-mail:      {self.email or '—'}",
            f"secrets:     seed and Pinata keys from '{self.site.title}'",
        ]


def _explain(step: Step) -> str:
    if step.type == ABORT:
        return f"the flow was aborted: {step.reason}"
    if step.errors:
        return "; ".join(FLOW_ERRORS.get(v, f"{k}: {v}") for k, v in step.errors.items())
    return f"unexpected step {step.type}/{step.step_id}"


def provision(
    ha: HomeAssistant,
    plan: Plan,
    replace: bool = False,
    say: Callable[[str], None] = print,
    wait_seconds: float = 30,
) -> str:
    """Run the integration's config flow; returns the new entry id."""

    existing = ha.entries(DOMAIN)
    if existing and not replace:
        listed = ", ".join(f"{e['entry_id']} ({e['state']})" for e in existing)
        raise ProvisionError(
            f"the integration is already set up here: {listed}. Removing it deletes its "
            "stored seed; if that is intended, run again with --replace"
        )
    for entry in existing:
        say(f"Removing the existing entry {entry['entry_id']} ({entry['title']}, {entry['state']})")
        ha.delete_entry(entry["entry_id"])

    step = ha.start_flow(DOMAIN)
    if step.type != FORM or step.step_id != "user":
        raise ProvisionError(f"Home Assistant did not open the setup form: {_explain(step)}")
    flow_id = step.flow_id
    try:
        say("Step 1/2: addresses and Pinata keys")
        step = ha.submit(flow_id, plan.user_step())
        if step.type != FORM or step.step_id != "seed" or step.errors:
            raise ProvisionError(f"step 1 failed: {_explain(step)}")

        # The seed step shows a seed the integration generated; it is not read.
        say("Step 2/2: the site's seed from Proton Pass")
        step = ha.submit(flow_id, {F_SEED: plan.site.seed})
        if step.type != CREATED or not step.entry_id:
            raise ProvisionError(f"step 2 failed: {_explain(step)}")
    except (ProvisionError, HaError):
        ha.abort_flow(flow_id)
        raise

    entry_id = step.entry_id
    deadline = time.monotonic() + wait_seconds
    state = "unknown"
    while time.monotonic() < deadline:
        state = next((e["state"] for e in ha.entries(DOMAIN) if e["entry_id"] == entry_id), "missing")
        if state in ("loaded", "setup_error", "setup_retry", "missing"):
            break
        time.sleep(2)
    if state != "loaded":
        raise ProvisionError(
            f"entry {entry_id} was created but is '{state}': check the Home Assistant log "
            "for robonomics_report_service"
        )
    return entry_id
