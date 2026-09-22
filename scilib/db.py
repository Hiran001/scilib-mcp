"""Local store: canonical works, files on disk with provenance, and a full-text
index.

The index is the point of this module. The most expensive retrieval failures in
practice are not paywalls, they are papers already on disk that nobody searched.
A keyword sweep over titles and abstracts only returns answers to questions you
already thought to ask; an index over full text lets you find the sentence in a
Methods section you did not know to look for.

Provenance is recorded per file because a number quoted from a paper must be
traceable to the exact bytes it came from: source URL, licence, sha256, date.
"""
from __future__ import annotations
import hashlib, json, pathlib, sqlite3, time
from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
  work_id    TEXT PRIMARY KEY,      -- canonical: doi, else pmid:N, else sha1 of title
  doi        TEXT, pmid TEXT, pmcid TEXT, arxiv TEXT,
  title      TEXT, authors TEXT, journal TEXT, year TEXT,
  abstract   TEXT, licence TEXT, is_oa INTEGER DEFAULT 0,
  meta_json  TEXT, added_at REAL
);
CREATE INDEX IF NOT EXISTS ix_works_doi   ON works(doi);
CREATE INDEX IF NOT EXISTS ix_works_pmid  ON works(pmid);
CREATE INDEX IF NOT EXISTS ix_works_pmcid ON works(pmcid);

