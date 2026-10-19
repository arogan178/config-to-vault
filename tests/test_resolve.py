import json

import pytest

from config_to_vault.resolve import flatten, merge_property_sources, parse_properties, resolve_file, resolve_yaml

MULTI_DOC = """\
app:
  name: demo
  hosts: [a, b]
  debug: false
  port: 80
---
spring:
  config:
    activate:
      on-profile: prod
app:
  port: 443
---
spring.profiles: dev
app:
  port: 8080
---
spring:
  config:
    activate:
      on-profile: eu,us
app:
  region: west
"""


def test_flatten_nested_and_lists():
    assert flatten({"a": {"b": {"c": 1}}, "l": [{"x": "y"}, 2], "n": None}) == {
        "a.b.c": 1,
        "l[0].x": "y",
        "l[1]": 2,
        "n": "",
    }


def test_flatten_non_scalar_leaf_becomes_string():
    import datetime

    assert flatten({"d": datetime.date(2020, 1, 2)}) == {"d": "2020-01-02"}


def test_yaml_profile_overrides_base():
    result = resolve_yaml(MULTI_DOC, "prod")
    assert result == {"app.name": "demo", "app.hosts[0]": "a", "app.hosts[1]": "b", "app.debug": False, "app.port": 443}


def test_yaml_legacy_spring_profiles():
    assert resolve_yaml(MULTI_DOC, "dev")["app.port"] == 8080


def test_yaml_comma_separated_profiles_and_no_match():
    assert resolve_yaml(MULTI_DOC, "us")["app.region"] == "west"
    other = resolve_yaml(MULTI_DOC, "stage")
    assert other["app.port"] == 80
    assert "app.region" not in other


def test_yaml_strips_spring_keys():
    text = "spring:\n  profiles:\n    active: dev\nkeep: 1\n"
    assert resolve_yaml(text, "dev") == {"keep": 1}


def test_parse_properties():
    text = "# comment\n! other\na=1\nb : two words\nc three\nd=x=y\ne=\nlong=one\\\n  two\n\n"
    assert parse_properties(text) == {
        "a": "1",
        "b": "two words",
        "c": "three",
        "d": "x=y",
        "e": "",
        "long": "onetwo",
    }


def test_resolve_file_by_type(tmp_path):
    (tmp_path / "a.properties").write_text("k=v\nspring.profiles.active=dev\n")
    (tmp_path / "b.json").write_text(json.dumps({"x": {"y": [1]}}))
    assert resolve_file(tmp_path / "a.properties", "dev") == {"k": "v"}
    assert resolve_file(tmp_path / "b.json", "dev") == {"x.y[0]": 1}
    (tmp_path / "c.txt").write_text("")
    with pytest.raises(ValueError):
        resolve_file(tmp_path / "c.txt", "dev")


def test_config_server_merge_precedence():
    content = json.dumps(
        {
            "propertySources": [
                {"source": {"a": "profile", "spring.profiles": "dev", "only.profile": 1}},
                {"source": {"a": "base", "only.base": 2}},
            ]
        }
    )
    # Profile-specific sources are applied after base sources, regardless of response order.
    assert merge_property_sources(content) == {"a": "profile", "only.profile": 1, "only.base": 2}


def test_config_server_merge_ignores_bad_shapes():
    assert merge_property_sources(json.dumps({"propertySources": "nope"})) == {}
    assert merge_property_sources(json.dumps({})) == {}
