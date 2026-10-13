"""HTTP: SEC User-Agent, SEC rate limit, retries, disk cache.

SEC rules: a User-Agent of the form "Name email@domain" is mandatory and the
fair-access cap is 10 requests/second. The UA is read from BIOCAT_UA and the
program refuses to start without it, because a missing or generic UA gets the
client IP blocked, and a block mid-run silently empties Stage 2 and 3.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

SEC_HOSTS = ("sec.gov", "efts.sec.gov", "data.sec.gov", "www.sec.gov")
SEC_MAX_RPS = 8.0  # stay under the 10/s cap with margin
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) biocat"


class ConfigError(RuntimeError):
    pass


def sec_user_agent() -> str:
    ua = os.environ.get("BIOCAT_UA", "").strip()
    if not ua or not re.search(r"\S+@\S+\.\S+", ua) or len(ua.split()) < 2:
        raise ConfigError(
            'BIOCAT_UA is not set (or has no email). SEC requires e.g.\n'
            '  export BIOCAT_UA="Jane Doe jane@example.com"')
    return ua


class _Bucket:
    def __init__(self, rps: float):
        self.min_gap = 1.0 / rps
        self.last = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            gap = self.last + self.min_gap - now
            if gap > 0:
                time.sleep(gap)
            self.last = time.monotonic()


class Http:
    """GET with per-host politeness, retries and an optional JSON disk cache."""

    def __init__(self, cache_dir: str | None = None, ttl_hours: float = 24.0, retries: int = 4):
        self.cache_dir = cache_dir
        self.ttl = ttl_hours * 3600
        self.retries = retries
        self.sec_bucket = _Bucket(SEC_MAX_RPS)
        self.sec_ua = sec_user_agent()
        self.calls = {"network": 0, "cache": 0}
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    # ------------------------------------------------------------------ cache
    def _cache_path(self, url: str) -> str | None:
        if not self.cache_dir:
            return None
        return os.path.join(self.cache_dir, hashlib.sha256(url.encode()).hexdigest() + ".json")

    def _cache_get(self, url: str):
        p = self._cache_path(url)
        if p and os.path.exists(p) and time.time() - os.path.getmtime(p) < self.ttl:
            with open(p) as f:
                self.calls["cache"] += 1
                return json.load(f)
        return None

    def _cache_put(self, url: str, data) -> None:
        p = self._cache_path(url)
        if p:
            tmp = p + ".part"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, p)

    # ------------------------------------------------------------------- get
    def get_text(self, url: str, use_cache: bool = True) -> str:
        """Raw document (e.g. an 8-K .htm), cached like JSON."""
        return self.get_json(url, None, use_cache, _text=True)["text"]

    def get_json(self, url: str, params: dict | None = None, use_cache: bool = True, _text: bool = False):
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        if use_cache:
            hit = self._cache_get(url)
            if hit is not None:
                return hit
        host = urllib.parse.urlparse(url).hostname or ""
        is_sec = any(host == h or host.endswith("." + h) for h in SEC_HOSTS)
        headers = {"User-Agent": self.sec_ua if is_sec else BROWSER_UA,
                   "Accept": "*/*" if _text else "application/json"}
        last_err = None
        for attempt in range(self.retries):
            if is_sec:
                self.sec_bucket.wait()
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=60) as r:
                    raw = r.read()
                    data = {"text": raw.decode("utf-8", "ignore")} if _text else json.loads(raw)
                self.calls["network"] += 1
                if use_cache:
                    self._cache_put(url, data)
                return data
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 404:
                    raise
                if e.code == 403 and is_sec:
                    raise ConfigError(f"SEC returned 403 for {url}: check BIOCAT_UA / rate") from e
                if e.code not in (429, 500, 502, 503, 504):
                    raise
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last_err = e
            time.sleep(2 ** attempt)
        raise RuntimeError(f"GET failed after {self.retries} tries: {url}: {last_err}")
