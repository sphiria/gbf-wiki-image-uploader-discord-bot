"""Proxy-only downloads of game scripts, manifests and spritesheets."""

import re
import time
from urllib.parse import urlsplit

import requests

from animation_assets import AssetNotFound
from http_settings import BROWSER_USER_AGENT


class AssetDownloader:
    def __init__(self, proxy_url, delay=0):
        self.delay = delay
        if not proxy_url:
            raise ValueError("Set PROXY_URL before downloading game assets")
        try:
            parsed = urlsplit(proxy_url)
            valid = (
                parsed.scheme in ("http", "https") and parsed.hostname and parsed.port
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(
                "PROXY_URL must be an HTTP(S) proxy URL with a host and port"
            )
        self.session = requests.Session()
        # Avoid environment proxies and NO_PROXY silently changing the selected route.
        self.session.trust_env = False
        self.session.proxies = {"http": proxy_url, "https": proxy_url}
        self.session.headers["User-Agent"] = BROWSER_USER_AGENT

    def preflight(self, cdn):
        parsed = urlsplit(cdn)
        try:
            with self.session.get(
                parsed.scheme + "://" + parsed.netloc + "/", timeout=15, stream=True
            ) as response:
                if response.status_code == 407:
                    raise ValueError("Proxy authentication failed; check PROXY_URL")
        except requests.RequestException as error:
            if re.search(r"Tunnel connection failed: 407\b", str(error)):
                raise ValueError(
                    "Proxy authentication failed (HTTP 407); check PROXY_URL"
                ) from None
            raise ValueError(
                "Proxy preflight failed; no direct fallback attempted"
            ) from None
        # Like the bot, any non-407 CDN response confirms connectivity (the root may be 404).
        print("Proxy-backed game CDN access verified.", flush=True)

    def fetch(self, url):
        if self.delay > 0:
            time.sleep(self.delay)
        for attempt in range(4):
            try:
                with self.session.get(url, timeout=60) as response:
                    status = response.status_code
                    if status == 404:
                        raise AssetNotFound(
                            "Game asset not found: " + url.rsplit("/", 1)[-1]
                        )
                    if status == 200:
                        return response.content
                    if status not in (429, 500, 502, 503, 504) or attempt == 3:
                        raise ValueError(f"Game asset download failed: HTTP {status}")
            except requests.RequestException as error:
                if re.search(r"Tunnel connection failed: 407\b", str(error)):
                    raise ValueError(
                        "Proxy authentication failed (HTTP 407); check PROXY_URL"
                    ) from None
                if attempt == 3:
                    raise ValueError(
                        "Game asset proxy request failed; no direct fallback attempted"
                    ) from None
            time.sleep(2**attempt)

    def close(self):
        self.session.close()
