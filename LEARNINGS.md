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


## Scraping: a paginated archive can silently ignore your page parameter

**Symptom:** A backfill reports success having collected exactly one page of articles, or
loops to `max_pages` collecting the same items over and over.

**Cause:** Substack's HTML archive accepts `?offset=12` and returns page 1 again. It does
not 404, does not redirect, and does not error — page 2 is simply page 1. Any loop that
trusts "I asked for page 2, so this is page 2" reads a fraction of the archive and reports
a clean finish. `verify_pagination` on `joanwestenberg.com/archive` measured
`new_on_page_2: 0` against a 113-article archive.

**Fix:** Never take a page's *existence* as proof of progress. `listing.read_listing`
tracks seen URLs and stops when a page contributes **no new ones**:

```python
if new_on_page == 0:
    break   # an empty page and an all-duplicate page are the same signal
```

`ingestor-backfill probe --check-pagination` measures this before a mapping is committed,
and `ListingConfig` refuses a template whose pagination type has no matching placeholder.

**Rule:** When walking any paginated remote resource, terminate on *no new results*, not on
an empty response. And do not infer the end of a listing from a short page — Substack
returns short pages mid-archive, and treating one as the end truncated 113 articles to 23
during development.

## Scraping: a client-rendered archive looks fine to `curl`

**Symptom:** CSS selectors work, extract real titles and URLs, and return a fraction of the
articles the page visibly holds.

**Cause:** `curl` on `joanwestenberg.com/archive` returns 12 posts; the rendered DOM holds
24; the archive holds 113. Nothing about the static markup announces that it is partial.

**Fix:** `probe.py` reports `static_article_count` and warns when it is suspiciously small,
auto-discovers JSON listing endpoints per platform, and recommends `json` (or `rendered`)
over `html`. Round numbers — 10, 12, 20, 24 — are the tell.

## Reuse the writer, do not reimplement the format

Backfill writes files that `ingestor-tools` and `newsletters-web` consume. It builds an
`EmailHeader` and calls gmail-ingestor's own `MarkdownConverter` / `MarkdownWriter` /
`RawEmailStore` rather than formatting front matter itself.

This is not tidiness. The front-matter format has already caused one real bug (escaping
quotes before backslashes made 16 files unparseable downstream — see gmail-ingestor's
LEARNINGS). A parallel implementation would have needed that fix re-derived, and would
drift the next time either side changed. The only backfill-specific step is injecting
`source_url` and `origin` between the existing keys and the closing `---`.

**Rule:** When a second producer writes into an existing format, call the first producer's
writer. Reserve custom formatting for genuinely new fields.

## Textual: `call_from_thread` raises when called *from* the app thread

**Error:** `RuntimeError: The `call_from_thread` method must run in a different thread from
the app`, surfacing as a `--- Logging error ---` traceback rather than a test failure,
because it happens inside a logging handler.

**Cause:** `TUILogHandler.emit` always marshalled writes with `app.call_from_thread`. That
was correct for as long as the handler only captured `gmail_ingestor`, whose records all
originate in worker threads. Attaching it to `ingestor_tui` as well (so backfill output
reaches the Log tab) suddenly routed main-thread records — `on_mount`'s "Working directory"
line among them — down the same path, and Textual rejects that.

**Fix:** Route on the calling thread, mirroring Textual's own guard:

```python
if getattr(app, "_thread_id", None) == threading.get_ident():
    self._rich_log.write(msg)          # already on the app thread
else:
    app.call_from_thread(self._rich_log.write, msg)
```

**Rule:** `call_from_thread` is not a safe default — it is specifically the *cross*-thread
path and errors on the near side. Any helper that might be reached from either thread has to
check. Note also how this hid: a logging handler that raises gets swallowed by
`handleError`, so the app kept running and only stderr showed the problem. Tests that assert
on the widget's contents would have passed.

## The rendered artifact is the raw HTML, not the markdown

**Symptom:** Backfilled articles rendered as full-bleed images and edge-to-edge text with
stray icon glyphs, while ingested emails in the same list looked right — even though backfill
deliberately reused gmail-ingestor's `MarkdownConverter`, `MarkdownWriter` and `RawEmailStore`
specifically so its output would match.

**Cause:** It reused them faithfully and matched the wrong file. `newsletters-web`'s build
picks `sorted(glob("*.html"))[0]` as the article body and reads the `.md` only for
front-matter metadata. The `.md` is a sidecar; `../output/raw/{id}.html` is what people see.

The deeper reason the mismatch was invisible: an email and a web page store their styling in
opposite places.

* Mail clients strip `<link rel="stylesheet">`, so senders are **forced** to inline
  everything — a `<style>` block plus `style=""` on every element. An email is self-describing
  by necessity, which is why `RawEmailStore`'s verbatim copy survives an iframe with no CSS.
* A web page keeps its styling in external CDN stylesheets. Storing the content subtree
  preserved the styling *reference* and lost the styling itself.

So backfill was saving HTML all along. It was saving HTML that could not stand on its own.

**Fix:** `extractor._wrap_document` emits a complete document with `ARTICLE_PAGE_CSS` inlined,
mirroring `EMAIL_PAGE_CSS` in `newsletters-web/scripts/build_site.py` — which already does
exactly this for emails that arrived without an HTML part. Element-level selectors only, so
any inline style that did survive still wins: a floor, not an override.

