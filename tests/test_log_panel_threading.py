"""Regression tests for TUILogHandler's threading behaviour.

Capturing the `ingestor_tui` logger (added for backfill) exposed a latent bug:
`emit` always routed through `app.call_from_thread`, which Textual rejects when
it is already on the app's own thread. Only worker-thread logs reached the
handler before, so nothing had ever hit that path.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import pytest

from ingestor_tui.app import IngestorApp
from ingestor_tui.widgets.log_panel import LogPanelWidget


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    (tmp_path / ".env").write_text("GMAIL_DATABASE_PATH=data/gmail_ingestor.db\n")
    return tmp_path


@pytest.mark.asyncio
async def test_main_thread_log_does_not_error(project_dir: Path, caplog) -> None:
    """A log record emitted on the app thread must reach the RichLog.

    Reproduces the crash: logging from on_mount (main thread) raised
    "The `call_from_thread` method must run in a different thread from the app"
    inside the handler, which logging swallowed into a noisy handler error.
    """
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)):
        handler = app.query_one("#log-panel", LogPanelWidget)._handler
        assert handler is not None

        errors: list[BaseException] = []
        handler.handleError = lambda record: errors.append(  # type: ignore[method-assign]
            RuntimeError("handleError called")
        )

        logging.getLogger("ingestor_tui.test").info("from the app thread")
        assert errors == []


@pytest.mark.asyncio
async def test_worker_thread_log_still_works(project_dir: Path) -> None:
    """The original path — logging from a worker thread — must be unaffected."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)) as pilot:
        handler = app.query_one("#log-panel", LogPanelWidget)._handler
        assert handler is not None

        errors: list[str] = []
        handler.handleError = lambda record: errors.append("failed")  # type: ignore[method-assign]

        done = threading.Event()

        def log_from_thread() -> None:
            logging.getLogger("ingestor_tui.test").info("from a worker thread")
            done.set()

        thread = threading.Thread(target=log_from_thread)
        thread.start()
        for _ in range(50):
            await pilot.pause()
            if done.is_set():
                break
        thread.join(timeout=5)

        assert errors == []


@pytest.mark.asyncio
async def test_both_loggers_are_captured(project_dir: Path) -> None:
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)):
        panel = app.query_one("#log-panel", LogPanelWidget)
        for name in ("gmail_ingestor", "ingestor_tui"):
            assert panel._handler in logging.getLogger(name).handlers


@pytest.mark.asyncio
async def test_handlers_removed_on_unmount(project_dir: Path) -> None:
    """Repeated app runs in one process must not stack handlers."""
    app = IngestorApp(project_dir)
    async with app.run_test(size=(120, 40)):
        handler = app.query_one("#log-panel", LogPanelWidget)._handler

    for name in ("gmail_ingestor", "ingestor_tui"):
        assert handler not in logging.getLogger(name).handlers
