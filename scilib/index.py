"""Index PDFs and text already on disk.

This is the half that has nothing to do with access. Papers sitting unread in a
project folder are invisible to every search that matters, and a keyword sweep
over filenames only answers questions already thought of.
"""
from __future__ import annotations
import pathlib, re
from . import config, db, extract, ids

SKIP_DIRS = {"__pycache__", "node_modules", ".git", "site-packages", ".venv",
             "mpl-data", "env", "envs"}


def _guess_meta(path: pathlib.Path, text: str) -> dict:
    """Recover identifiers from the filename and the first page.

    Filenames in this user's existing libraries already carry PMIDs
    (`<year>_<title>_PMID<n>.pdf`), so that convention is honoured first.
    """
    name = path.stem
    pmid = ""
    if (m := re.search(r"PMID(\d{7,8})", name, re.I)):
        pmid = m.group(1)
    # Look in the head AND the tail. Publishers put the DOI in a first-page
    # footer, a running header, or the final page of the PDF; searching only the
    # first 4000 characters left 63% of a real library with no resolved
    # identity, which makes every coverage report wrong.
    head, tail = text[:9000], text[-6000:]
    doi = ""
    for zone in (head, tail):
        for m in ids.DOI_RE.finditer(zone):
            cand = ids.norm_doi(m.group(1))
            # Skip DOIs that belong to cited references rather than this paper.
            # A reference-list DOI is usually preceded by a citation marker.
            if cand and not re.search(r"(?:\[\d+\]|\(\d{4}\))\s*$",
                                      zone[:m.start()][-40:]):
                doi = cand
                break
        if doi:
            break
    if not doi:
        for zone in (head, tail):
            if (m := re.search(r"doi\.org/(10\.\d{4,9}/[^\s\"<>]+)", zone, re.I)):
                doi = ids.norm_doi(m.group(1))
                break
    if not pmid:
        for zone in (head, tail):
            if (m := re.search(r"PMID:?\s*(\d{7,8})", zone, re.I)):
                pmid = m.group(1)
                break
    year = ""
    if (m := re.match(r"(\d{4})_", name)):
        year = m.group(1)
    elif (m := re.search(r"\b(19[89]\d|20[0-4]\d)\b", head)):
        year = m.group(1)
    title = re.sub(r"^\d{4}_", "", name)
    title = re.sub(r"_PMID\d+$", "", title).replace("_", " ").strip()
    first = [ln.strip() for ln in text.splitlines() if len(ln.strip()) > 25][:1]
    return {"doi": doi, "pmid": pmid, "year": year,
            "title": (first[0][:300] if first else title) or title,
            "meta": {"indexed_from": str(path)}}


def index_path(root: pathlib.Path, recursive: bool = True,
               limit: int | None = None) -> dict:
    """Index every PDF/XML/TXT under root. Returns real counts, not a sample.

    No cap is applied unless the caller passes one explicitly, and when they do
    the result says so, because a capped scan reported as a total is how an
    inventory lies.
    """
    root = pathlib.Path(root).expanduser()
    if not root.exists():
        return {"error": f"no such path: {root}"}
    pat = "**/*" if recursive else "*"
    files = [p for p in root.glob(pat)
             if p.is_file() and p.suffix.lower() in (".pdf", ".xml", ".txt", ".nxml")
             and not (SKIP_DIRS & set(p.parts))]
    files.sort()
    total_found = len(files)
    capped = limit is not None and total_found > limit
    if limit is not None:
        files = files[:limit]

    done = skipped = failed = 0
    failures: list[dict] = []
    for p in files:
        try:
            already = db.con().execute(
                "SELECT n_chunks FROM files WHERE path=?", (str(p),)).fetchone()
            if already and already[0]:
                skipped += 1
                continue
            data = p.read_bytes()
            kind = extract.sniff(data)
            if kind == "pdf":
                text = extract.pdf_to_text(p)
            elif kind == "xml":
                parsed = extract.jats_sections(data)
                text = extract.to_plain(parsed) if parsed else data.decode("utf-8", "replace")
            else:
                text = data.decode("utf-8", "replace")
            if not text.strip():
                failed += 1
                # A PDF yielding no text is almost always a scan without OCR.
                # Recorded by name, because "1 failed" with no path is not a
                # result anyone can act on.
                failures.append({"path": str(p), "reason":
                                 "no extractable text (likely a scan without OCR)"})
                continue
            meta = _guess_meta(p, text)
            wid = db.upsert_work(meta)
            db.record_file(wid, p, kind, source="local", source_url=f"file://{p}",
                           licence="(local file, licence not recorded)")
            db.index_text(wid, p, text)
            done += 1
        except Exception as e:
            failed += 1
            failures.append({"path": str(p), "reason": f"{type(e).__name__}: {e}"[:200]})
    return {"root": str(root), "files_found": total_found,
            "indexed": done, "already_indexed": skipped, "failed": failed,
            "failures": failures, "capped": capped,
            "note": (f"LIMIT APPLIED: {limit} of {total_found} files. This is a "
                     f"lower bound, not a total." if capped else
                     "uncapped: every matching file under root was considered")}
