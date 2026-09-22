"""OpenAlex: open index of ~250M scholarly works. CC0, no key required.

Used as the primary discovery index because it is the broadest fully open
catalogue and carries open-access location and licence per work.
"""
from __future__ import annotations
from .. import net, config

BASE = "https://api.openalex.org"


def _norm(w: dict) -> dict:
    ids = w.get("ids") or {}
    loc = w.get("best_oa_location") or w.get("primary_location") or {}
    src = (loc.get("source") or {}) if isinstance(loc, dict) else {}
    inv = w.get("abstract_inverted_index")
    abstract = ""
    if inv:
        # OpenAlex ships abstracts as an inverted index for licensing reasons.
        pos = {}
        for word, idxs in inv.items():
            for i in idxs:
                pos[i] = word
        abstract = " ".join(pos[i] for i in sorted(pos))
    return {
        "source": "openalex",
        "doi": (ids.get("doi") or "").replace("https://doi.org/", ""),
        "pmid": (ids.get("pmid") or "").rsplit("/", 1)[-1],
        "pmcid": (ids.get("pmcid") or "").rsplit("/", 1)[-1],
        "title": w.get("title") or w.get("display_name") or "",
        "year": str(w.get("publication_year") or ""),
        "journal": src.get("display_name") or "",
        "authors": ", ".join(
            (a.get("author") or {}).get("display_name", "")
            for a in (w.get("authorships") or [])[:12]),
        "abstract": abstract,
        "is_oa": bool((w.get("open_access") or {}).get("is_oa")),
        "licence": loc.get("license") or "",
        "pdf_url": loc.get("pdf_url") or "",
        "landing_url": loc.get("landing_page_url") or "",
        "cited_by": w.get("cited_by_count", 0),
        "type": w.get("type", ""),
    }


def search(query: str, limit: int = 20, year_from: str = "", year_to: str = "") -> list[dict]:
    params = {"search": query, "per-page": min(limit, 50)}
    f = []
    if year_from:
        f.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        f.append(f"to_publication_date:{year_to}-12-31")
    if f:
        params["filter"] = ",".join(f)
    if (c := config.contact()):
        params["mailto"] = c
    j = net.get_json(f"{BASE}/works", params, kind="search:openalex")
    return [_norm(w) for w in (j.get("results") or [])]


def by_id(kind: str, value: str) -> dict | None:
    key = {"doi": f"doi:{value}", "pmid": f"pmid:{value}",
           "pmcid": f"pmcid:{value}"}.get(kind)
    if not key:
        return None
    params = {"mailto": config.contact()} if config.contact() else {}
    j = net.get_json(f"{BASE}/works/{key}", params, kind="lookup:openalex")
    return _norm(j) if j and not j.get("__error__") else None


def cited_by(doi: str, limit: int = 25) -> list[dict]:
    """Papers citing this one.

    The route that finds the paper you did not know to search for. A keyword
    search only returns answers to questions you already thought to ask; a
    citation edge does not have that limitation.
    """
    w = by_id("doi", doi)
    if not w:
        return []
    params = {"filter": f"cites:doi:{doi}", "per-page": min(limit, 50)}
    if (c := config.contact()):
        params["mailto"] = c
    j = net.get_json(f"{BASE}/works", params, kind="cites:openalex")
    return [_norm(x) for x in (j.get("results") or [])]


def references(doi: str, limit: int = 60) -> list[dict]:
    params = {"mailto": config.contact()} if config.contact() else {}
    j = net.get_json(f"{BASE}/works/doi:{doi}", params, kind="refs:openalex")
    out = []
    for url in (j.get("referenced_works") or [])[:limit]:
        wid = url.rsplit("/", 1)[-1]
        r = net.get_json(f"{BASE}/works/{wid}", params, kind="refs:openalex")
        if r and not r.get("__error__"):
            out.append(_norm(r))
    return out
