# Architecture

## Overview

Single-screen Textual app with `TabbedContent` (5 tabs). All tabs stay mounted simultaneously so the log panel auto-scrolls in the background while viewing other tabs.

Two pipelines feed the same output directory: the **Gmail pipeline** (via `gmail-ingestor`) and **backfill** (`ingestor_tui.backfill`), which scrapes a publication's web archive to fill gaps Gmail can never supply.

## Component Hierarchy

```
IngestorApp (app.py)
├── Header
├── TabbedContent
│   ├── TabPane: Dashboard
│   │   └── DashboardWidget (widgets/dashboard.py)
│   │       ├── Input + Button (project directory)
│   │       ├── StatusCard x5 (pending/fetched/converted/failed/total)
│   │       ├── Static (last run info)
│   │       ├── Static (sync state — incremental sync history per label)
│   │       └── Static (config display)
│   ├── TabPane: Labels
│   │   └── LabelsWidget (widgets/labels.py)
│   │       ├── Input (filter) + Button (refresh)
│   │       ├── Action bar (selection count, Copy to Operations, Clear Selection)
│   │       └── DataTable (checkbox, label id, name) — row-click toggles selection
│   ├── TabPane: Operations
│   │   └── OperationsWidget (widgets/operations.py)
│   │       ├── Button x5 (pipeline operations) + Button (stop)
│   │       ├── Select + Button x3 (preset load/save/delete)
│   │       ├── Input x5 (label, query, limit, offset, batch_size)
│   │       ├── Checkbox (Full Sync — force_full_sync)
│   │       ├── ProgressBar
│   │       └── Static (stage label)
│   ├── TabPane: Backfill
│   │   └── BackfillWidget (widgets/backfill.py)
│   │       ├── Action bar (selection, Reload Mappings, Scan, Backfill, Stop)
│   │       ├── Input (limit) + Checkbox (dry run)
│   │       ├── DataTable (mappings: check, label, mode, archive, state)
│   │       ├── DataTable (scan results: status, date, title, detail)
│   │       └── ProgressBar + Static (stage)
│   └── TabPane: Log
│       └── LogPanelWidget (widgets/log_panel.py)
│           ├── Static (command label — shows running operation name)
│           ├── RichLog (log output)
│           └── Button (clear)
├── ConfirmDialog (widgets/confirm_dialog.py) — modal, pushed on-demand
├── PresetNameDialog (widgets/preset_name_dialog.py) — modal, preset name input
└── Footer
```

## Data Flow

```
User clicks button → on_button_pressed() (async)
    → push_screen_wait(ConfirmDialog) → user confirms
    → _run_operation() → switches to Log tab
    → @work(thread=True, exclusive=True) worker starts
        → creates EmailIngestor lazily (triggers OAuth if needed)
        → runs ingestor method (blocking, in worker thread)
        → on_progress callback fires from worker thread
            → call_from_thread() updates OperationsWidget progress
            → call_from_thread() refreshes DashboardWidget
        → TUILogHandler captures gmail_ingestor.* logs
            → call_from_thread() writes to RichLog
    → worker checks is_cancelled between label iterations
    → worker completes → re-enables buttons, final dashboard refresh

Backfill flow:
    User selects a mapping row → BackfillWidget._selected
    → "Scan" or "Backfill" button → posts BackfillRequested
    → IngestorApp.on_backfill_requested()
        → Scan runs immediately (read-only); a real run confirms first
    → @work(thread=True, exclusive=True, group="pipeline") worker
        → BackfillRunner.scan()  reads listing → classifies against corpus
        → BackfillRunner.run()   fetches, converts and writes the gaps
        → on_progress → call_from_thread() updates BackfillWidget
        → should_stop() reads worker.is_cancelled between articles
    → shares the "pipeline" worker group with Gmail ops, so the existing Stop
      button works and the two pipelines can never run concurrently

Labels → Operations copy flow:
    User clicks rows in Labels DataTable → toggles _selected_ids set
    → "Copy to Operations" button → posts LabelsSelected message
    → IngestorApp.on_labels_selected() → sets #input-label value, switches to Operations tab
```

## Key Design Decisions

