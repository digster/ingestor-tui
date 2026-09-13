---
name: backfill-mapping
description: Author or repair a backfill mapping for a newsletter label — analyse a publication's archive page, derive the listing mode and selectors, and write the entry into backfill_mappings.json. Use when the user wants to backfill a newsletter, adds a new archive URL, or reports that a backfill is returning too few articles.
---

# Authoring a backfill mapping

A mapping tells the backfill runner where a publication's web archive lives and how to
read it. You write one entry per Gmail label into `backfill_mappings.json`.

**The single most important decision is the listing mode**, because getting it wrong fails
silently: a selector-only mapping against a client-rendered archive backfills the first
page and reports success. Everything below is structured around not doing that.

## 1. Gather inputs

You need a label name and an archive URL. Resolve the label ID from the ingestor database
— it must match a label that actually holds messages, or matching falls back to a slow
markdown scan:

```bash
sqlite3 ../gmail-ingestor/data/gmail_ingestor.db "SELECT label_id, label_name FROM labels WHERE label_name LIKE '%NAME%';"
```

Then check the **sender timeline**, before you look at the archive at all:

```bash
sqlite3 ../gmail-ingestor/data/gmail_ingestor.db "SELECT m.sender, MIN(date), MAX(date), COUNT(*) FROM messages m JOIN message_labels ml ON ml.message_id=m.message_id WHERE ml.label_id='LABEL_ID' GROUP BY m.sender ORDER BY MIN(date);"
```

One sender is the simple case. Two or more senders whose date ranges **do not abut** mean
the publication moved, and you must find out whether the back catalogue moved with it.
Compare the archive's earliest post against the label's earliest message: if the archive
starts *later* than the corpus does, an older archive exists at another URL.

