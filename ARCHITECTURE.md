# Architecture

## Overview

Single-screen Textual app with `TabbedContent` (4 tabs). All tabs stay mounted simultaneously so the log panel auto-scrolls in the background while viewing other tabs.

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
│   │       └── Static (config display)
│   ├── TabPane: Labels
│   │   └── LabelsWidget (widgets/labels.py)
│   │       ├── Input (filter) + Button (refresh)
│   │       ├── Action bar (selection count, Copy to Operations, Clear Selection)
│   │       └── DataTable (checkbox, label id, name) — row-click toggles selection
│   ├── TabPane: Operations
│   │   └── OperationsWidget (widgets/operations.py)
│   │       ├── Button x5 (pipeline operations) + Button (stop)
│   │       ├── Input x5 (label, query, limit, offset, batch_size)
│   │       ├── ProgressBar
│   │       └── Static (stage label)
│   └── TabPane: Log
│       └── LogPanelWidget (widgets/log_panel.py)
│           ├── RichLog (log output)
│           └── Button (clear)
├── ConfirmDialog (widgets/confirm_dialog.py) — modal, pushed on-demand
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

## Key Files

| File | Purpose |
|---|---|
| `src/ingestor_tui/app.py` | Main app, CLI entry point, worker orchestration |
| `src/ingestor_tui/db_reader.py` | Read-only SQLite queries (no OAuth needed) |
| `src/ingestor_tui/widgets/dashboard.py` | Status cards, last run, config display |
| `src/ingestor_tui/widgets/operations.py` | Pipeline buttons, inputs, progress bar |
| `src/ingestor_tui/widgets/labels.py` | Gmail labels DataTable with filter |
| `src/ingestor_tui/widgets/confirm_dialog.py` | Reusable ModalScreen confirmation dialog |
| `src/ingestor_tui/widgets/log_panel.py` | RichLog + TUILogHandler |

## Integration with gmail-ingestor

- Uses `EmailIngestor` from `gmail_ingestor.pipeline.ingestor` for all operations
- Uses `GmailIngestorSettings` from `gmail_ingestor.config.settings` for configuration
- Uses `FetchProgress` from `gmail_ingestor.core.models` for progress callbacks
- `DBReader` mirrors the SQLite schema from `gmail_ingestor.storage.tracker`