| Decision | Rationale |
|---|---|
| `@work(thread=True, exclusive=True)` | Ingestor methods are sync/blocking (Gmail API calls); exclusive prevents concurrent ops |
| `DBReader` for dashboard | Direct read-only SQLite queries avoid triggering OAuth just to view local state |
| `os.chdir(project_dir)` on mount | `GmailIngestorSettings()` uses `.env` and resolves relative paths from CWD |
| `TUILogHandler` on `gmail_ingestor` logger | Captures all library log output (discovery pages, fetch errors, etc.) into RichLog |
| `set_interval(5.0)` for dashboard | Auto-polls DB during operations for live status updates |
| `PresetStore` with `~/.config/` path | User-level config (not project-specific) so presets work across project directories |
| Preset buttons use `event.stop()` | Keeps preset logic encapsulated in OperationsWidget, no app.py changes needed |
| `TUILogHandler` also on `ingestor_tui` logger | Backfill logs under its own package; without this its scraping progress and per-article failures would be invisible |
| Backfill in a **separate** `backfill.db` | Backfilled articles are not Gmail messages. In `messages` they would be picked up by `run_convert_pending` (raw paths point nowhere) and reset by `retry_failed`. Separation keeps `gmail_ingestor.db` read-only and its schema untouched |
| Backfill **reuses** `MarkdownConverter` / `MarkdownWriter` / `RawEmailStore` | A second definition of the output format would drift. Front matter, slugs and filenames come from one implementation, so the YAML-escaping fix in gmail-ingestor's LEARNINGS applies to backfilled files for free |
| Backfill IDs are `web-<sha256(url)[:16]>` | No underscore (ingestor-tools splits on the last one), no `/ \ .` (find_raw_files rejects them), and visibly distinct from 16-hex Gmail IDs. Derived from the *canonicalised* URL so tracking parameters cannot mint a second ID |
| Listing pagination stops on "no new URLs" | Substack accepts `?offset=N` on its HTML archive and returns page 1 again. Without this guard a mapping would loop to max_pages, or worse, report a truncated read as complete |
| Backfill shares the `"pipeline"` worker group | The existing Stop button and `_cancel_operation()` apply unchanged, and a backfill can never run concurrently with an ingest |
| Backfilled HTML carries its own stylesheet | The viewer iframe supplies none, and a scraped page's CSS lives on the publisher's CDN. Reusing gmail-ingestor's writers guaranteed the *markdown* matched — but the markdown is not what gets rendered |
| `record_article` never downgrades `done` | `classify` reports an already-backfilled URL as `have`, and the runner records every classified entry. Letting that overwrite `done` lost the file paths, hid the files from `prune`, and made the article churn done → have → discovered → done |
| `prune` reaches into `../newsletters` | `ingestor-tools` only ever *copies*, skipping files already present. Rewriting `../output` alone never propagates — the stale copy is what gets published |
| Title matching, never date matching | Publication and delivery timestamps differ by hours; near a month boundary a date comparison is actively wrong |

## Key Files

| File | Purpose |
|---|---|
| `src/ingestor_tui/app.py` | Main app, CLI entry point, worker orchestration |
| `src/ingestor_tui/db_reader.py` | Read-only SQLite queries (no OAuth needed) |
| `src/ingestor_tui/widgets/dashboard.py` | Status cards, last run, config display |
| `src/ingestor_tui/widgets/operations.py` | Pipeline buttons, inputs, progress bar |
| `src/ingestor_tui/widgets/labels.py` | Gmail labels DataTable with filter |
| `src/ingestor_tui/widgets/confirm_dialog.py` | Reusable ModalScreen confirmation dialog |
| `src/ingestor_tui/widgets/preset_name_dialog.py` | Modal dialog for entering preset name |
| `src/ingestor_tui/widgets/log_panel.py` | RichLog + TUILogHandler |
| `src/ingestor_tui/preset_store.py` | JSON persistence for label presets (~/.config/ingestor-tui/) |
| `src/ingestor_tui/widgets/backfill.py` | Mapping browser, scan results, backfill controls |
| `src/ingestor_tui/backfill/mappings.py` | Load/validate/write `backfill_mappings.json` |
| `src/ingestor_tui/backfill/listing.py` | html/json/rendered listing readers + the pagination guard |
| `src/ingestor_tui/backfill/extractor.py` | Article page → title, date, clean content fragment |
| `src/ingestor_tui/backfill/matcher.py` | Normalised title matching against the existing corpus |
| `src/ingestor_tui/backfill/writer.py` | Converts and writes via gmail-ingestor's own writers |
| `src/ingestor_tui/backfill/store.py` | `backfill.db` state (articles + runs) |
| `src/ingestor_tui/backfill/runner.py` | Orchestrator: list → classify → fetch → write |
| `src/ingestor_tui/backfill/prune.py` | Removes a label's backfilled artifacts from all four places |
| `src/ingestor_tui/backfill/probe.py` | Archive analysis for mapping authoring |
| `src/ingestor_tui/backfill/cli.py` | `ingestor-backfill` console script |
| `backfill_mappings.json` | Per-label archive URL + listing config (version-controlled) |
| `.claude/skills/backfill-mapping/SKILL.md` | LLM workflow for authoring a mapping |

## Backfill Subsystem