When that happens the label needs **more than one archive source** — see step 4b. The scan
gives you no warning otherwise: a mapping can pass every check below while an entire back
catalogue sits at a subdomain it never visits (see "A rebrand can split one label across two
archives" in `LEARNINGS.md`). Probe *each* archive separately; they may need different modes.

If the label name in the mapping and the one in `labels` differ, the mapping's key must be
the **Gmail label name** — it becomes the `labels:` value in the output front matter, and
`ingestor-tools` files articles into `newsletters/{that name}/`.

## 2. Probe the archive

```bash
uv run ingestor-backfill probe https://example.com/archive
```

Read the output in this order:

| Field | What it tells you |
|---|---|
| `Platform` | Substack / Ghost / beehiiv / WordPress fingerprint |
| `Articles in static HTML` | How many articles a plain fetch sees |
| `JSON listing endpoints` | An endpoint was found and its fields enumerated |
| `Repeated container candidates` | Ranked `item_selector` candidates |
| `URL patterns` | The article URL shape, e.g. `/p/{slug}` |
| `WARNINGS` | Read these first — they name the mode you should use |

## 3. Choose the mode

Decide in this order and stop at the first match:

1. **A JSON endpoint was found → `mode: "json"`.** Always prefer it. It paginates
   reliably, carries clean titles and ISO dates, and needs no selectors.
2. **Static HTML shows all the articles → `mode: "html"`.** Confirm the count looks like a
   full archive, not one page of it.
3. **Neither → `mode: "rendered"`.** JS-only archive with no endpoint. Note in `notes` that
   it needs `uv sync --extra rendered && uv run playwright install chromium`.

### Verify pagination before committing

For `html` mode with a templated URL, prove that page 2 differs from page 1:

```bash
uv run ingestor-backfill probe <url> --check-pagination 'https://example.com/archive?offset={offset}'
```

`paginates: false` means the site ignores your parameter. Substack does exactly this on its
HTML archive — it accepts `?offset=12` and returns page 1 again. If you see `false`, the
template is wrong: find the real pagination scheme, or switch modes.

## 4. Write the entry

Add to `backfill_mappings.json` under `mappings`, keyed by label name.

**JSON mode** (Substack shown; field names come from the probe's `fields` list):

```json
"Some Newsletter": {
  "label_id": "Label_123",
  "archive_url": "https://example.com/archive",
  "sender": "Author Name <author@example.com>",
  "listing": {
    "mode": "json",
    "url_template": "https://example.com/api/v1/archive?sort=new&offset={offset}&limit={limit}",
    "pagination": { "type": "offset", "page_size": 50, "start": 0, "max_pages": 40 },
    "items_path": "",
    "fields": { "url": "canonical_url", "title": "title", "date": "post_date" }
  },
  "article": {
    "content_selector": "div.available-content",
    "title_selector": "h1",
    "date_selector": "time[datetime]",
    "date_attr": "datetime"
  },
  "notes": "Why this mode was chosen, and anything surprising about the site."
}
```

**HTML mode**:

```json
"listing": {
  "mode": "html",
  "url_template": "https://example.com/archive/page/{page}",
  "pagination": { "type": "page", "start": 1, "max_pages": 50 },
  "item_selector": "article.post-card",
  "fields": {
    "url":   { "selector": "a.post-title", "attr": "href" },
    "title": { "selector": "a.post-title", "attr": "text" },
    "date":  { "selector": "time", "attr": "datetime" }
  }
}
```

Notes on the fields:

- `items_path` — dotted path to the array. `""` for a bare array at the JSON root.
- `fields` — dotted JSON paths in `json` mode; `{selector, attr}` objects in `html` mode,
  where `attr` is an attribute name or `"text"`, and an empty `selector` means the item
  element itself.
- `date` is optional. Without it the article page is used, which costs nothing extra
  because the page is fetched anyway.
- Single-page archives use `"pagination": {"type": "none"}`; a paginated one can instead
  set `next_page_selector` to follow the site's own "older posts" link, which is more
  reliable than guessing a URL scheme.
- **Prefer structural selectors over class names.** `a[href*="/p/"]` survives a redesign;
  `a.clamp-3-lxFDfR` is a hashed design-system class that will not.
- `sender` becomes the `from:` line in the output front matter. Match what the publication
  actually sends from — check the DB:
  `SELECT DISTINCT sender FROM messages m JOIN message_labels ml ON ml.message_id=m.message_id WHERE ml.label_id='...';`
- `notes` is not decoration. Record *why* the mode was chosen and any trap you hit; the
  next person to touch the mapping (or you, in six months) needs that reasoning.

### 4b. Labels that span more than one archive

When the sender timeline showed a split, use `sources` instead of a top-level
`archive_url`. Each entry is a full archive config — its own listing mode, selectors and
`sender` — and they are read in the order given:

```json
"Neel Chhabra": {
  "label_id": "Label_1703214449706674033",
  "sender": "Neel Chhabra from Resight <resight@substack.com>",
  "sources": [
    {
      "name": "Resight",
      "archive_url": "https://resight.substack.com/archive",
      "sender": "Neel Chhabra from Resight <resight@substack.com>",
      "listing": { "mode": "json", "...": "..." },
      "article": { "content_selector": "div.available-content" },
      "notes": "Current publication, from 2026-07-20."
    },
    {
      "name": "Neel's Newsletter (pre-rebrand)",
      "archive_url": "https://neelchhabra.substack.com/archive",
      "sender": "Neel Chhabra from Neel's Newsletter <neelchhabra@substack.com>",
      "listing": { "mode": "json", "...": "..." },
      "article": { "content_selector": "div.available-content" },
      "notes": "23 posts, 2025-04-28 to 2026-05-09. The rename did not move them."
    }
  ],
  "notes": "Why this label is split, and where the boundary falls."
}
```

Rules that matter:

- **Order is meaningful.** `sources[0]` is primary: when two archives list the same post,
  the first one wins. Put the current/canonical archive first.
- **Set `sender` per source** whenever the sending address changed. It lands in the `from:`
  front matter, so getting it wrong misattributes a whole era. A source without one
  inherits the mapping-level `sender`.
- **`name` is for humans** — it appears in logs, `list` output and the TUI. Without it you
  get the archive's hostname.
- **Do not set both** a top-level `archive_url` and `sources`; that is a validation error,
  because which archive is primary would be ambiguous.
- Dedup runs across sources on canonical URL *and* normalised title, so an archive that
  imported the other's back catalogue will not write everything twice.
- A dead archive is tolerated when there are others — the run logs a warning and continues.
  It only fails if every source yields nothing.

Probe each archive separately before writing the entry: a publication that changed platform
may need `json` for one era and `html` or `rendered` for another.

## 5. Validate

```bash
uv run ingestor-backfill validate
uv run ingestor-backfill scan --label "Some Newsletter"
```

Check all four before declaring it done:

- **Listing count is plausible.** Suspiciously round numbers — exactly 10, 12, 20, 24 —
  usually mean one page was read and pagination stopped early. Compare against what the
  archive page shows when scrolled.
- **Every source contributed.** A multi-source scan logs one
  `Archive '<name>' contributed N article(s)` line per archive. A zero there means that
  source is misconfigured or dead, and the run will otherwise look entirely healthy.
- **Titles and dates are real**, not empty strings or `----------`.
- **The held/missing split makes sense.** All-missing on a label with hundreds of held
  messages means the matcher is not finding overlap: check that `label_id` is right and
  that the titles resemble the email subjects.
- **A spot check of an article page works:**
  `uv run ingestor-backfill run --label "Some Newsletter" --limit 1` then read the written
  markdown. If the body contains navigation or subscribe prompts, tighten
  `content_selector`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Far fewer articles than the archive shows | Client-rendered listing | Switch to `json`, or `rendered` |
| Same articles repeat, count stalls | Pagination parameter ignored | `--check-pagination`; find the real scheme |
| Everything reports as missing | Wrong `label_id`, or titles differ from subjects | Re-resolve the ID; check subjects in the DB |
| Article bodies contain site chrome | `content_selector` too broad | Narrow it; probe the article page |
| `content_selector matched nothing` warnings | Selector went stale after a redesign | Re-probe an article page and update it |
| `ListingError: ... needs Playwright` | `rendered` mode without the extra | `uv sync --extra rendered && uv run playwright install chromium` |

## What you must not change

The mapping controls **reading** the archives. It does not control the output format.
Filenames, front matter and the `web-<hash>` IDs come from shared code so backfilled files
stay byte-compatible with ingested ones — do not add fields hoping to influence them.
