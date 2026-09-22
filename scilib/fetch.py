"""Execute the resolution ladder: download, verify, store, extract, index.

Every stored file records where it came from, under what licence, and its
sha256. A number quoted from a paper must be traceable to the exact bytes it was
read from, otherwise it is a rumour with a citation attached.
"""
from __future__ import annotations
import pathlib
from . import config, db, extract, net, ids, resolve


def _store(meta: dict, data: bytes, kind: str, source: str, url: str,
           licence: str) -> dict:
    lib = config.library()
    ident = f"PMID{meta['pmid']}" if meta.get("pmid") else (
        meta.get("pmcid") or ids.norm_doi(meta.get("doi", "")).replace("/", "_"))
    ext = {"pdf": "pdf", "xml": "xml", "txt": "txt"}.get(kind, "bin")
    sub = "pdf" if kind == "pdf" else "fulltext"
    path = lib / sub / ids.slug(meta.get("title", ""), meta.get("year", ""), ident, ext)
    path.write_bytes(data)

    wid = db.upsert_work(meta)
    db.record_file(wid, path, kind, source=source, source_url=url, licence=licence)

    # Extract text for the index. XML keeps sections; PDF does not.
    if kind == "xml":
        parsed = extract.jats_sections(data)
        text = extract.to_plain(parsed) if parsed else ""
        nsec = len(parsed.get("sections", [])) if parsed else 0
    elif kind == "pdf":
        text, nsec = extract.pdf_to_text(path), 0
    else:
        text, nsec = data.decode("utf-8", "replace"), 0

    n = db.index_text(wid, path, text) if text.strip() else 0
    return {"path": str(path), "kind": kind, "bytes": len(data), "source": source,
            "source_url": url, "licence": licence, "chars": len(text),
            "sections": nsec, "chunks": n, "work_id": wid}


def fetch(query: str, prefer_pdf: bool = False) -> dict:
    """Resolve and retrieve the best legal full text available."""
    meta = resolve.identify(query)
    if not meta.get("found"):
        return meta

    existing = db.files_for(meta["work_id"])
    if existing:
        return {"status": "already_held", "work_id": meta["work_id"],
                "title": meta.get("title", ""),
                "files": [{"path": f["path"], "kind": f["kind"],
                           "licence": f["licence"], "source": f["source"]}
                          for f in existing]}

    rs = resolve.routes(meta)
    if prefer_pdf:
        rs.sort(key=lambda r: 0 if r["kind"] == "pdf" else 1)

    tried = []
    maxb = int(config.CFG.get("max_pdf_mb", 80)) * 1024 * 1024
    for r in rs:
        if r["via"] == "local":
            continue
        data = net.get(r["url"], kind=f"fetch:{r['via']}")
        if not data:
            tried.append({"via": r["via"], "result": net.LAST_ERROR.get("error", "no data")})
            continue
        if len(data) > maxb:
            tried.append({"via": r["via"], "result": f"oversize {len(data)//1048576} MB"})
            continue
        got = extract.sniff(data)
        # A route that promises a PDF and returns HTML is a login wall, not a
        # paper. Storing it would quietly poison the library.
        if got == "html":
            tried.append({"via": r["via"], "result": "HTML (login/landing page), not a document"})
            continue
        if r["kind"] == "pdf" and got != "pdf":
            tried.append({"via": r["via"], "result": f"expected pdf, got {got}"})
            continue
        rec = _store(meta, data, got, r["via"], r["url"], r.get("licence", ""))
        rec["status"] = "fetched"
        rec["title"] = meta.get("title", "")
        rec["tried"] = tried
        return rec

    return {"status": "no_legal_fulltext", "work_id": meta["work_id"],
            "title": meta.get("title", ""), "doi": meta.get("doi", ""),
            "pmid": meta.get("pmid", ""), "tried": tried,
            "note": "No open copy found. The paper may still be available through "
                    "your institution's subscription, interlibrary loan, or directly "
                    "from the authors.",
            "author_request_draft": resolve.author_request(meta)}
