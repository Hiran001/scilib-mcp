"""Execute the resolution ladder: download, verify, store, extract, index.

Every stored file records where it came from, under what licence, and its
sha256. A number quoted from a paper must be traceable to the exact bytes it was
read from, otherwise it is a rumour with a citation attached.
"""
from __future__ import annotations
import pathlib
import re
from . import config, db, extract, net, ids, resolve


# Markers that a downloaded PDF is a SUPPLEMENT, not the article. A publisher's
# "full text PDF" link can resolve to the supplementary file, and the bytes are a
# valid PDF, so every earlier check passes and the supplement is stored under the
# paper's own name. Two structural pillars were once recorded as retrieved when
# what had actually been fetched was a 2-page MD-methods supplement and a 3-page
# sequence listing. A short PDF whose first page announces itself as supplementary
# is the signal.
_SUPP_MARKERS = (
    "supplementary information", "supplementary material", "supplementary section",
    "supplementary methods", "supplementary figures", "supplementary tables",
    "supplemental information", "supplemental material", "supplemental figure",
    "supporting information", "si appendix", "extended data",
)


def looks_like_supplement(text: str, title: str = "", pages: int | None = None) -> str:
    """Return a reason string if this PDF looks like a supplement, else ""."""
    head = (text or "").lstrip()[:1200].lower()
    tl = (title or "").lower()
    for m in _SUPP_MARKERS:
        if head.startswith(m) or m in tl:
            return f"first page or PDF title announces {m!r}"
    # A real article never OPENS with the word "supplement". Caught the case
    # "Supplement 1 - Sequences used in this study", which the phrase list above
    # missed because the publisher numbered it instead of naming it.
    if re.match(r"supplement(al|ary)?\b", head):
        return "first page opens with the word 'supplement'"
    # A marker in the OPENING of page 1, not necessarily at character zero.
    # Missed a real Cell supplement whose page 1 began "Cell, Volume 171
    # Supplemental Information ...", so the marker sat at character 22 and the
    # file ran to 6 pages, escaping both the startswith test and the page-count
    # test below. An article's first 400 characters are title and author
    # territory and do not announce supplementary material.
    opening = head[:400]
    for m in _SUPP_MARKERS:
        if m in opening:
            return f"page 1 opens by announcing {m!r}"
    # A short PDF whose own metadata title carries "supplement" in any form.
    # Length is required here so a paper ABOUT dietary supplements is not caught.
    if pages is not None and pages <= 6 and re.search(r"supplement", tl):
        return f"only {pages} pages and a PDF title containing 'supplement'"
    if pages is not None and pages <= 4 and any(m in head for m in _SUPP_MARKERS):
        return f"only {pages} pages and supplementary wording on page 1"
    return ""


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
        pages, pdf_title = extract.pdf_info(path)
        supp = looks_like_supplement(text, pdf_title, pages)
        if supp:
            # Keep the bytes (they are often useful) but never let this count as
            # the article. A caller that believes it holds the paper will quote
            # from a supplement without noticing.
            db.record_file(wid, path, "pdf_supplement", source=source,
                           source_url=url, licence=licence)
            return {"path": str(path), "kind": "pdf_supplement",
                    "status": "supplement_not_article", "bytes": len(data),
                    "source": source, "source_url": url, "licence": licence,
                    "chars": len(text), "pages": pages, "reason": supp,
                    "note": "A supplementary PDF was served in place of the "
                            "article. The main text was NOT retrieved."}
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

    # Distinguish "nothing legal exists" from "automated retrieval failed but
    # legal copies are sitting right there". Reporting the second as the first
    # is wrong and sends the user to an author request they do not need.
    manual = [{"via": r["via"], "url": r["url"], "kind": r["kind"],
               "note": r.get("note", "")} for r in rs
              if r["via"] != "local" and r.get("url")]
    if manual:
        return {"status": "manual_retrieval_needed", "work_id": meta["work_id"],
                "title": meta.get("title", ""), "doi": meta.get("doi", ""),
                "pmid": meta.get("pmid", ""), "tried": tried,
                "legal_copies": manual,
                "note": f"{len(manual)} legal copy/copies exist but automated "
                        f"retrieval failed (landing pages, login walls, or a "
                        f"server error). Open one of the URLs above and save the "
                        f"PDF into the library, or use lib_index on it."}
    return {"status": "no_legal_fulltext", "work_id": meta["work_id"],
            "title": meta.get("title", ""), "doi": meta.get("doi", ""),
            "pmid": meta.get("pmid", ""), "tried": tried,
            "note": "No open copy found anywhere. The paper may still be available "
                    "through your institution's subscription, interlibrary loan, or "
                    "directly from the authors.",
            "author_request_draft": resolve.author_request(meta)}
