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
        if not value and prefix:
            out[prefix] = ""  # as Spring does for an empty list
        for index, item in enumerate(value):
            out.update(flatten(item, f"{prefix}[{index}]"))
    elif prefix:
        if value is None:
            value = ""
        elif not isinstance(value, str | int | float | bool):
            value = str(value)  # e.g. YAML dates
        out[prefix] = value
    return out


def merge_flat(base: dict, override: dict) -> dict:
    """Overlay flat maps; a list set in `override` replaces the whole list, as Spring's binder does."""
    roots = {k.split("[", 1)[0] for k in override}
    kept = {k: v for k, v in base.items() if "[" not in k or k.split("[", 1)[0] not in roots}
    return {**kept, **override}


def _document_profiles(flat: dict) -> list[str] | None:
    """Profile expressions a document is restricted to, or None if it applies to every profile."""
    keys = [k for k in flat if _PROFILE_KEY.match(k)]
    if not keys:
        return None
    return [p.strip() for k in keys for p in str(flat[k]).split(",") if p.strip()]


def profile_matches(expression: str, profile: str) -> bool:
    """Evaluate a Spring profile expression (!, &, |, parentheses) for a single active profile."""
    tokens = re.findall(r"[()&|!]|[^()&|!\s]+", expression)
    pos = 0

    def fail():
        raise ValueError(f"Malformed profile expression: {expression!r}")

    def term() -> bool:
        nonlocal pos
        if pos >= len(tokens) or tokens[pos] in ("&", "|", ")"):
            fail()
        token = tokens[pos]
        pos += 1
        if token == "!":
            return not term()
        if token == "(":
            value = expr()
            if pos >= len(tokens) or tokens[pos] != ")":
                fail()
            pos += 1
            return value
        return token == profile

    def expr() -> bool:
        nonlocal pos
        values, ops = [term()], set()
        while pos < len(tokens) and tokens[pos] in ("&", "|"):
            ops.add(tokens[pos])
            pos += 1
            values.append(term())
        if len(ops) > 1:
            fail()  # Spring rejects mixing & and | without parentheses
        return all(values) if ops == {"&"} else any(values)

    value = expr()
    if pos != len(tokens):
        fail()
    return value


def resolve_documents(documents: list[dict], profile: str) -> dict:
    """Spring Boot 2.4+ style: active documents are applied in file order, later ones win."""
    merged: dict = {}
    for flat in documents:
        profiles = _document_profiles(flat)
        if profiles is None or any(profile_matches(p, profile) for p in profiles):
            merged = merge_flat(merged, flat)
    return strip_spring_keys(merged)


def resolve_yaml(text: str, profile: str) -> dict:
    return resolve_documents([flatten(document) for document in yaml.safe_load_all(text)], profile)


_PROPERTIES_ESCAPES = {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}


def _unescape(text: str) -> str:
    def replace(match: re.Match) -> str:
        char = match.group(1)
        return chr(int(char[1:], 16)) if len(char) == 5 else _PROPERTIES_ESCAPES.get(char, char)

    return re.sub(r"\\(u[0-9a-fA-F]{4}|.)", replace, text, flags=re.S)


def parse_properties(text: str) -> dict:
    """Parse a .properties file with java.util.Properties rules (separators, escapes, continuations)."""
    props: dict = {}
    lines = iter(re.split(r"\r\n|\r|\n", text))
    for line in lines:
        line = line.lstrip(" \t\f")
        if not line or line[0] in "#!":
            continue
        while (len(line) - len(line.rstrip("\\"))) % 2:  # odd trailing backslashes continue the line
            line = line[:-1] + next(lines, "").lstrip(" \t\f")
        match = re.match(r"((?:\\.|[^\\=:\s])*)\s*[=:]?\s*(.*)", line, re.S)
        props[_unescape(match.group(1))] = _unescape(match.group(2))
    return props


def resolve_properties(text: str, profile: str) -> dict:
    """Multi-document .properties files are split on #--- or !--- lines, like Spring Boot 2.4+."""
    documents = re.split(r"^[#!]---[ \t]*$", text, flags=re.M)
    return resolve_documents([parse_properties(document) for document in documents], profile)


def resolve_file(path: Path, profile: str) -> dict:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix in (".yml", ".yaml"):
        return resolve_yaml(text, profile)
    if suffix == ".properties":
        return resolve_properties(text, profile)
    if suffix == ".json":
        return strip_spring_keys(flatten(json.loads(text)))
    raise ValueError(f"Unsupported file type: {path.name}")


def merge_property_sources(content: str) -> dict:
    """Merge a Config Server response, whose propertySources are listed highest precedence first."""
    payload = json.loads(content)
    property_sources = payload.get("propertySources") if isinstance(payload, dict) else None
    # An empty answer (unknown name, wrong label) must not wipe the existing secret.
    if not isinstance(property_sources, list) or not property_sources:
        raise ValueError("Config Server returned no property sources")

    merged: dict = {}
    for item in reversed(property_sources):
        source = item.get("source", {}) if isinstance(item, dict) else {}
        if isinstance(source, dict):
            merged = merge_flat(merged, source)
    return strip_spring_keys(merged)
