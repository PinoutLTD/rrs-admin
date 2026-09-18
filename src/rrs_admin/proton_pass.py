"""Proton Pass through pass-cli: reading fields and creating items via stdin.

Secrets go to pass-cli through stdin only, never in its arguments, where any
process of the same user could read them. That is also why this tool never
*updates* an item with a secret: `pass-cli item update` takes values as
arguments. A new key always gets a new item.
"""

import json
import os
import subprocess
from dataclasses import dataclass


class PassError(RuntimeError):
    pass


def run_pass_cli(
    arguments: list[str], reason: str, stdin: str | None = None
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    # Required when pass-cli runs under an agent token; recorded in the audit.
    env.setdefault("PROTON_PASS_AGENT_REASON", reason)
    try:
        return subprocess.run(
            ["pass-cli", *arguments],
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    except FileNotFoundError as e:
        raise PassError("pass-cli is not installed") from e


@dataclass(frozen=True)
class PassClient:
    reason: str

    def titles(self, vault: str) -> list[str]:
        result = run_pass_cli(["item", "list", vault, "--output", "json"], self.reason)
        if result.returncode != 0:
            raise PassError(
                f"cannot list vault '{vault}' (exit {result.returncode}); "
                "is pass-cli logged in?"
            )
        data = json.loads(result.stdout)
        items = data.get("items", data) if isinstance(data, dict) else data
        return [item.get("title", "") for item in items if isinstance(item, dict)]

    def field(self, vault: str, title: str, name: str) -> str:
        result = run_pass_cli(
            ["item", "view", "--vault-name", vault, "--item-title", title, "--field", name],
            self.reason,
        )
        if result.returncode != 0:
            raise PassError(f"cannot read field '{name}' of '{title}' in vault '{vault}'")
        return result.stdout.strip()

    def custom_template(self) -> dict:
        result = run_pass_cli(["item", "create", "custom", "--get-template"], self.reason)
        if result.returncode != 0:
            raise PassError(
                f"cannot get the custom item template (exit {result.returncode}); "
                "is pass-cli logged in?"
            )
        template = json.loads(result.stdout)
        if not isinstance(template, dict) or "title" not in template:
            raise PassError("pass-cli returned an unexpected custom item template")
        return template

    def create_custom(self, vault: str, item: dict) -> None:
        created = run_pass_cli(
            ["item", "create", "custom", "--vault-name", vault, "--from-template", "-"],
            self.reason,
            stdin=json.dumps(item),
        )
        if created.returncode != 0:
            # stderr of pass-cli describes the request, not its values.
            raise PassError(
                f"pass-cli could not create the item (exit {created.returncode}): "
                f"{created.stderr.strip()[:300]}"
            )
