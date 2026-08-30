# Ingestor TUI

Textual-based terminal UI for the Gmail Ingestor pipeline. Provides an interactive interface to control the 3-stage email ingestion pipeline (discover, fetch, convert), monitor progress in real-time, and inspect pipeline state.

## Setup

```bash
# From the ingestor-tui directory
uv sync
```

Requires the `gmail-ingestor` project to be available at `../gmail-ingestor` (sibling directory).

## Usage

```bash
# Launch with default project directory (../gmail-ingestor)
uv run ingestor-tui

# Launch with a custom project directory
uv run ingestor-tui --project-dir /path/to/gmail-ingestor
```

## Tabs

| Tab | Key | Description |
|-----|-----|-------------|
| Dashboard | `d` | Status counts, last run info, configuration display, project directory |
| Labels | `l` | Gmail label browser with search/filter and refresh |
| Operations | `o` | Pipeline controls, parameter inputs, progress bar, stop button |
| Backfill | `b` | Fill gaps in a newsletter from the publication's web archive |
| Log | `g` | Real-time log output from the ingestor |

Press `q` to quit.

## Features

- **Dashboard**: Auto-refreshing status overview (pending/fetched/converted/failed counts), last fetch run details, configuration display, and editable project directory.
- **Operations**: Buttons for Full Fetch, Discover, Fetch Pending, Convert Pending, Retry Failed, and Stop. Confirmation dialog before running operations. Supports multiple comma-separated labels for Discover and Full Fetch. Configurable parameters (label, query, limit, offset, batch size). Real-time progress bar updates. **Label Presets**: Save, load, and delete named label presets that persist across sessions (`~/.config/ingestor-tui/label_presets.json`).
- **Labels**: Browse available Gmail labels in a sortable DataTable with real-time search/filter.
- **Backfill**: Fill gaps in a newsletter's history from the publication's own web archive — for authors who moved platforms, back catalogues we subscribed to late, or posts that were never emailed. Pick a mapped label, **Scan** to see what is missing versus already held, then **Backfill** to fetch and write the gaps. See [Backfill](#backfill) below.
- **Log Panel**: All `gmail_ingestor.*` and `ingestor_tui.*` log output piped into a RichLog widget with syntax highlighting.

## Backfill

Gmail is the ingestor's only source, so anything that never arrived as an email is missing
permanently. Backfill closes those gaps from the publication's web archive, writing articles
into `../output/` in exactly the format the Gmail pipeline produces — the downstream
organizer and site builder need no changes.

### Adding a mapping

Each label needs one entry in `backfill_mappings.json` saying where its archive is and how
to read it. Ask Claude Code to author it — the `backfill-mapping` skill probes the archive,
picks a listing mode and validates the result:

> Add a backfill mapping for the "Joan Westenberg" label using https://www.joanwestenberg.com/archive

### CLI

```bash
uv run ingestor-backfill probe <url>              # analyse an archive page
uv run ingestor-backfill list                     # mappings and their state
uv run ingestor-backfill validate                 # check the mapping file
uv run ingestor-backfill scan --label "Name"      # what's missing (no writes)
uv run ingestor-backfill run  --label "Name"      # fetch and write the gaps
```

`run` accepts `--limit N` to cap what is written, `--dry-run` to report without touching
disk, `--delay` to change the inter-request pause (default 1s), and `--ignore-robots`.

### Listing modes

| Mode | Use for | Notes |
|---|---|---|
| `html` | Server-rendered archives (Ghost, Jekyll, WordPress) | CSS selectors + page/offset template, or a next-page link |
| `json` | Sites with a listing endpoint (Substack, beehiiv, Ghost API) | Preferred where available — paginates reliably |
| `rendered` | JS-only archives with no endpoint | Needs `uv sync --extra rendered && uv run playwright install chromium` |

Prefer `json` when a probe finds an endpoint. A client-rendered archive read with `html`
selectors will silently return only its first page.

### Output

Backfilled articles are written exactly like ingested ones, with IDs derived from the
article URL rather than Gmail:

```
../output/markdown/{slug}_web-<16 hex>.md
../output/raw/web-<16 hex>.html
```

Their front matter carries the usual keys plus two provenance fields, `source_url` and
`origin: backfill`. State lives in `data/backfill.db` alongside — never inside —
`gmail_ingestor.db`, which backfill only ever opens read-only.

## Development

```bash
# Run tests
uv run pytest tests/ -v

# Run with Textual dev tools
uv run textual run --dev src/ingestor_tui/app.py
```
