import pytest

from config_to_vault.plan import build_plan, diff_keys, prune_dir, vault_path
from config_to_vault.sources import ConfigServerClient, bitbucket_api_url
from config_to_vault.vault import kv_url


def test_vault_path_default_and_custom_template():
    assert vault_path("{base}/{profile}/{name}", "secret/app/", "dev", "orders") == "secret/app/dev/orders"
    assert vault_path("{base}/{name}/{profile}", "/secret", "dev", "orders") == "secret/orders/dev"


@pytest.mark.parametrize("profile,name", [("dev", "../x"), ("a/b", "x"), ("dev", ""), ("", "x")])
def test_vault_path_rejects_unsafe_parts(profile, name):
    with pytest.raises(ValueError):
        vault_path("{base}/{profile}/{name}", "secret", profile, name)


def test_prune_dir():
    assert prune_dir("{base}/{profile}/{name}", "secret/app", "dev") == "secret/app/dev"
    with pytest.raises(ValueError):
        prune_dir("{base}/{name}/{profile}", "secret", "dev")


def test_kv_url_uses_first_segment_as_mount():
    assert kv_url("http://v:8200/", "data", "secret/app/dev/x") == "http://v:8200/v1/secret/data/app/dev/x"
    assert kv_url("http://v:8200", "metadata", "kv/a") == "http://v:8200/v1/kv/metadata/a"


def test_build_plan():
    assert build_plan(None, {"a": 1, "b": 2}) == ("created", ["a", "b"])
    assert build_plan({"a": 1}, {"a": 1}) == ("unchanged", [])
    assert build_plan({"a": 1, "b": 2, "c": 3}, {"a": 1, "b": 5, "d": 4}) == ("updated", ["b", "c", "d"])


def test_diff_distinguishes_types():
    assert diff_keys({"a": 1}, {"a": True}) == ["a"]
    assert diff_keys({"a": "1"}, {"a": 1}) == ["a"]


def test_bitbucket_api_url():
    base = "https://api.bitbucket.org/2.0/repositories/ws/repo/src/main"
    assert bitbucket_api_url("https://bitbucket.org/ws/repo/src/main/conf/dir/") == f"{base}/conf/dir"
    assert bitbucket_api_url("https://bitbucket.org/ws/repo/src/main/") == base
    assert bitbucket_api_url(f"{base}/x/") == f"{base}/x"
    with pytest.raises(ValueError):
        bitbucket_api_url("https://example.com/ws/repo/src/main/x")


def test_config_server_url_template():
    client = ConfigServerClient("https://cs.example/{name}/{profile}")
    assert client.build_url("orders", "dev") == "https://cs.example/orders/dev"
    with pytest.raises(ValueError):
        ConfigServerClient("https://cs.example/fixed")
