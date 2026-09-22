"""Repository and aggregator sources: Semantic Scholar, CORE, DOAJ, OpenAIRE.

These cover the layer the publisher-centric sources miss. A paywalled paper very
often has a legal author manuscript in an institutional repository, deposited
under a funder mandate. Unpaywall indexes many of them; CORE and OpenAIRE index
far more, and Semantic Scholar carries its own open-PDF pointer plus the
citation graph.

All four are free. CORE and Semantic Scholar accept an optional free API key
that only raises the rate limit.
"""
from __future__ import annotations
from .. import net, config


# ----------------------------------------------------------- Semantic Scholar

S2 = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = ("title,abstract,year,venue,externalIds,openAccessPdf,"
             "citationCount,authors.name,publicationTypes,tldr")


def _s2_norm(p: dict) -> dict:
    ext = p.get("externalIds") or {}
    oa = p.get("openAccessPdf") or {}
    return {
        "source": "semanticscholar",
        "doi": (ext.get("DOI") or "").lower(),
        "pmid": str(ext.get("PubMed") or ""),
        "pmcid": (f"PMC{ext['PubMedCentral']}" if ext.get("PubMedCentral") else ""),
        "arxiv": ext.get("ArXiv") or "",
        "title": p.get("title") or "",
        "year": str(p.get("year") or ""),
        "journal": p.get("venue") or "",
        "authors": ", ".join(a.get("name", "") for a in (p.get("authors") or [])[:12]),
        "abstract": p.get("abstract") or "",
        "cited_by": p.get("citationCount", 0),
        "pdf_url": oa.get("url") or "",
        "licence": oa.get("license") or "",
        "is_oa": bool(oa.get("url")),
        # S2's one-sentence machine summary. Useful for triage, never a
        # substitute for reading; it is model-generated, not author text.
        "tldr": ((p.get("tldr") or {}).get("text") or ""),
    }


def _s2_headers() -> dict:
    k = config.CFG.get("s2_api_key")
    return {"x-api-key": k} if k else {}


def s2_search(query: str, limit: int = 15) -> list[dict]:
    j = net.get_json(f"{S2}/paper/search",
                     {"query": query, "limit": min(limit, 100), "fields": S2_FIELDS},
                     kind="search:semanticscholar")
    return [_s2_norm(p) for p in (j.get("data") or [])]


def s2_by_id(identifier: str) -> dict | None:
    """identifier may be DOI:10.x, PMID:123, arXiv:1234.5678 or an S2 id."""
    j = net.get_json(f"{S2}/paper/{identifier}", {"fields": S2_FIELDS},
                     kind="lookup:semanticscholar")
    return None if j.get("__error__") else _s2_norm(j)


def s2_references(identifier: str, limit: int = 60) -> list[dict]:
    """The bibliography of a paper, resolved. Reading a paper's reference list
    is how you find the work nobody cites in your subfield."""
    j = net.get_json(f"{S2}/paper/{identifier}/references",
                     {"limit": min(limit, 100), "fields": S2_FIELDS},
                     kind="refs:semanticscholar")
    return [_s2_norm(d["citedPaper"]) for d in (j.get("data") or []) if d.get("citedPaper")]


# ------------------------------------------------------------------- CORE

def core_search(query: str, limit: int = 15) -> list[dict]:
    """CORE aggregates ~300M records from open repositories worldwide.

    The best single source for a green-OA author manuscript of a paywalled
    paper. Without an API key the public endpoint is heavily rate-limited; a
    free key from core.ac.uk raises it considerably.
    """
    key = config.CFG.get("core_api_key")
    if not key:
        return [{"source": "core", "__note__":
                 "CORE needs a free API key. Get one at https://core.ac.uk/services/api "
                 "then: configure(core_api_key='...'). Skipped for now."}]
    j = net.get_json("https://api.core.ac.uk/v3/search/works",
                     {"q": query, "limit": min(limit, 50)},
                     kind="search:core")
    out = []
    for r in (j.get("results") or []):
        out.append({
            "source": "core",
            "doi": (r.get("doi") or "").lower(),
            "title": r.get("title") or "",
            "year": str(r.get("yearPublished") or ""),
            "journal": (r.get("publisher") or ""),
            "authors": ", ".join(a.get("name", "") for a in (r.get("authors") or [])[:10]),
            "abstract": (r.get("abstract") or "")[:2000],
            "pdf_url": r.get("downloadUrl") or "",
            "repository": ((r.get("dataProviders") or [{}])[0] or {}).get("name", ""),
            "is_oa": True,
            "licence": r.get("license") or "",
        })
    return out


# ------------------------------------------------------------------- DOAJ

def doaj_search(query: str, limit: int = 15) -> list[dict]:
    """Directory of Open Access Journals: articles in fully open journals.

    Everything here is open by definition, which makes it a good cross-check
    when another source reports a paper as closed.
    """
    import urllib.parse as u
    j = net.get_json(
        f"https://doaj.org/api/search/articles/{u.quote(query, safe='')}",
        {"pageSize": min(limit, 50)}, kind="search:doaj")
    out = []
    for r in (j.get("results") or []):
        b = r.get("bibjson") or {}
        ids = {i.get("type"): i.get("id") for i in (b.get("identifier") or [])}
        links = [l.get("url") for l in (b.get("link") or []) if l.get("type") == "fulltext"]
        out.append({
            "source": "doaj",
            "doi": (ids.get("doi") or "").lower(),
            "pmid": ids.get("pmid") or "",
            "title": b.get("title") or "",
            "year": str(b.get("year") or ""),
            "journal": ((b.get("journal") or {}).get("title") or ""),
            "authors": ", ".join(a.get("name", "") for a in (b.get("author") or [])[:10]),
            "abstract": (b.get("abstract") or "")[:2000],
            "landing_url": links[0] if links else "",
            "is_oa": True,
            "licence": "; ".join(l.get("type", "") for l in
                                 ((b.get("journal") or {}).get("license") or [])),
        })
    return out


# ---------------------------------------------------------------- OpenAIRE

def openaire_search(query: str, limit: int = 15) -> list[dict]:
    """OpenAIRE aggregates European repositories and funder outputs.

    Strong where a paper is EU-funded and the accepted manuscript sits in a
    national repository that no publisher-side index knows about.
    """
    j = net.get_json("https://api.openaire.eu/search/publications",
                     {"title": query, "size": min(limit, 50), "format": "json"},
                     kind="search:openaire")
    results = (((j.get("response") or {}).get("results") or {}).get("result") or [])
    out = []
    for r in results:
        try:
            md = r["metadata"]["oaf:entity"]["oaf:result"]
        except (KeyError, TypeError):
            continue
        def first(v):
            if isinstance(v, list):
                v = v[0] if v else {}
            return v.get("$", "") if isinstance(v, dict) else (v or "")
        pids = md.get("pid") or []
        pids = pids if isinstance(pids, list) else [pids]
        doi = next((p.get("$", "") for p in pids
                    if isinstance(p, dict) and p.get("@classid") == "doi"), "")
        out.append({
            "source": "openaire",
            "doi": doi.lower(),
            "title": first(md.get("title")),
            "year": str(first(md.get("dateofacceptance")))[:4],
            "journal": first(md.get("journal")),
            "abstract": first(md.get("description"))[:2000],
            "is_oa": first(md.get("bestaccessright")) .lower().startswith("open")
                     if md.get("bestaccessright") else False,
            "licence": "",
        })
    return out