Gmail is the ingestor's only source, so anything that never arrived as an email — a back
catalogue we subscribed to late, posts published after an author changed platforms, web-only
or paid-tier articles — is permanently absent. Backfill closes those gaps from the
publication's own web archive.

```
backfill_mappings.json  ──→  MappingStore     (validated config per label)
                                   │
         gmail_ingestor.db  ──→  CorpusIndex   (what we already hold; read-only)
                                   │
archive URL ──→ listing.py ──→ [ArticleRef] ──→ matcher.classify ──→ [ScanEntry]
                                                                        │ missing
                                              extractor.py  ←───────────┘
                                                   │ ExtractedArticle
                                              writer.py  ──→  ../output/markdown/{slug}_{id}.md
                                                          └─→  ../output/raw/{id}.html
                                                               (styled, self-contained —
                                                                this is what readers see)
                                                   │
                                              backfill.db   (state + run audit)
```

### Three listing modes

| Mode | Mechanism | Use for |
|---|---|---|
| `html` | Static fetch + CSS selectors, with a `{page}`/`{offset}` template or a next-page link | Ghost, Jekyll, WordPress |
| `json` | Listing endpoint + dotted field paths | Substack, beehiiv, Ghost Content API |
| `rendered` | Playwright scroll-to-bottom, then the `html` selectors | JS-only archives with no endpoint. Optional `rendered` extra; imported lazily |

`probe.py` fingerprints the platform, reports how many articles the *static* HTML exposes,
and auto-discovers JSON endpoints — so an author is pushed away from `html` mode before a
client-rendered archive is silently half-read.

### Output contract

**The rendered artifact is the raw HTML, not the markdown.** `newsletters-web`'s build picks
`sorted(glob("*.html"))[0]` as the article body and reads the `.md` only for front-matter
metadata; `view.html` then loads that HTML in `<iframe sandbox="allow-same-origin">` whose
only style is a white background. No host CSS reaches the article.

An ingested `{id}.html` survives that bare iframe because mail clients strip external
stylesheets, so senders are forced to inline everything — an email is self-describing by
necessity. A scraped web page is the inverse: semantic markup whose styling lives in external
CDN stylesheets. So `extractor._wrap_document` emits a **complete styled document** — charset,
viewport, `<title>`, and an inlined `ARTICLE_PAGE_CSS` mirroring `EMAIL_PAGE_CSS` in
`newsletters-web/scripts/build_site.py`, which already styles the pages that build generates
for emails with no HTML part. Backfilled articles are the same category and share its look.

Two rules follow from how the viewer works, both easy to break by accident:

* **No `<h1>` or subject header in the body.** `app.js` builds each list preview by walking
  the document's `<body>` text, so a heading would prepend duplicate subject text to every
  preview row. A *footer* is safe — previews truncate from the start, which is where the
  subscriber-only note goes.
* **Interactive chrome is stripped** (`button`, `[role=button]`, form controls). It is inert
  under the iframe's sandbox and renders as stray glyphs without the site's CSS. Overridable
  per publication via `article.strip_selectors`.

Beyond that, backfilled artifacts are indistinguishable from ingested ones to downstream
tools, except for the ID shape and two extra front-matter keys:

```yaml
id: "web-4d77605a905cdfc5"     # sha256 of the canonical URL, not a Gmail ID
source_url: "https://..."      # added by backfill
origin: "backfill"             # added by backfill
```

Both consumers ignore unknown keys — `ingestor-tools` uses `yaml.safe_load` and reads only
`labels`; `newsletters-web`'s hand-rolled line parser collects them and never looks. Backfilled
files carry no `.txt` body, which `find_raw_files()` handles because it probes two candidate
paths rather than globbing.

`labels` on a backfilled file holds only the mapped label, where an ingested one also carries
Gmail system labels (`INBOX`, `UNREAD`, `CATEGORY_*`). Harmless, and marginally better: the
file lands in exactly one folder without relying on `label-stop-list.txt`.

## Integration with gmail-ingestor

- Uses `EmailIngestor` from `gmail_ingestor.pipeline.ingestor` for all operations
- Uses `GmailIngestorSettings` from `gmail_ingestor.config.settings` for configuration
- Uses `FetchProgress` from `gmail_ingestor.core.models` for progress callbacks
- `DBReader` mirrors the SQLite schema from `gmail_ingestor.storage.tracker`
- `run()` and `run_discovery()` accept `force_full_sync` param for incremental vs full sync
- Backfill reuses `MarkdownConverter`, `MarkdownWriter` and `RawEmailStore` so its output format is generated by the same code, not a parallel implementation
- Backfill reads `gmail_ingestor.db` **read-only** via `DBReader` and writes only to its own `data/backfill.db`
