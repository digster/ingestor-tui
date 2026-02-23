# Prompts

## 2026-02-23

Implement the Ingestor TUI — a Textual-based terminal interface for the Gmail Ingestor pipeline. Single-screen app with TabbedContent (4 tabs: Dashboard, Operations, Labels, Log). Operations run in @work(thread=True) workers. Custom logging.Handler pipes gmail_ingestor.* logs into RichLog. Lightweight DBReader reads SQLite DB directly for dashboard state. Full project scaffold with pyproject.toml, tests, and documentation.
