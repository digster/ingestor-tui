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

## 2026-02-27 (Session 3)

Accommodate gmail-ingestor changes in TUI: Add 6 new rate-limiting config settings (max_retries, initial_backoff_seconds, max_backoff_seconds, inter_batch_delay_seconds, inter_page_delay_seconds, num_retries) to Dashboard config display. Add specific RateLimitError handling in _do_operation with user-friendly message suggesting to wait or adjust settings.

## 2026-03-05

TUI Updates for gmail-ingestor incremental sync: Add "Full Sync" checkbox to Operations widget (maps to force_full_sync param). Pass force_full_sync to ingestor.run() and ingestor.run_discovery() calls. Add get_sync_state() to DBReader querying sync_state table with graceful missing-table handling. Add "Sync State" section to Dashboard showing per-label history_id and last sync timestamp. Tests for checkbox params, sync state queries, and missing table edge case.

## 2026-03-14

Show running command above log output: Add a dedicated Static label (#command-label) to LogPanelWidget between the title and RichLog. set_command() displays the active operation name in bold/accent color, clear_command() resets to dim idle state. Called from _run_operation() (set) and _do_operation() finally block (clear) in app.py.

## 2026-03-14 (Session 2)

Persist command name after operation completes: Replace clear_command() in _do_operation() finally block with complete_command(status). The label now shows "Completed: {name}", "Cancelled: {name}", or "Failed: {name}" with CSS classes .completed ($success color) or .error ($error color). Tracks command name in _current_command field. clear_command() retained for the Clear button.

## 2026-03-14 (Session 3)

Show CLI command in log panel label: Instead of showing just the operation name (e.g., "discover"), the command label now shows the full CLI command (e.g., "gmail-ingestor discover --label INBOX --limit 100"). Added _build_cli_command() helper with operation-to-subcommand mapping, per-operation flag lists, and smart defaults (skips None, False, offset=0, quotes values with spaces/commas).
