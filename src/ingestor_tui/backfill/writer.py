"""Write a backfilled article into ../output/ in the Gmail pipeline's shape.

Everything about the output format is **borrowed, not reimplemented**:

* front matter from ``MarkdownConverter._build_front_matter``
* the ``{slug}_{id}.md`` filename from ``MarkdownWriter``
* the ``{id}.html`` raw file from ``RawEmailStore``

That is the whole point of this module. A second, parallel definition of the
output format would drift the first time either side changed — and the format
has already cost this project one real bug (see the YAML-escaping note in
gmail-ingestor's LEARNINGS.md, where escaping quotes before backslashes made 16
files unparseable downstream). Backfill inherits that fix rather than risking
it again.

The only addition is two provenance keys injected into the front matter.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

from gmail_ingestor.core.converter import MarkdownConverter, _escape_yaml
from gmail_ingestor.core.exceptions import ConversionError
from gmail_ingestor.core.models import EmailBody, EmailHeader
from gmail_ingestor.storage.raw_store import RawEmailStore
from gmail_ingestor.storage.writer import MarkdownWriter

from ingestor_tui.backfill.mappings import BackfillMapping
from ingestor_tui.backfill.models import ExtractedArticle

logger = logging.getLogger(__name__)

# Marks a file as web-scraped rather than email-ingested. Both consumers of
# this front matter ignore unknown keys (ingestor-tools uses yaml.safe_load and
# reads only `labels`; newsletters-web's line parser collects them and never
# looks), so adding these is backward-compatible.
ORIGIN_BACKFILL = "backfill"


@dataclasses.dataclass(frozen=True)
class WrittenArticle:
    """Paths produced for one backfilled article."""

    article_id: str
    markdown_path: Path
    raw_html_path: Path


class BackfillWriter:
    """Converts extracted articles and writes them alongside ingested emails."""

    def __init__(self, markdown_dir: Path, raw_dir: Path) -> None:
        self._converter = MarkdownConverter()
        self._markdown_writer = MarkdownWriter(markdown_dir)
        self._raw_store = RawEmailStore(raw_dir)

    def write(
        self,
        article_id: str,
        article: ExtractedArticle,
        mapping: BackfillMapping,
    ) -> WrittenArticle:
        """Convert and persist one article.

        Conversion happens first and entirely in memory, so a trafilatura
        failure leaves no half-written pair of files on disk.

        Raises:
            ConversionError: If the article body yields no markdown.
        """
        header = self._build_header(article, mapping)
        body = EmailBody(plain_text=None, html=article.content_html)

        converted = self._converter.convert(article_id, header, body)
        markdown = _inject_front_matter(
            converted.markdown,
            {"source_url": article.url, "origin": ORIGIN_BACKFILL},
        )
        converted = dataclasses.replace(converted, markdown=markdown)

        # Raw first, then markdown: the markdown file is what ingestor-tools
        # enumerates, so writing it last means a crash between the two can
        # never produce a markdown file whose raw body is missing.
        saved = self._raw_store.store(article_id, body)
        raw_html_path = saved.get("html")
        if raw_html_path is None:  # pragma: no cover — body.html is always set
            raise ConversionError(f"No raw HTML stored for {article.url}")

        markdown_path = self._markdown_writer.write(converted)
        logger.info("Wrote %s", markdown_path.name)

        return WrittenArticle(
            article_id=article_id,
            markdown_path=markdown_path,
            raw_html_path=raw_html_path,
        )

    @staticmethod
    def _build_header(article: ExtractedArticle, mapping: BackfillMapping) -> EmailHeader:
        """Build the EmailHeader the converter turns into front matter.

        ``to`` is empty, matching ingested newsletters (which are sent to a
        list, not addressed to the recipient). Labels carry only the mapped
        label — a backfilled article has no Gmail state, so no INBOX/UNREAD.
        """
        return EmailHeader(
            subject=article.title,
            sender=mapping.sender,
            to="",
            date=article.published_at,
            label_ids=(mapping.label_id,) if mapping.label_id else (),
            label_names=(mapping.label_name,),
        )


def _inject_front_matter(markdown: str, fields: dict[str, str]) -> str:
    """Insert extra keys before the closing ``---`` of a front-matter block.

    Values are escaped with the converter's own ``_escape_yaml`` so the
    backslash-before-quote ordering that this project has already been bitten
    by is applied here too, rather than re-derived.

    Returns the markdown unchanged if it has no recognisable front matter.
    """
    lines = markdown.split("\n")
    if not lines or lines[0].strip() != "---":
        logger.warning("Markdown has no front matter — provenance keys not added")
        return markdown

    try:
        closing = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        logger.warning("Unclosed front matter — provenance keys not added")
        return markdown

    injected = [f'{key}: "{_escape_yaml(str(value))}"' for key, value in fields.items()]
    return "\n".join(lines[:closing] + injected + lines[closing:])
