"""Polite HTTP fetching shared by the listing and article stages.

Centralises the things it would be rude (or fragile) to get wrong per-caller:
a descriptive User-Agent, an inter-request delay, a robots.txt check, and
uniform timeout/error handling.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

logger = logging.getLogger(__name__)

# Identifies the tool and points at the project, so a publisher who sees it in
# their logs can tell what it is. Anonymous scrapers get blocked; named ones
# usually do not.
USER_AGENT = (
    "ingestor-tui-backfill/0.1 (+https://github.com/digster/ingestor-tui; "
    "personal newsletter archive backfill)"
)

DEFAULT_TIMEOUT = 30.0
DEFAULT_DELAY_SECONDS = 1.0


class FetchError(RuntimeError):
    """Raised when a URL could not be retrieved."""


class RobotsDisallowedError(FetchError):
    """Raised when robots.txt forbids fetching a URL."""


@dataclass
class Fetcher:
    """A requests.Session wrapper that paces itself and respects robots.txt.

    The delay is applied *between* requests rather than before the first, so a
    single-article fetch is not artificially slowed.
    """

    delay_seconds: float = DEFAULT_DELAY_SECONDS
    timeout: float = DEFAULT_TIMEOUT
    respect_robots: bool = True

    def __post_init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})
        self._last_request_at: float | None = None
        # One parser per origin; robots.txt is fetched at most once per host.
        self._robots: dict[str, RobotFileParser | None] = {}

    # --- politeness ---

    def _sleep_if_needed(self) -> None:
        if self._last_request_at is None or self.delay_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.delay_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _robots_for(self, url: str) -> RobotFileParser | None:
        """Fetch and cache the robots.txt parser for a URL's origin.

        Returns None when robots.txt is absent or unreadable — an unreachable
        robots.txt is conventionally treated as "no restrictions", and failing
        closed here would make the feature unusable on sites that simply do not
        publish one.
        """
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots:
            return self._robots[origin]

        parser: RobotFileParser | None = RobotFileParser()
        robots_url = urljoin(origin, "/robots.txt")
        try:
            response = self._session.get(robots_url, timeout=self.timeout)
            if response.status_code >= 400:
                parser = None
            else:
                assert parser is not None
                parser.parse(response.text.splitlines())
        except requests.RequestException as e:
            logger.debug("Could not read %s (%s) — treating as unrestricted", robots_url, e)
            parser = None

        self._robots[origin] = parser
        return parser

    def check_allowed(self, url: str) -> None:
        """Raise RobotsDisallowedError if robots.txt forbids fetching ``url``."""
        if not self.respect_robots:
            return
        parser = self._robots_for(url)
        if parser is not None and not parser.can_fetch(USER_AGENT, url):
            raise RobotsDisallowedError(
                f"robots.txt at {urlparse(url).netloc} disallows {url}. "
                "Pass --ignore-robots to override."
            )

    # --- fetching ---

    def get_text(self, url: str) -> str:
        """Fetch a URL and return its decoded body."""
        return self._get(url).text

    def get_json(self, url: str) -> object:
        """Fetch a URL and parse its body as JSON."""
        response = self._get(url)
        try:
            return response.json()
        except ValueError as e:
            raise FetchError(f"{url} did not return valid JSON: {e}") from e

    def _get(self, url: str) -> requests.Response:
        self.check_allowed(url)
        self._sleep_if_needed()
        logger.debug("GET %s", url)
        try:
            response = self._session.get(url, timeout=self.timeout)
        except requests.RequestException as e:
            raise FetchError(f"GET {url} failed: {e}") from e
        finally:
            self._last_request_at = time.monotonic()

        if response.status_code >= 400:
            raise FetchError(f"GET {url} returned HTTP {response.status_code}")
        return response

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
