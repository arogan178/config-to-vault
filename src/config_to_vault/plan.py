"""Path templating and diffing between desired and existing secrets."""

import re

_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


def vault_path(template: str, base: str, profile: str, name: str) -> str:
    for label, value in (("profile", profile), ("name", name)):
        if not _SAFE.match(value) or value in (".", ".."):
            raise ValueError(f"Invalid {label} for a Vault path: {value!r}")
    return template.format(base=base.strip("/"), profile=profile, name=name).strip("/")


def prune_dir(template: str, base: str, profile: str) -> str:
    """Folder that holds all secrets produced by the template."""
    if not template.endswith("/{name}"):
        raise ValueError("--prune requires --vault-path-template to end with /{name}")
    return vault_path(template[: -len("/{name}")] + "/x", base, profile, "x").rsplit("/", 1)[0]


def _same(a, b) -> bool:
    return type(a) is type(b) and a == b


def diff_keys(existing: dict, desired: dict) -> list[str]:
    """Sorted keys that are added, removed or changed."""
    keys = existing.keys() | desired.keys()
    return sorted(k for k in keys if k not in existing or k not in desired or not _same(existing[k], desired[k]))


def build_plan(existing: dict | None, desired: dict) -> tuple[str, list[str]]:
    """Return (action, changed_keys) where action is created, updated or unchanged."""
    if existing is None:
        return "created", sorted(desired)
    changed = diff_keys(existing, desired)
    return ("updated", changed) if changed else ("unchanged", [])
