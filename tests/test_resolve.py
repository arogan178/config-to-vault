import json

import pytest

from config_to_vault.resolve import (
    flatten,
    merge_property_sources,
    parse_properties,
    profile_matches,
    resolve_file,
    resolve_properties,
    resolve_yaml,
)
from config_to_vault.vault import describe_error

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
                {"name": "orders-prod.yml", "source": {"a": "profile", "only.profile": 1, "l[0]": "p"}},
                {"name": "orders.yml", "source": {"a": "app", "b": "app", "l[0]": "x", "l[1]": "y"}},
                {"name": "application.yml", "source": {"a": "base", "b": "base", "only.base": 2}},
            ]
        }
    )
    # Earlier sources win, whether or not they carry spring.profile* keys; lists are replaced whole.
    assert merge_property_sources(content) == {
        "a": "profile",
        "b": "app",
        "only.profile": 1,
        "only.base": 2,
        "l[0]": "p",
    }


def test_config_server_merge_ignores_profile_keys_for_order():
    content = json.dumps(
        {"propertySources": [{"source": {"a": "high"}}, {"source": {"a": "low", "spring.profiles": "x"}}]}
    )
    assert merge_property_sources(content) == {"a": "high"}


@pytest.mark.parametrize("payload", [{"propertySources": "nope"}, {}, {"propertySources": []}, []])
def test_config_server_merge_rejects_empty_or_bad_shapes(payload):
    with pytest.raises(ValueError):
        merge_property_sources(json.dumps(payload))


@pytest.mark.parametrize(
    "expression,expected",
    [("prod", True), ("!dev", True), ("!prod", False), ("prod | eu", True), ("prod & eu", False),
     ("(eu | prod) & !dev", True), ("!(prod)", False)],
)  # fmt: skip
def test_profile_expressions(expression, expected):
    assert profile_matches(expression, "prod") is expected


@pytest.mark.parametrize("expression", ["a & b | c", "(a", "a &", "a)"])
def test_profile_expression_malformed(expression):
    with pytest.raises(ValueError):
        profile_matches(expression, "a")


def test_yaml_negated_profile_and_list():
    text = "a: 1\n---\nspring.config.activate.on-profile: '!dev'\na: 2\n---\nspring.profiles: [x, prod]\nb: 3\n"
    assert resolve_yaml(text, "prod") == {"a": 2, "b": 3}
    assert resolve_yaml(text, "dev") == {"a": 1}


def test_yaml_later_document_wins_and_lists_replace():
    text = "spring.config.activate.on-profile: prod\na: profile\nl: [p]\n---\na: later\n---\nl: [x, y]\ne: []\n"
    assert resolve_yaml("l: [x, y]\n---\nspring.config.activate.on-profile: prod\nl: [p]\n", "prod") == {"l[0]": "p"}
    assert resolve_yaml(text, "prod") == {"a": "later", "l[0]": "x", "l[1]": "y", "e": ""}


def test_properties_java_escapes():
    text = "k\\=1 = v \nt=a\\tb\\u00e9\nbs=c:\\\\\\\n  more\nsp=  lead and trail  \n"
    assert parse_properties(text) == {"k=1": "v ", "t": "a\tb\u00e9", "bs": "c:\\more", "sp": "lead and trail  "}


def test_properties_multi_document():
    text = "a=1\n#---\nspring.config.activate.on-profile=dev\na=2\n!---\nspring.config.activate.on-profile=prod\nb=3\n"
    assert resolve_properties(text, "prod") == {"a": "1", "b": "3"}
    assert resolve_properties(text, "dev") == {"a": "2"}


def test_yaml_error_does_not_leak_line_content():
    with pytest.raises(Exception) as info:
        resolve_yaml("password: hunter2: oops\n", "prod")
    message = describe_error(info.value)
    assert "hunter2" not in message
    assert "line 1" in message


def test_describe_error_redacts_url_credentials():
    assert "s3cret" not in describe_error(RuntimeError("failed for https://user:s3cret@cs.example/a"))
