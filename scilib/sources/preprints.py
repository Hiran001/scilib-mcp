"""Preprint servers: bioRxiv, medRxiv, arXiv.

Often the highest-value route for a paywalled paper. The preprint is usually the
same science, is legally free, and for a Methods detail is almost always
sufficient. Worth checking before concluding a paper is unreachable.
"""
from __future__ import annotations
import re
from .. import net


def biorxiv_by_doi(doi: str, server: str = "biorxiv") -> dict | None:
    j = net.get_json(f"https://api.biorxiv.org/details/{server}/{doi}",
                      kind="lookup:biorxiv")
    coll = j.get("collection") or []
    if not coll:
        return None
    r = coll[-1]                                     # latest version
    return {
        "source": server,
        "doi": (r.get("doi") or "").lower(),
        "title": r.get("title") or "",
        "authors": r.get("authors") or "",
        "year": (r.get("date") or "")[:4],
        "date": r.get("date") or "",
        "version": r.get("version") or "",
        "category": r.get("category") or "",
        "abstract": r.get("abstract") or "",
        "published_doi": (r.get("published") or "").lower()
                         if (r.get("published") or "NA") != "NA" else "",
        "pdf_url": f"https://www.{server}.org/content/{r.get('doi')}v{r.get('version')}.full.pdf",
        "licence": r.get("license") or "",
        "is_oa": True,
    }


def find_preprint_of(doi: str, title: str = "") -> dict | None:
    """Given a published DOI, look for the preprint version on either server."""
    for server in ("biorxiv", "medrxiv"):
        # bioRxiv's /pubs/ endpoint maps published DOI -> preprint DOI.
        j = net.get_json(f"https://api.biorxiv.org/pubs/{server}/{doi}",
                          kind="pubs:biorxiv")
        for r in (j.get("collection") or []):
            if r.get("biorxiv_doi"):
                return biorxiv_by_doi(r["biorxiv_doi"], server)
    return None


def arxiv(query: str = "", arxiv_id: str = "", limit: int = 10) -> list[dict]:
    # arXiv answers a rate-limit with HTTP 406, not 429. That is easy to
    # misdiagnose as a malformed query: an A/B of URL encodings appeared to
    # implicate the percent-encoded colon, but with >=3 s spacing every encoding
    # returns 200. The real requirement is the documented one request per three
    # seconds, enforced in http.py, plus the backoff there. The URL is still
    # built by hand simply to keep it readable in the audit log.
    import urllib.parse as _u
    if arxiv_id:
        qs = f"id_list={_u.quote(arxiv_id)}&max_results={min(limit, 50)}"
    else:
        qs = (f"search_query=all:{_u.quote(query, safe='')}"
              f"&max_results={min(limit, 50)}&sortBy=relevance")
    raw = net.get(f"https://export.arxiv.org/api/query?{qs}", kind="search:arxiv")
    if not raw:
        return []
    txt = raw.decode("utf-8", "replace")
    out = []
    for entry in re.findall(r"<entry>(.*?)</entry>", txt, re.S):
        g = lambda tag: (re.search(rf"<{tag}>(.*?)</{tag}>", entry, re.S) or [None, ""])[1]
        aid = (g("id") or "").rsplit("/", 1)[-1]
        out.append({
            "source": "arxiv",
            "arxiv": aid,
            "title": " ".join((g("title") or "").split()),
            "abstract": " ".join((g("summary") or "").split()),
            "year": (g("published") or "")[:4],
            "authors": ", ".join(re.findall(r"<name>([^<]+)</name>", entry))[:400],
            "pdf_url": f"https://arxiv.org/pdf/{aid}",
            "doi": (g("arxiv:doi") or "").lower(),
            "is_oa": True, "licence": "arXiv non-exclusive",
        })
    return out
