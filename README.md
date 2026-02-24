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
| Log | `g` | Real-time log output from the ingestor |

Press `q` to quit.

## Features

- **Dashboard**: Auto-refreshing status overview (pending/fetched/converted/failed counts), last fetch run details, configuration display, and editable project directory.
- **Operations**: Buttons for Full Fetch, Discover, Fetch Pending, Convert Pending, Retry Failed, and Stop. Confirmation dialog before running operations. Supports multiple comma-separated labels for Discover and Full Fetch. Configurable parameters (label, query, limit, offset, batch size). Real-time progress bar updates.
- **Labels**: Browse available Gmail labels in a sortable DataTable with real-time search/filter.
- **Log Panel**: All `gmail_ingestor.*` log output piped into a RichLog widget with syntax highlighting.

## Development

```bash
# Run tests
uv run pytest tests/ -v

# Run with Textual dev tools
uv run textual run --dev src/ingestor_tui/app.py
```
