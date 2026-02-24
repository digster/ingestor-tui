# Prompts

## 2026-02-23

Implement the Ingestor TUI — a Textual-based terminal interface for the Gmail Ingestor pipeline. Single-screen app with TabbedContent (4 tabs: Dashboard, Operations, Labels, Log). Operations run in @work(thread=True) workers. Custom logging.Handler pipes gmail_ingestor.* logs into RichLog. Lightweight DBReader reads SQLite DB directly for dashboard state. Full project scaffold with pyproject.toml, tests, and documentation.

## 2026-02-23 (Session 2)

Implement TUI improvements: fix params grid CSS (grid-size 2 3 → 2 5), reorder tabs to Dashboard → Labels → Operations → Log, add search/filter to Labels tab, add confirmation dialog before operations, support multiple comma-separated labels, add Stop button for running operations, and add editable project directory in Dashboard.

## 2026-02-23 (Session 3)

Fix `NoActiveWorker` error on Operations button click — replace `push_screen_wait` with `push_screen` callback pattern since message handlers run on the main thread, not a worker thread.