**Rule:** Reusing a producer's writer proves the *format* matches. It proves nothing about how
the output renders. Before claiming downstream parity, find the file the consumer actually
opens and compare that one. And ask what a format depends on that you are not storing.

**Corollary — `include_links=True` is not the same as "it has HTML".** Trafilatura runs with
`output_format="txt"` in this project, so no `.md` in `../output/markdown` contains a tag.
Both pipelines' markdown already matched; only the raw HTML ever differed.


## An upsert can silently downgrade a status that means "files exist"

**Symptom:** `prune` left two backfilled articles behind. Their files were on disk with
`origin: backfill` front matter, but `backfill.db` said `have` — a status that means "a Gmail
message already covers this URL", which owns no files at all.

**Cause:** `classify` reports an already-backfilled URL as `have` with reason
`"already backfilled"` (correct — it stops a re-scrape). `runner._write_missing` then calls
`_record()` for **every** classified entry, and `record_article`'s `ON CONFLICT` did
`status = excluded.status`. So the second scan overwrote `done` — losing `raw_html_path` and
`markdown_path` with it.

The article then churned: `done` → `have` (row overwritten) → `discovered` (no longer in
`completed_urls`, and the Gmail corpus does not hold it) → re-fetched → `done`. Repeat.

**Fix:** the upsert defers to `done` rather than overwriting it:

```sql
status = CASE WHEN backfill_articles.status = 'done' THEN 'done' ELSE excluded.status END
```

`mark_done` / `mark_failed` still set the status outright — only the classification upsert
backs off. `prune` additionally treats **files on disk as authoritative over status**, so
rows already damaged by the old behaviour are still cleaned up.

**Rule:** When a status asserts something about the filesystem, an upsert driven by
*classification* must not be allowed to overwrite it. Classification is a guess about the
world; `done` is a fact about the disk.


## `ingestor-tools` copies but never overwrites

**Symptom:** A corrected file in `../output/raw/` did not reach the site. The build ran, the
organizer ran, and the stale version was still published.

**Cause:** `newsletter_organizer.organize()` is idempotent by *skipping* — files already
present in the destination are not copied — and it never deletes. So `../newsletters/` is
write-once per path, and it is what `newsletters-web` builds from.

**Fix:** `ingestor-backfill prune --label X` clears all four locations (both `../output`
files, the `../newsletters/<label>/<id>/` directory, and the database rows) so a normal
`run` + organize + build regenerates them.

**Rule:** Changing how a file is *generated* is only half the job when a downstream copier
skips existing paths. Anything that regenerates output into this pipeline needs a way to
invalidate `../newsletters/` too, or the fix stops at `../output/`.

## A rebrand can split one label across two archives, and the scan looks healthy

**Symptom:** A mapping validates, scans, and backfills cleanly — plausible counts, real
titles, exact-match overlap with the corpus — while most of the label's back catalogue sits
at a URL the mapping never visits.

**Cause:** `Neel Chhabra` (`Label_1703214449706674033`) is one Gmail label fed by two
Substack publications. Posts up to 2026-05-09 went out as *Neel's Newsletter* from
`neelchhabra@substack.com` and live at `neelchhabra.substack.com` (23 posts). From
2026-07-20 the publication is *Resight*, sending from `resight@substack.com` and publishing
at `resight.substack.com` (7 posts). The rename did **not** carry the back catalogue to the
new subdomain.

The preceding case set the wrong expectation. Joan Westenberg also migrated platforms, but
her Substack import pulled the whole history into the new home — 113 listed against 27 held
— so a single `archive_url` covered everything. That is the lucky case, not the rule.

Nothing in the scan flags the split. `Neel Chhabra: 7 listed, 3 already held, 4 missing` is
exactly what a healthy small archive looks like, and all three matches were exact
normalised-title hits, so the matcher confirms `label_id` is correct. Every check in the
`backfill-mapping` skill's step 5 passes while 23 posts stay invisible.

**The tell is in the database, not the archive.** A sender change with a delivery gap:

```sql
SELECT m.sender, MIN(date), MAX(date), COUNT(*)
FROM messages m JOIN message_labels ml ON ml.message_id = m.message_id
WHERE ml.label_id = '...' GROUP BY m.sender ORDER BY MIN(date);
```

Two senders whose ranges do not abut mean two publications. Compare the archive's earliest
post against the label's earliest message: if the archive starts *later* than the corpus,
an older archive exists somewhere.

**Rule:** Run the sender-timeline query before writing a mapping. When the label spans more
than one archive, give it a `sources` list — one entry per archive, each with its own
listing mode, selectors and `sender` — and record the boundary in `notes`.

**Resolved:** the schema originally held exactly one `archive_url` per label, so a split
label could not be expressed at all. `ArchiveSource` fixed that. Two details of the fix are
worth keeping in mind when authoring one:

* **`sender` is per source.** It becomes the `from:` line in the front matter. A single
  mapping-level sender would attribute the entire pre-rebrand back catalogue to an address
  that did not exist when those posts were sent.
* **Dedup spans sources on title, not just URL.** Where a migration *did* import the back
  catalogue, both archives list the same post at different URLs — and different URLs mint
  different `web-` IDs, so URL-only dedup would write the article twice. Earlier sources
  win, which is why `sources[0]` should be the canonical archive.
