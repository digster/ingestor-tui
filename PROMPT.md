# Prompts

## 2026-02-24

Multi-Select Labels → Operations Copy UX: Add checkbox column to Labels DataTable with row-click toggling, selection count indicator, "Copy to Operations" and "Clear Selection" buttons. LabelsSelected message carries selected label IDs to Operations input field and switches tab.

## 2026-02-23

Implement the Ingestor TUI — a Textual-based terminal interface for the Gmail Ingestor pipeline. Single-screen app with TabbedContent (4 tabs: Dashboard, Operations, Labels, Log). Operations run in @work(thread=True) workers. Custom logging.Handler pipes gmail_ingestor.* logs into RichLog. Lightweight DBReader reads SQLite DB directly for dashboard state. Full project scaffold with pyproject.toml, tests, and documentation.

## 2026-02-23 (Session 2)

Implement TUI improvements: fix params grid CSS (grid-size 2 3 → 2 5), reorder tabs to Dashboard → Labels → Operations → Log, add search/filter to Labels tab, add confirmation dialog before operations, support multiple comma-separated labels, add Stop button for running operations, and add editable project directory in Dashboard.

## 2026-02-23 (Session 3)

Fix `NoActiveWorker` error on Operations button click — replace `push_screen_wait` with `push_screen` callback pattern since message handlers run on the main thread, not a worker thread.

## 2026-02-27

Implement save/load label presets in Operations tab. Add PresetStore (JSON persistence at ~/.config/ingestor-tui/label_presets.json), PresetNameDialog (modal for entering preset name), and preset row (Select dropdown + Load/Save/Del buttons) in OperationsWidget. All preset logic encapsulated in OperationsWidget with event.stop() — no app.py changes. Tests for PresetStore unit tests and app integration.

## 2026-02-27 (Session 2)

Fix `NoActiveWorker` crash when saving/deleting presets — decorate `_handle_save` and `_handle_delete` with `@work` (from `textual import work`), remove `await` from calls in `on_button_pressed`, update tests to match.
