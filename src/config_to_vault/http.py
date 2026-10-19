"""Shared HTTP session with retries."""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TIMEOUT = 30


def new_session(extra_methods: tuple[str, ...] = ()) -> requests.Session:
    """Session that retries transient errors; writes are only retried when listed in extra_methods."""
    retry = Retry(
        total=5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS", *extra_methods],
        backoff_factor=2,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
