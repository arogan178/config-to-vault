"""Where file names (and optionally their content) come from."""

import urllib.parse
from pathlib import Path

from .http import TIMEOUT, new_session

LOCAL_SUFFIXES = (".yml", ".yaml", ".properties", ".json")


def list_local_files(source_dir: Path) -> list[Path]:
    return sorted(p for p in source_dir.rglob("*") if p.is_file() and p.suffix.lower() in LOCAL_SUFFIXES)


def bitbucket_api_url(url: str) -> str:
    """Convert a bitbucket.org browse URL (.../src/<branch>/<path>) to its API 2.0 listing URL."""
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc == "api.bitbucket.org":
        return url.rstrip("/")
    if parsed.netloc == "bitbucket.org":
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 4 and parts[2] == "src":
            workspace, repo, _, branch, *path_parts = parts
            api_url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/src/{branch}"
            if path_parts:
                api_url = f"{api_url}/{'/'.join(path_parts)}"
            return api_url
    raise ValueError(f"Could not derive Bitbucket API URL from {url}")


def list_bitbucket_files(url: str, email: str = "", api_token: str = "") -> list[str]:
    """List YAML file names below a Bitbucket Cloud folder, following subfolders and pagination."""
    session = new_session()
    if email and api_token:
        session.auth = (email, api_token)

    urls_to_visit = [bitbucket_api_url(url)]
    paths: list[str] = []
    while urls_to_visit:
        current_url = urls_to_visit.pop(0)
        while current_url:
            response = session.get(current_url, timeout=TIMEOUT)
            if response.status_code >= 400:
                raise RuntimeError(f"Bitbucket API request failed ({response.status_code}) for {current_url}")
            payload = response.json()
            for item in payload.get("values", []):
                if item.get("type") == "commit_file":
                    paths.append(item.get("path", ""))
                elif item.get("type") == "commit_directory":
                    dir_url = item.get("links", {}).get("self", {}).get("href")
                    if dir_url:
                        urls_to_visit.append(dir_url)
            current_url = payload.get("next")

    names = {p.split("/")[-1] for p in paths if p.lower().endswith((".yaml", ".yml"))}
    return sorted(names)


class ConfigServerClient:
    """Fetches /{name}/{profile}-style responses from a Spring Cloud Config Server."""

    def __init__(self, url_template: str, token: str = "", timeout: int = TIMEOUT):
        if "{name}" not in url_template:
            raise ValueError("Config server URL template must contain {name}")
        self.url_template = url_template
        self.timeout = timeout
        self.session = new_session()
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def build_url(self, name: str, profile: str) -> str:
        quote = urllib.parse.quote
        return self.url_template.replace("{name}", quote(name, safe="")).replace("{profile}", quote(profile, safe=""))

    def fetch(self, name: str, profile: str) -> str:
        response = self.session.get(self.build_url(name, profile), timeout=self.timeout)
        response.raise_for_status()
        return response.text
