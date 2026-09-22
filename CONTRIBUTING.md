# Contributing

## Scope

scilib uses documented public APIs the way their operators intend. Pull requests
adding pirate mirrors, publisher-page scraping, credential sharing or anti-bot
circumvention will not be merged. This is not squeamishness: publishers monitor
institutional proxy traffic and respond by cutting off the entire institution,
so a tool that does this costs its users the legitimate access they already had.

A new source is in scope if it is a public API with published terms that permit
programmatic use.

## Before opening a PR

```bash
.venv/bin/python tests/test_offline.py     # no network, must pass
```

Every test in `tests/test_offline.py` corresponds to a bug that actually
happened. If you fix a bug, add the test that would have caught it.

## Things that have bitten us, so you do not repeat them

- **Never name a module after a stdlib top-level module.** `http.py`, `json.py`,
  `types.py`, `select.py`, `io.py`. An MCP client runs `server.py` as a script,
  which puts the package directory on `sys.path[0]`, and the shadow breaks
  imports deep inside unrelated dependencies. There is a test for this.
- **Never print to stdout.** stdio MCP uses stdout as the protocol channel.
  Third-party loggers are silenced at import in `net.py` for the same reason.
- **A lookup returning nothing is an error, not an absence.** Record why.
  `net.LAST_ERROR` exists because a swallowed failure is indistinguishable from
  a missing record, and that distinction is the whole diagnosis.
- **Rate limits are per IP, not per process.** The throttle uses an flock'd file
  so several processes, or several people behind one institutional NAT, queue
  against one clock. arXiv answers an exceeded limit with HTTP 406, which reads
  like a malformed query; Semantic Scholar's anonymous pool answers with 429.
- **DOIs are case-insensitive by ISO 26324, but Europe PMC's DOI index is not.**
  Carry the as-published casing alongside the normalised form.
- **FTS5 matches per row.** Chunked rows lose any multi-term AND that spans a
  chunk boundary, which is why there is a document-level index too.

## Adding a source

1. New module in `scilib/sources/`, normalising to the common record shape
   (`doi, pmid, pmcid, title, year, journal, authors, abstract, is_oa, licence,
   pdf_url`).
2. Add its host and a polite interval to `_MIN_INTERVAL` in `scilib/net.py`.
3. Wire it into `lit_search` in `scilib/server.py` and document it in the
   docstring's source table.
4. Record the terms-of-use URL in a module docstring.
