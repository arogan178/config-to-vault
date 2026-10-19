"""Minimal HashiCorp Vault KV v2 client."""

import re
import urllib.parse

import requests
import yaml

from .http import TIMEOUT, new_session

K8S_JWT_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"


def kv_url(addr: str, kind: str, path: str) -> str:
    """KV v2 URL for `kind` ('data' or 'metadata'); the first path segment is the mount."""
    mount, _, secret_path = path.strip("/").partition("/")
    return f"{addr.rstrip('/')}/v1/{urllib.parse.quote(mount)}/{kind}/{urllib.parse.quote(secret_path)}"


class VaultClient:
    def __init__(
        self,
        addr: str,
        token: str = "",
        role_id: str = "",
        secret_id: str = "",
        k8s_role: str = "",
        k8s_mount: str = "",
        namespace: str = "",
    ):
        self.addr = addr.rstrip("/")
        self.session = new_session(extra_methods=("POST", "PUT", "DELETE", "LIST"))
        if namespace:
            self.session.headers["X-Vault-Namespace"] = namespace

        if not token:
            if role_id and secret_id:
                token = self._login("approle", {"role_id": role_id, "secret_id": secret_id})
            elif k8s_role:
                with open(K8S_JWT_PATH, encoding="utf-8") as handle:
                    jwt = handle.read().strip()
                token = self._login(k8s_mount or "kubernetes", {"role": k8s_role, "jwt": jwt})
            else:
                raise RuntimeError(
                    "No Vault auth configured: set VAULT_TOKEN, VAULT_ROLE_ID + VAULT_SECRET_ID, or VAULT_K8S_ROLE"
                )
        self.session.headers["X-Vault-Token"] = token

    def _login(self, mount: str, payload: dict) -> str:
        response = self.session.post(f"{self.addr}/v1/auth/{mount}/login", json=payload, timeout=TIMEOUT)
        response.raise_for_status()
        token = response.json().get("auth", {}).get("client_token")
        if not token:
            raise RuntimeError("Vault login did not return a client token")
        return token

    def check_kv2(self, path: str) -> None:
        """Fail unless the first segment of `path` is a KV v2 mount; a KV v1 mount would take data/ as a folder."""
        url = f"{self.addr}/v1/sys/internal/ui/mounts/{urllib.parse.quote(path.strip('/'))}"
        response = self.session.get(url, timeout=TIMEOUT)
        if response.status_code in (403, 404):
            return  # endpoint not available to this token or Vault version
        response.raise_for_status()
        data = response.json().get("data") or {}
        mount = path.strip("/").split("/")[0] + "/"
        version = (data.get("options") or {}).get("version")
        if data.get("path") != mount or data.get("type") != "kv" or version != "2":
            raise RuntimeError(
                f"{path} is in mount {data.get('path')!r} ({data.get('type')} v{version}); "
                f"expected the KV v2 mount {mount!r}"
            )

    def read_secret(self, path: str) -> dict | None:
        """Current secret data, or None if the secret does not exist."""
        response = self.session.get(kv_url(self.addr, "data", path), timeout=TIMEOUT)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json().get("data", {}).get("data") or {}

    def write_secret(self, path: str, data: dict) -> None:
        response = self.session.post(kv_url(self.addr, "data", path), json={"data": data}, timeout=TIMEOUT)
        response.raise_for_status()

    def list_secrets(self, path: str) -> list[str]:
        """Secret names directly below `path`; sub-folders are skipped."""
        response = self.session.request("LIST", kv_url(self.addr, "metadata", path), timeout=TIMEOUT)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        keys = response.json().get("data", {}).get("keys", [])
        return [k for k in keys if not k.endswith("/")]

    def delete_secret(self, path: str) -> None:
        """Delete the secret and all its versions."""
        response = self.session.delete(kv_url(self.addr, "metadata", path), timeout=TIMEOUT)
        response.raise_for_status()


def describe_error(exc: Exception) -> str:
    """Short error text without response bodies, file contents or URL credentials."""
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        text = f"HTTP {exc.response.status_code} for {exc.response.request.method} {exc.response.url}"
    elif isinstance(exc, yaml.MarkedYAMLError):
        # str() would include a snippet of the offending line, which may hold a secret.
        mark = exc.problem_mark
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        text = f"{type(exc).__name__}: {exc.problem}{where}"
    else:
        text = f"{type(exc).__name__}: {exc}"
    return re.sub(r"://[^/@\s]+@", "://***@", text)
