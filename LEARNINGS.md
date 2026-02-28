# Learnings

Lessons learned while working with this codebase. Read this file to avoid repeating past mistakes.

## Textual: `push_screen_wait` requires a worker context

**Error:** `NoActiveWorker: push_screen must be run from a worker when wait_for_dismiss is True`

**Cause:** Calling `await self.app.push_screen_wait(...)` from a message handler (e.g., `on_button_pressed`) crashes because `push_screen_wait` sets `wait_for_dismiss=True`, which requires a Textual worker context.

**Fix:** Always decorate methods that use `push_screen_wait` with `@work` from `textual.work`, and call them without `await` from the message handler:

```python
from textual import work

def on_button_pressed(self, event: Button.Pressed) -> None:
    # No await — @work methods are fire-and-forget from handlers
    self._handle_action()

@work
async def _handle_action(self) -> None:
    result = await self.app.push_screen_wait(SomeDialog())
    # ... use result
```

**Rule:** Any async method that calls `push_screen_wait` (or any API requiring a worker) must be decorated with `@work`. The caller should invoke it without `await`.
