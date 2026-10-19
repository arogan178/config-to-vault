"""Turn a config file (or a Spring Cloud Config response) into a flat key/value map."""

import json
import re
from pathlib import Path

import yaml

STRIPPED_PREFIXES = ("spring.profile", "spring.config.activate")
_PROFILE_KEY = re.compile(r"^(spring\.config\.activate\.on-profile|spring\.profiles)(\[\d+\])?$")


def strip_spring_keys(data: dict) -> dict:
    return {k: v for k, v in data.items() if not k.startswith(STRIPPED_PREFIXES)}


def flatten(value, prefix: str = "") -> dict:
    """Flatten nested dicts and lists into dotted keys (a.b.c, a[0])."""
    out: dict = {}
    if isinstance(value, dict):
        for key, item in value.items():
            out.update(flatten(item, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            out.update(flatten(item, f"{prefix}[{index}]"))
    elif prefix:
        if value is None:
            value = ""
        elif not isinstance(value, str | int | float | bool):
            value = str(value)  # e.g. YAML dates
        out[prefix] = value
    return out


def _document_profiles(flat: dict) -> list[str] | None:
    """Profiles a document is restricted to, or None if it applies to every profile."""
    keys = [k for k in flat if _PROFILE_KEY.match(k)]
    if not keys:
        return None
    return [p.strip() for k in keys for p in str(flat[k]).split(",") if p.strip()]


def resolve_yaml(text: str, profile: str) -> dict:
    """Spring Boot style: profile-less documents first, then documents activated for `profile`."""
    base: dict = {}
    matched: dict = {}
    for document in yaml.safe_load_all(text):
        flat = flatten(document)
        profiles = _document_profiles(flat)
        if profiles is None:
            base.update(flat)
        elif profile in profiles:
            matched.update(flat)
    return strip_spring_keys({**base, **matched})


def parse_properties(text: str) -> dict:
    """Parse a .properties file (comments, = : or whitespace separators, backslash continuations)."""
    props: dict = {}
    lines = iter(text.splitlines())
    for line in lines:
        line = line.lstrip()
        if not line or line[0] in "#!":
            continue
        while line.endswith("\\") and not line.endswith("\\\\"):
            line = line[:-1] + next(lines, "").lstrip()
        key, *rest = re.split(r"\s*[=:]\s*|\s+", line, maxsplit=1)
        props[key] = rest[0].strip() if rest else ""
    return props


def resolve_file(path: Path, profile: str) -> dict:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix in (".yml", ".yaml"):
        return resolve_yaml(text, profile)
    if suffix == ".properties":
        return strip_spring_keys(parse_properties(text))
    if suffix == ".json":
        return strip_spring_keys(flatten(json.loads(text)))
    raise ValueError(f"Unsupported file type: {path.name}")


def merge_property_sources(content: str) -> dict:
    """Merge a Config Server response: base sources first, then profile-specific ones."""
    payload = json.loads(content)
    property_sources = payload.get("propertySources", [])
    if not isinstance(property_sources, list):
        return {}

    base_sources: list[dict] = []
    profile_sources: list[dict] = []
    for item in property_sources:
        source = item.get("source", {}) if isinstance(item, dict) else {}
        if not isinstance(source, dict):
            continue
        if any(key.startswith(STRIPPED_PREFIXES) for key in source):
            profile_sources.append(source)
        else:
            base_sources.append(source)

    merged: dict = {}
    for source in base_sources + profile_sources:
        merged.update(source)
    return strip_spring_keys(merged)
