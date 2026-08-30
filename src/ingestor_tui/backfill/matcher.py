"""Decide which archive articles we already hold.

The authoritative source is the gmail-ingestor SQLite database: one join gives
subject and date per label, where scanning ``../output/markdown/`` would mean
parsing ~17k files. The markdown scan exists only as a fallback for when the DB
is missing or the label has no rows in it.

Matching is on normalised titles, never on dates: a newsletter's publication
timestamp and its delivery timestamp routinely differ by hours, and around a
month boundary that is enough to make a date comparison actively wrong. Dates
are surfaced for the operator to eyeball, and nothing else.
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from gmail_ingestor.storage.writer import MarkdownWriter

from ingestor_tui.backfill.identity import article_id_for, canonicalize_url
from ingestor_tui.backfill.models import ArticleRef, ScanEntry

logger = logging.getLogger(__name__)

# Similarity floor for the fuzzy fallback. 0.90 accepts the small rewordings a
# publisher makes between an email subject line and the on-site headline, while
# still separating distinct posts in a series ("Part One" / "Part Two" score
# well below it once the shared prefix is normalised away).
SIMILARITY_THRESHOLD = 0.90

# Below this length a prefix match means little — "the-list" is a prefix of far
# too many titles to treat as evidence.
MIN_PREFIX_LENGTH = 20

# Reply/forward markers, stripped repeatedly from the front of a subject.
_REPLY_PREFIX_RE = re.compile(r"^\s*(re|fwd?|fw)\s*:\s*", re.IGNORECASE)

# Unicode punctuation that ASCII-folding would delete outright, joining words
# that should stay apart: "state—of—the—art" must not become "stateoftheart".
_DASH_CHARS = "‐‑‒–—―−"


def title_key(text: str) -> str:
    """Normalise a title or subject into a comparable slug.

    Reuses ``MarkdownWriter._slugify`` — the same function that names the
    output files — so the key can never drift from the on-disk convention.
    The length cap is raised well above the writer's 50 chars because the
    prefix rule below needs the untruncated form to be meaningful.
    """
    if not text:
        return ""

    cleaned = unicodedata.normalize("NFKC", text)
    while _REPLY_PREFIX_RE.match(cleaned):
        cleaned = _REPLY_PREFIX_RE.sub("", cleaned, count=1)

    for dash in _DASH_CHARS:
        cleaned = cleaned.replace(dash, "-")

    return MarkdownWriter._slugify(cleaned, max_length=200)


def url_slug(url: str) -> str:
    """The trailing path segment of an article URL, as a comparable slug.

    Substack truncates its slugs to roughly the first six words of the title
    (``how-to-build-a-roadmap-for-the-life``), which is why this is compared by
    prefix rather than equality.
    """
    path = canonicalize_url(url).rsplit("/", 1)
    return path[-1] if path else ""


@dataclass(frozen=True)
class HeldMessage:
    """One message already in the corpus, reduced to what matching needs."""

    message_id: str
    subject: str
    date: str = ""


class CorpusIndex:
    """Normalised index of the messages already held for one label."""

    def __init__(self, messages: list[HeldMessage]) -> None:
        self._messages = messages
        # key → message, first writer wins (duplicated subjects are common for
        # recurring formats like "Weekly Digest"; either match is equally valid)
        self._by_key: dict[str, HeldMessage] = {}
        for message in messages:
            key = title_key(message.subject)
            if key and key not in self._by_key:
                self._by_key[key] = message

    def __len__(self) -> int:
        return len(self._messages)

    @property
    def keys(self) -> list[str]:
        return list(self._by_key)

    def find(self, ref: ArticleRef) -> tuple[HeldMessage, str] | None:
        """Return the held message matching ``ref``, plus why it matched.

        Three rules, cheapest first:

        1. Exact normalised-title equality.
        2. Prefix containment in either direction, which is what catches a
           platform's truncated URL slug against a full email subject.
        3. Fuzzy ratio above the threshold, for small rewordings.
        """
        candidate_key = title_key(ref.title)
        slug = url_slug(ref.url)

        if candidate_key and candidate_key in self._by_key:
            return self._by_key[candidate_key], f"exact title match: {candidate_key!r}"

        for probe in (candidate_key, slug):
            if not probe or len(probe) < MIN_PREFIX_LENGTH:
                continue
            for key, message in self._by_key.items():
                if key.startswith(probe) or probe.startswith(key):
                    return message, f"prefix match: {probe!r} ~ {key!r}"

        if candidate_key:
            close = difflib.get_close_matches(
                candidate_key, self._by_key, n=1, cutoff=SIMILARITY_THRESHOLD
            )
            if close:
                ratio = difflib.SequenceMatcher(None, candidate_key, close[0]).ratio()
                return self._by_key[close[0]], f"fuzzy match ({ratio:.2f}): {close[0]!r}"

        return None


def classify(
    refs: list[ArticleRef],
    corpus: CorpusIndex,
    *,
    known_urls: set[str] | None = None,
) -> list[ScanEntry]:
    """Split archive entries into 'already held' and 'missing'.

    ``known_urls`` are canonical URLs already recorded in backfill.db; they
    short-circuit the title rules entirely so a completed backfill never
    re-scrapes, even if the publisher later edits a headline.
    """
    known = known_urls or set()
    entries: list[ScanEntry] = []

    for ref in refs:
        article_id = article_id_for(ref.url)

        if canonicalize_url(ref.url) in known:
            entries.append(
                ScanEntry(ref=ref, article_id=article_id, status="have",
                          match_reason="already backfilled")
            )
            continue

        found = corpus.find(ref)
        if found is not None:
            message, reason = found
            entries.append(
                ScanEntry(
                    ref=ref,
                    article_id=article_id,
                    status="have",
                    match_reason=f"{reason} → {message.message_id}",
                )
            )
        else:
            entries.append(ScanEntry(ref=ref, article_id=article_id, status="discovered"))

    held = sum(1 for e in entries if e.status == "have")
    logger.info("Classified %d archive entries: %d held, %d missing",
                len(entries), held, len(entries) - held)
    return entries


def index_from_markdown(markdown_dir: Path, label_name: str) -> CorpusIndex:
    """Fallback index built by scanning output markdown front matter.

    Only reads each file's front-matter block, not its body. Used when the
    gmail-ingestor DB is unavailable or has no rows for the label.
    """
    messages: list[HeldMessage] = []
    if not markdown_dir.is_dir():
        logger.warning("Markdown directory not found: %s", markdown_dir)
        return CorpusIndex(messages)

    needle = f'"{label_name}"'
    for path in markdown_dir.glob("*.md"):
        front_matter = _read_front_matter(path)
        if not front_matter:
            continue
        labels_line = next(
            (line for line in front_matter if line.startswith("labels:")), ""
        )
        if needle not in labels_line:
            continue

        subject = _front_matter_value(front_matter, "subject")
        if subject:
            messages.append(
                HeldMessage(
                    message_id=path.stem.rsplit("_", 1)[-1],
                    subject=subject,
                    date=_front_matter_value(front_matter, "date"),
                )
            )

    logger.info("Markdown fallback found %d held messages for %r", len(messages), label_name)
    return CorpusIndex(messages)


def _read_front_matter(path: Path, max_lines: int = 40) -> list[str]:
    """Return the front-matter lines of a markdown file, without its body."""
    lines: list[str] = []
    try:
        with path.open(encoding="utf-8") as handle:
            if handle.readline().strip() != "---":
                return []
            for _ in range(max_lines):
                line = handle.readline()
                if not line or line.strip() == "---":
                    break
                lines.append(line.rstrip("\n"))
    except OSError as e:
        logger.debug("Could not read %s: %s", path, e)
        return []
    return lines


def _front_matter_value(lines: list[str], key: str) -> str:
    """Pull a single scalar value out of front-matter lines."""
    prefix = f"{key}:"
    for line in lines:
        if line.startswith(prefix):
            value = line[len(prefix):].strip()
            if value.startswith('"') and value.endswith('"') and len(value) > 1:
                value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            return value
    return ""
