"""Path templating and diffing between desired and existing secrets."""

import re
import string

_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


def vault_path(template: str, base: str, profile: str, name: str) -> str:
    fields = {field for _, field, _, _ in string.Formatter().parse(template) if field is not None}
    if "name" not in fields or not fields <= {"base", "profile", "name"}:
        raise ValueError("--vault-path-template must contain {name} and only {base}, {profile} and {name}")
    if not base.strip("/"):
        raise ValueError("--vault-base must not be empty")
    for label, value in (("profile", profile), ("name", name)):
        if not _SAFE.match(value) or value in (".", ".."):
            raise ValueError(f"Invalid {label} for a Vault path: {value!r}")
    path = template.format(base=base.strip("/"), profile=profile, name=name).strip("/")
    segments = path.split("/")
    # Needs a mount plus a secret name; empty or dot segments would address a different path.
    if len(segments) < 2 or any(s in ("", ".", "..") for s in segments):
        raise ValueError(f"Invalid Vault path: {path!r}")
    return path


def prune_dir(template: str, base: str, profile: str) -> str:
    """Folder that holds all secrets produced by the template."""
    if not template.endswith("/{name}") or template.count("{name}") != 1:
        raise ValueError("--prune requires --vault-path-template to end with /{name} and use it once")
    return vault_path(template, base, profile, "x").rsplit("/", 1)[0]


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