CREATE TABLE IF NOT EXISTS files (
  file_id   INTEGER PRIMARY KEY,
  work_id   TEXT, path TEXT UNIQUE, kind TEXT,      -- pdf | jats | txt
  sha256    TEXT, bytes INTEGER,
  source    TEXT, source_url TEXT, licence TEXT,    -- provenance
  doc_type  TEXT DEFAULT '',                        -- paper|preprint|supplementary|own_work|other
  fetched_at REAL, indexed_at REAL, n_chunks INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_files_work ON files(work_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
  body, work_id UNINDEXED, path UNINDEXED, loc UNINDEXED,
  tokenize = 'porter unicode61'
);

-- Document-level mirror of the same text.
-- FTS5 matches per ROW, so with only the chunk table a query like
-- "heptad AND register" fails whenever the two terms fall in different
-- 1800-character chunks, even though both are in the paper. That is a silent
-- recall loss on exactly the multi-concept queries worth asking. The chunk
-- table gives precise snippets; this one gives document-level recall.
CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(
  body, work_id UNINDEXED, path UNINDEXED,
  tokenize = 'porter unicode61'
);
"""


def connect() -> sqlite3.Connection:
    p = config.library() / "scilib.db"
    con = sqlite3.connect(p, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(files)")}
    if "doc_type" not in cols:
        con.execute("ALTER TABLE files ADD COLUMN doc_type TEXT DEFAULT ''")
        con.commit()
    return con


_con: sqlite3.Connection | None = None


def con() -> sqlite3.Connection:
    global _con
    if _con is None:
        _con = connect()
    return _con


def work_id(doi: str = "", pmid: str = "", title: str = "") -> str:
    if doi:
        return doi.lower()
    if pmid:
        return f"pmid:{pmid}"
    return "title:" + hashlib.sha1((title or "").lower().encode()).hexdigest()[:16]


def upsert_work(rec: dict) -> str:
    """Insert or merge a work record. Merging never overwrites a present field
    with an empty one, so a sparse record from one source cannot erase a
    richer one from another."""
    wid = rec.get("work_id") or work_id(rec.get("doi", ""), rec.get("pmid", ""),
                                        rec.get("title", ""))
    c = con()
    cur = c.execute("SELECT * FROM works WHERE work_id=?", (wid,)).fetchone()
    fields = ["doi", "pmid", "pmcid", "arxiv", "title", "authors", "journal",
              "year", "abstract", "licence"]
    if cur:
        merged = {f: (rec.get(f) or cur[f] or "") for f in fields}
        merged["is_oa"] = int(bool(rec.get("is_oa") or cur["is_oa"]))
        meta = json.loads(cur["meta_json"] or "{}")
        meta.update(rec.get("meta", {}))
    else:
        merged = {f: (rec.get(f) or "") for f in fields}
        merged["is_oa"] = int(bool(rec.get("is_oa")))
        meta = rec.get("meta", {})
    c.execute(
        """INSERT INTO works (work_id,doi,pmid,pmcid,arxiv,title,authors,journal,
                              year,abstract,licence,is_oa,meta_json,added_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(work_id) DO UPDATE SET
             doi=excluded.doi, pmid=excluded.pmid, pmcid=excluded.pmcid,
             arxiv=excluded.arxiv, title=excluded.title, authors=excluded.authors,
             journal=excluded.journal, year=excluded.year,
             abstract=excluded.abstract, licence=excluded.licence,
             is_oa=excluded.is_oa, meta_json=excluded.meta_json""",
        (wid, merged["doi"], merged["pmid"], merged["pmcid"], merged["arxiv"],
         merged["title"], merged["authors"], merged["journal"], merged["year"],
         merged["abstract"], merged["licence"], merged["is_oa"],
         json.dumps(meta), time.time()))
    c.commit()
    return wid


def get_work(wid: str) -> dict | None:
    r = con().execute("SELECT * FROM works WHERE work_id=?", (wid,)).fetchone()
    return dict(r) if r else None


def find_work(doi: str = "", pmid: str = "", pmcid: str = "") -> dict | None:
    for col, val in (("doi", doi), ("pmid", pmid), ("pmcid", pmcid)):
        if val:
            r = con().execute(
                f"SELECT * FROM works WHERE {col}=? COLLATE NOCASE", (val,)).fetchone()
            if r:
                return dict(r)
    return None


def set_doc_type(path: pathlib.Path, doc_type: str) -> None:
    """Tag a file's document type. A personal library mixes published papers
    with the owner's own posters, protocols and instrument data; counting those
    as literature makes every coverage figure wrong."""
    c = con()
    c.execute("UPDATE files SET doc_type=? WHERE path=?", (doc_type, str(path)))
    c.commit()


def record_file(work_id_: str, path: pathlib.Path, kind: str, *, source: str = "",
                source_url: str = "", licence: str = "") -> int:
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    c = con()
    c.execute(
        """INSERT INTO files (work_id,path,kind,sha256,bytes,source,source_url,
                              licence,fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(path) DO UPDATE SET
             work_id=excluded.work_id, kind=excluded.kind, sha256=excluded.sha256,
             bytes=excluded.bytes, source=excluded.source,
             source_url=excluded.source_url, licence=excluded.licence""",
        (work_id_, str(path), kind, sha, len(data), source, source_url,
         licence, time.time()))
    c.commit()
    return c.execute("SELECT file_id FROM files WHERE path=?", (str(path),)).fetchone()[0]


def files_for(work_id_: str) -> list[dict]:
    return [dict(r) for r in con().execute(
        "SELECT * FROM files WHERE work_id=? ORDER BY kind", (work_id_,))]


def index_text(work_id_: str, path: pathlib.Path, text: str,
               chunk_chars: int = 1800) -> int:
    """Chunk and index. Chunks overlap by a sentence so a hit spanning a
    boundary is still findable, and each carries a locator so a result can be
    pointed at rather than merely asserted."""
    c = con()
    c.execute("DELETE FROM chunks WHERE path=?", (str(path),))
    c.execute("DELETE FROM docs WHERE path=?", (str(path),))
    c.execute("INSERT INTO docs (body, work_id, path) VALUES (?,?,?)",
              (text, work_id_, str(path)))
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    buf, loc, n = [], 1, 0
    size = 0
    for p in paras:
        buf.append(p)
        size += len(p)
        if size >= chunk_chars:
            body = "\n\n".join(buf)
            c.execute("INSERT INTO chunks (body,work_id,path,loc) VALUES (?,?,?,?)",
                      (body, work_id_, str(path), f"chunk {loc}"))
            n += 1
            loc += 1
            buf = buf[-1:]                      # one-paragraph overlap
            size = len(buf[0]) if buf else 0
    if buf:
        c.execute("INSERT INTO chunks (body,work_id,path,loc) VALUES (?,?,?,?)",
                  ("\n\n".join(buf), work_id_, str(path), f"chunk {loc}"))
        n += 1
    c.execute("UPDATE files SET indexed_at=?, n_chunks=? WHERE path=?",
              (time.time(), n, str(path)))
    c.commit()
    return n


_DOC_SQL = """SELECT d.work_id, d.path, bm25(docs) AS score
              FROM docs d WHERE docs MATCH ?
              ORDER BY score LIMIT ?"""

_FTS_SQL = """SELECT c.work_id, c.path, c.loc,
                     snippet(chunks, 0, '>>>', '<<<', ' ... ', 24) AS snip,
                     bm25(chunks) AS score
              FROM chunks c WHERE chunks MATCH ?
              ORDER BY score LIMIT ?"""


def _fts_fallback(query: str) -> str:
    """Rewrite a query FTS5 refused into one it accepts.

    FTS5 has its own grammar: NEAR(a b, 8) not NEAR/8, and bare punctuation is a
    syntax error. A person typing a natural phrase should get results, not a
    stack trace, so quote every bare token and AND them together. Quoting also
    neutralises operator words that were meant literally.
    """
    import re as _re
    toks = _re.findall(r"[A-Za-z0-9][A-Za-z0-9_.\-]*", query)
    toks = [t for t in toks if t.upper() not in {"AND", "OR", "NOT", "NEAR"}]
    return " AND ".join(f'"{t}"' for t in toks) if toks else ""


def search(query: str, limit: int = 20) -> list[dict]:
    """Full-text search with snippets. Ranked by bm25, best first.

    A query FTS5 cannot parse is retried in a quoted form rather than raising,
    because the caller is usually a person or a model writing prose, not FTS5
    grammar.
    """
    def _run(sql, q):
        try:
            return con().execute(sql, (q, limit)).fetchall()
        except sqlite3.OperationalError:
            fb = _fts_fallback(q)
            if not fb:
                return []
            try:
                return con().execute(sql, (fb, limit)).fetchall()
            except sqlite3.OperationalError:
                return []

    rows = _run(_FTS_SQL, query)
    if not rows:
        # Fall back to document scope. A term pair split across two chunks is a
        # real hit that chunk-scoped matching cannot see.
        doc_rows = _run(_DOC_SQL, query)
        rows = []
        for d in doc_rows:
            # Re-derive a snippet from the best chunk of that document for any
            # single term of the query, so the result is still pointable.
            terms = [t for t in _fts_fallback(query).split(" AND ") if t]
            snip, loc = "", "document"
            for t in terms:
                try:
                    hit = con().execute(
                        """SELECT loc, snippet(chunks,0,'>>>','<<<',' ... ',24)
                           FROM chunks WHERE path=? AND chunks MATCH ? LIMIT 1""",
                        (d["path"], t)).fetchone()
                except sqlite3.OperationalError:
                    hit = None
                if hit:
                    loc, snip = hit[0], hit[1]
                    break
            rows.append({"work_id": d["work_id"], "path": d["path"], "loc": loc,
                         "snip": snip or "(match spans the document; terms appear "
                                         "in different sections)",
                         "score": d["score"]})
    out = []
    for r in rows:
        w = get_work(r["work_id"]) or {}
        dt = con().execute("SELECT doc_type FROM files WHERE path=?",
                           (r["path"],)).fetchone()
        out.append({"doc_type": (dt[0] if dt else "") or "",
                    "work_id": r["work_id"], "title": w.get("title", ""),
                    "year": w.get("year", ""), "journal": w.get("journal", ""),
                    "doi": w.get("doi", ""), "pmid": w.get("pmid", ""),
                    "path": r["path"], "loc": r["loc"],
                    "snippet": r["snip"], "score": round(r["score"], 3)})
    return out


def stats() -> dict:
    c = con()
    q = lambda s: c.execute(s).fetchone()[0]
    return {
        "works": q("SELECT count(*) FROM works"),
        "files": q("SELECT count(*) FROM files"),
        "indexed_files": q("SELECT count(*) FROM files WHERE n_chunks>0"),
        "chunks": q("SELECT count(*) FROM chunks"),
        "with_fulltext": q("SELECT count(DISTINCT work_id) FROM files WHERE n_chunks>0"),
        "library": str(config.library()),
        "by_doc_type": {row[0] or "(untyped)": row[1] for row in c.execute(
            "SELECT doc_type, count(*) FROM files GROUP BY doc_type ORDER BY 2 DESC")},
    }


def purge_index() -> dict:
    """Drop every file and index row, keeping work metadata.

    Needed because deleting `files` alone leaves orphaned rows in `chunks` and
    `docs`, which then report a chunk count for files that are no longer known.
    """
    c = con()
    before = stats()
    for t in ("chunks", "docs", "files"):
        c.execute(f"DELETE FROM {t}")
    c.commit()
    return {"before": before, "after": stats()}
