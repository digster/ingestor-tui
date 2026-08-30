"""Command-line interface for newsletter backfill.

Exists for two reasons: it is what the ``backfill-mapping`` skill drives while
authoring a mapping, and it makes the whole feature testable end-to-end without
starting the TUI.

    ingestor-backfill probe <url>        analyse an archive page
    ingestor-backfill list               show mappings and their state
    ingestor-backfill validate           check the mapping file
    ingestor-backfill scan --label X     classify entries, no writes
    ingestor-backfill run --label X      backfill the missing articles
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from gmail_ingestor.config.settings import GmailIngestorSettings

from ingestor_tui.backfill.fetcher import DEFAULT_DELAY_SECONDS, Fetcher
from ingestor_tui.backfill.mappings import MappingError, MappingStore
from ingestor_tui.backfill.models import ScanEntry
from ingestor_tui.backfill.probe import probe, verify_pagination
from ingestor_tui.backfill.runner import BackfillRunner
from ingestor_tui.backfill.store import BackfillTracker
from ingestor_tui.db_reader import DBReader

logger = logging.getLogger(__name__)

DEFAULT_PROJECT_DIR = Path("../gmail-ingestor")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # Resolved before any command runs, because _settings() chdirs into the
    # gmail-ingestor project and a relative --mappings would then point
    # somewhere else entirely.
    if args.mappings is not None:
        args.mappings = args.mappings.resolve()

    try:
        return args.handler(args)
    except MappingError as e:
        print(f"Mapping error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130
    except Exception as e:
        logger.debug("Command failed", exc_info=True)
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ingestor-backfill",
        description="Backfill missing newsletter articles from a publication's web archive.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    parser.add_argument(
        "--mappings",
        type=Path,
        default=None,
        help="Path to backfill_mappings.json (default: repo root)",
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=DEFAULT_PROJECT_DIR,
        help="gmail-ingestor project directory (default: ../gmail-ingestor)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    probe_parser = subparsers.add_parser("probe", help="Analyse an archive page")
    probe_parser.add_argument("url", help="Archive listing URL")
    probe_parser.add_argument(
        "--json", action="store_true", help="Emit the raw report as JSON"
    )
    probe_parser.add_argument(
        "--check-pagination",
        metavar="TEMPLATE",
        help="Verify a templated listing URL actually paginates",
    )
    probe_parser.set_defaults(handler=_cmd_probe)

    list_parser = subparsers.add_parser("list", help="Show mappings and backfill state")
    list_parser.set_defaults(handler=_cmd_list)

    validate_parser = subparsers.add_parser("validate", help="Validate the mapping file")
    validate_parser.set_defaults(handler=_cmd_validate)

    for name, help_text in (
        ("scan", "Classify archive entries against the corpus (no writes)"),
        ("run", "Backfill the missing articles"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--label", "-l", required=True, help="Label to backfill")
        sub.add_argument("--limit", type=int, default=None, help="Cap articles processed")
        sub.add_argument(
            "--delay",
            type=float,
            default=DEFAULT_DELAY_SECONDS,
            help=f"Seconds between requests (default: {DEFAULT_DELAY_SECONDS})",
        )
        sub.add_argument(
            "--ignore-robots",
            action="store_true",
            help="Fetch even when robots.txt disallows it",
        )
        if name == "run":
            sub.add_argument(
                "--dry-run",
                action="store_true",
                help="Report what would be written without touching disk",
            )
        sub.set_defaults(handler=_cmd_scan if name == "scan" else _cmd_run)

    return parser


# --- helpers ---


def _store(args: argparse.Namespace) -> MappingStore:
    return MappingStore(args.mappings) if args.mappings else MappingStore()


def _settings(args: argparse.Namespace) -> GmailIngestorSettings:
    """Load gmail-ingestor settings from its project directory.

    GmailIngestorSettings reads `.env` and resolves relative paths from the CWD,
    so the directory switch is what makes `../output/markdown` mean the same
    thing here as it does when the TUI or the CLI run the Gmail pipeline.
    """
    project_dir = args.project_dir.resolve()
    if not project_dir.is_dir():
        raise RuntimeError(f"Not a directory: {project_dir}")
    os.chdir(project_dir)
    return GmailIngestorSettings()


def _runner(args: argparse.Namespace) -> BackfillRunner:
    return BackfillRunner(
        _settings(args),
        mapping_store=_store(args),
        delay_seconds=args.delay,
        respect_robots=not args.ignore_robots,
    )


def _print_entries(entries: list[ScanEntry], limit: int = 40) -> None:
    """Render a scan result as an aligned table, missing entries first."""
    ordered = sorted(entries, key=lambda e: (not e.is_missing, _sort_date(e)))
    for entry in ordered[:limit]:
        marker = "MISSING" if entry.is_missing else "have   "
        published = entry.ref.published_at
        date = published.strftime("%Y-%m-%d") if published else "----------"
        title = entry.ref.title[:62]
        print(f"  {marker}  {date}  {title}")
        if entry.match_reason and not entry.is_missing:
            print(f"           └─ {entry.match_reason[:88]}")
    if len(ordered) > limit:
        print(f"  … and {len(ordered) - limit} more")


def _sort_date(entry: ScanEntry) -> str:
    return entry.ref.published_at.isoformat() if entry.ref.published_at else ""


# --- commands ---


def _cmd_probe(args: argparse.Namespace) -> int:
    with Fetcher(delay_seconds=DEFAULT_DELAY_SECONDS) as fetcher:
        report = probe(args.url, fetcher)

        if args.json:
            print(report.to_json())
        else:
            print(f"\nArchive: {report.url}")
            print(f"Platform: {report.platform}")
            print(f"Articles in static HTML: {report.static_article_count}")

            print("\nURL patterns:")
            for pattern, count in report.url_patterns.items():
                print(f"  {count:>4}  {pattern}")

            print("\nRepeated container candidates (item_selector):")
            for candidate in report.container_candidates:
                print(f"  {candidate['count']:>4}  {candidate['selector']}")

            print("\n<time> elements:")
            for element in report.time_elements:
                print(f"  datetime={element['datetime']!r}  text={element['text']!r}")

            if report.feeds:
                print("\nFeeds:")
                for feed in report.feeds:
                    print(f"  {feed['type']}  {feed['href']}")

            if report.json_endpoints:
                print("\nJSON listing endpoints:")
                for endpoint in report.json_endpoints:
                    print(f"  {endpoint['url_template']}")
                    print(f"    items_path: {endpoint['items_path']!r}")
                    print(f"    fields: {', '.join(endpoint['available_fields'][:12])}")

            print("\nSample articles:")
            for sample in report.sample_articles:
                print(f"  {sample['url']}\n    {sample['text']}")

            if report.warnings:
                print("\nWARNINGS:")
                for warning in report.warnings:
                    print(f"  ! {warning}")

        if args.check_pagination:
            print("\nPagination check:")
            for key, value in verify_pagination(args.check_pagination, fetcher).items():
                print(f"  {key}: {value}")

    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    store = _store(args)
    mappings = store.list_mappings()

    if not mappings:
        print(f"No mappings in {store.path}")
        return 0

    settings = _settings(args)
    tracker_path = BackfillTracker.beside(settings.database_path).path
    counts: dict[str, dict[str, int]] = {}
    if tracker_path.exists():
        with BackfillTracker(tracker_path) as tracker:
            counts = {name: tracker.count_by_status(name) for name in mappings}

    print(f"\n{len(mappings)} mapping(s) in {store.path}\n")
    for name, mapping in mappings.items():
        state = counts.get(name, {})
        summary = ", ".join(f"{k}={v}" for k, v in sorted(state.items())) or "no runs yet"
        print(f"  {name}")
        print(f"    archive: {mapping.archive_url}")
        print(f"    mode:    {mapping.listing.mode}  (label_id={mapping.label_id or 'unset'})")
        print(f"    state:   {summary}")
        if mapping.notes:
            print(f"    notes:   {mapping.notes}")
        print()
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    store = _store(args)
    mappings = store.list_mappings()  # raises MappingError on the first problem
    settings = _settings(args)
    reader = DBReader(settings.database_path)

    print(f"{len(mappings)} mapping(s) valid in {store.path}")

    # A label_id that no longer resolves is the one error the schema cannot
    # catch, and it silently degrades matching to the markdown fallback.
    problems = 0
    for name, mapping in mappings.items():
        if not mapping.label_id:
            print(f"  ! {name}: no label_id set — matching will use the markdown fallback")
            problems += 1
            continue
        held = reader.get_messages_for_label(mapping.label_id)
        if not held:
            print(f"  ! {name}: label_id {mapping.label_id} matches no messages in the database")
            problems += 1
        else:
            print(f"  ✓ {name}: {len(held)} messages held")

    return 1 if problems else 0


def _cmd_scan(args: argparse.Namespace) -> int:
    entries = _runner(args).scan(args.label, limit=args.limit)
    missing = [e for e in entries if e.is_missing]

    print(f"\n{args.label}: {len(entries)} listed, "
          f"{len(entries) - len(missing)} already held, {len(missing)} missing\n")
    _print_entries(entries)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    result = _runner(args).run(args.label, limit=args.limit, dry_run=args.dry_run)

    total_missing = sum(1 for e in result.entries if e.is_missing)
    print(f"\n{result.label}: {result.listed} listed, {result.already_held} already held, "
          f"{total_missing} missing")

    if result.dry_run:
        print(f"DRY RUN — would backfill {result.selected} article(s)\n")
        _print_entries(result.entries)
    else:
        print(f"Backfilled {result.written} of {result.selected} selected, "
              f"{result.failed} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
