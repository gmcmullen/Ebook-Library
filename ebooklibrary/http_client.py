"""Polite, thread-safe HTTP client with per-host rate limiting and backoff."""
import threading
import time
from typing import Dict, Optional
from urllib.parse import urlparse

import requests

from .config import Config
from .logging_setup import get_logger

logger = get_logger()

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
)


class RateLimitedSession:
    """Wraps requests.Session with per-host pacing, retries and a cooling circuit breaker.

    Rate limiting is per host rather than global, so a slow or throttled API
    (Google Books) no longer stalls requests to unrelated ones (OpenLibrary,
    Wikipedia). When a host returns 429 the interval for that host is widened
    for the rest of the run, which keeps a large library from burning its whole
    quota in the first few minutes.
    """

    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

        self._lock = threading.Lock()
        self._last_request: Dict[str, float] = {}
        self._interval: Dict[str, float] = {}
        self._cooldown_until: Dict[str, float] = {}
        self._trips: Dict[str, int] = {}
        self._host_locks: Dict[str, threading.Lock] = {}

    def _host_lock(self, host: str) -> threading.Lock:
        with self._lock:
            return self._host_locks.setdefault(host, threading.Lock())

    def _interval_for(self, host: str) -> float:
        with self._lock:
            return self._interval.get(host, self.config.min_request_interval)

    def _widen_interval(self, host: str) -> float:
        """Double this host's interval (capped) after a rate-limit response."""
        with self._lock:
            current = self._interval.get(host, self.config.min_request_interval)
            widened = min(current * 2, 8.0)
            self._interval[host] = widened
            return widened

    def _in_cooldown(self, host: str) -> bool:
        with self._lock:
            until = self._cooldown_until.get(host, 0)
        return time.time() < until

    def _start_cooldown(self, host: str) -> float:
        """Sideline a host, doubling the pause each time it trips again."""
        with self._lock:
            trips = self._trips.get(host, 0) + 1
            self._trips[host] = trips
            duration = min(self.config.host_cooldown * (2 ** (trips - 1)), 900.0)
            self._cooldown_until[host] = time.time() + duration
            return duration

    def _retries_for(self, host: str) -> int:
        """Retry budget for a host.

        Once a host has tripped the breaker, retrying is almost always wasted
        time: an unauthenticated Google Books quota does not recover within a
        backoff ladder. Sideline it immediately instead and let the fallback
        providers do the work.
        """
        with self._lock:
            return 0 if self._trips.get(host) else self.config.max_retries

    def _wait_turn(self, host: str) -> None:
        """Sleep until this host is allowed another request."""
        interval = self._interval_for(host)
        with self._lock:
            last = self._last_request.get(host, 0.0)
        elapsed = time.time() - last
        if elapsed < interval:
            time.sleep(interval - elapsed)

    @staticmethod
    def _retry_after(response: requests.Response, fallback: float) -> float:
        """Honour a Retry-After header when the server sends one."""
        raw = response.headers.get("Retry-After")
        if not raw:
            return fallback
        try:
            return min(float(raw), 60.0)
        except (TypeError, ValueError):
            return fallback

    def get(self, url: str, retry_count: int = 0) -> Optional[requests.Response]:
        """GET a URL, returning None rather than raising on any failure."""
        host = urlparse(url).hostname or ""

        if self._in_cooldown(host):
            logger.debug(f"Skipping {host} — cooling down after rate limit")
            return None

        try:
            # Serialise per host so pacing is honoured across worker threads.
            with self._host_lock(host):
                self._wait_turn(host)
                response = self.session.get(url, timeout=self.config.request_timeout)
                with self._lock:
                    self._last_request[host] = time.time()

            if response.status_code == 429:
                widened = self._widen_interval(host)
                if retry_count < self._retries_for(host):
                    wait = self._retry_after(
                        response, self.config.retry_delay * (2 ** retry_count)
                    )
                    logger.debug(
                        f"Rate limited by {host}; waiting {wait:.0f}s "
                        f"(interval now {widened:.1f}s)"
                    )
                    time.sleep(wait)
                    return self.get(url, retry_count + 1)

                duration = self._start_cooldown(host)
                logger.warning(
                    f"{host} is rate limiting — pausing it for {duration:.0f}s "
                    f"and using the other providers"
                )
                return None

            # 4xx other than 429 is permanent; retrying only wastes quota.
            if 400 <= response.status_code < 500:
                if response.status_code != 404:
                    logger.debug(f"Client error {response.status_code} for {url}")
                return None

            response.raise_for_status()
            return response

        except requests.exceptions.RequestException as e:
            if retry_count < self._retries_for(host):
                wait = self.config.retry_delay * (2 ** retry_count)
                logger.debug(f"Request to {host} failed ({e}); retrying in {wait:.0f}s")
                time.sleep(wait)
                return self.get(url, retry_count + 1)
            logger.debug(f"Request to {host} failed after {self.config.max_retries} retries: {e}")
            return None

    def get_json(self, url: str) -> Optional[dict]:
        """GET and parse JSON, returning None if the request or parse fails."""
        response = self.get(url)
        if not response:
            return None
        try:
            return response.json()
        except ValueError as e:
            logger.debug(f"Invalid JSON from {url}: {e}")
            return None
