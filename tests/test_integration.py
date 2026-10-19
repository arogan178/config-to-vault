"""Runs against a real Vault dev server. Start one with:

docker run -d --name ctv-vault -p 18200:8200 -e VAULT_DEV_ROOT_TOKEN_ID=root hashicorp/vault
CONFIG_TO_VAULT_VAULT=1 uv run pytest tests/test_integration.py
"""

import os
import shutil
import time

import pytest
import requests

from config_to_vault.cli import main
from config_to_vault.vault import VaultClient

pytestmark = pytest.mark.skipif(os.getenv("CONFIG_TO_VAULT_VAULT") != "1", reason="set CONFIG_TO_VAULT_VAULT=1")

ADDR = "http://127.0.0.1:18200"
BASE = "secret/ctv-test"


@pytest.fixture
def vault(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_ADDR", ADDR)
    monkeypatch.setenv("VAULT_TOKEN", "root")
    for _ in range(30):
        try:
            if requests.get(f"{ADDR}/v1/sys/health", timeout=2).ok:
                break
        except requests.RequestException:
            time.sleep(1)
    client = VaultClient(ADDR, token="root")
    for name in client.list_secrets(f"{BASE}/prod"):
        client.delete_secret(f"{BASE}/prod/{name}")
    return client


def run(source, *extra):
    return main(["--source-dir", str(source), "--profile", "prod", "--vault-base", BASE, *extra])


def test_dry_run_apply_rerun_prune(vault, tmp_path, capsys):
    src = tmp_path / "conf"
    shutil.copytree("examples/config", src)

    assert run(src, "--shared-base", "application") == 0
    assert "created" in capsys.readouterr().out
    assert vault.list_secrets(f"{BASE}/prod") == []

    assert run(src, "--shared-base", "application", "--apply") == 0
    assert sorted(vault.list_secrets(f"{BASE}/prod")) == ["billing", "feature-flags", "orders-service"]
    orders = vault.read_secret(f"{BASE}/prod/orders-service")
    assert orders["orders.db.host"] == "db.prod.example"
    assert orders["orders.retries"] == 5
    assert orders["logging.level"] == "INFO"

    assert run(src, "--shared-base", "application", "--apply") == 0
    assert "3 unchanged" in capsys.readouterr().out

    (src / "billing.properties").write_text("billing.currency=USD\n")
    assert run(src, "--shared-base", "application", "--apply") == 0
    out = capsys.readouterr().out
    assert "changed: billing.currency" in out
    assert "USD" not in out

    (src / "feature-flags.json").unlink()
    assert run(src, "--shared-base", "application", "--apply", "--prune") == 0
    out = capsys.readouterr().out
    assert "deleted    feature-flags" in out
    assert sorted(vault.list_secrets(f"{BASE}/prod")) == ["billing", "orders-service"]


def test_prune_dry_run_keeps_secret(vault, tmp_path, capsys):
    src = tmp_path / "conf"
    shutil.copytree("examples/config", src)
    assert run(src, "--apply") == 0
    (src / "feature-flags.json").unlink()
    assert run(src, "--prune") == 0
    assert "would-delete" in capsys.readouterr().out
    assert "feature-flags" in vault.list_secrets(f"{BASE}/prod")
