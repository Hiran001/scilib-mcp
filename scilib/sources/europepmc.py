"""Europe PMC: ~45M abstracts, ~6M open-access full texts as JATS XML.

The single most valuable source here, because `fullTextXML` returns the article
with its section structure intact. That is what makes "read the Methods" a real
operation rather than a grep over a flat blob, and it is why a buffer condition
buried in a methods paragraph is findable at all.

API terms: https://europepmc.org/RestfulWebService
"""
from __future__ import annotations
from .. import net, config

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def _norm(r: dict) -> dict:
    return {
        "source": "europepmc",
        "doi": (r.get("doi") or "").lower(),
        "pmid": r.get("pmid") or "",
        "pmcid": r.get("pmcid") or "",
        "title": r.get("title") or "",
        "year": str(r.get("pubYear") or ""),
        "journal": ((r.get("journalInfo") or {}).get("journal") or {}).get("title", ""),
        "authors": r.get("authorString") or "",
        "abstract": r.get("abstractText") or "",
        "is_oa": r.get("isOpenAccess") == "Y",
        "licence": r.get("license") or "",
        "has_fulltext": r.get("hasTextMinedTerms") == "Y" or r.get("inEPMC") == "Y",
        "in_epmc": r.get("inEPMC") == "Y",
        "cited_by": r.get("citedByCount", 0),
    }


def search(query: str, limit: int = 20, open_only: bool = False) -> list[dict]:
    q = query + (" AND OPEN_ACCESS:Y" if open_only else "")
    j = net.get_json(f"{BASE}/search", {
        "query": q, "format": "json", "resultType": "core",
        "pageSize": min(limit, 100)}, kind="search:europepmc")
    return [_norm(r) for r in ((j.get("resultList") or {}).get("result") or [])]


def by_ids(pmids: list[str]) -> dict[str, dict]:
    """Batch resolve. Europe PMC accepts OR-ed external IDs, so 20 PMIDs cost
    one request instead of twenty."""
    out: dict[str, dict] = {}
    for k in range(0, len(pmids), 20):
        chunk = pmids[k:k + 20]
        q = " OR ".join(f"EXT_ID:{p}" for p in chunk) + " AND SRC:MED"
        j = net.get_json(f"{BASE}/search", {
            "query": q, "format": "json", "resultType": "core", "pageSize": 100},
            kind="lookup:europepmc")
        for r in ((j.get("resultList") or {}).get("result") or []):
            out[r.get("pmid") or ""] = _norm(r)
    return out


def _doi_query(doi: str) -> dict | None:
    j = net.get_json(f"{BASE}/search", {
        "query": f'DOI:"{doi}"', "format": "json", "resultType": "core",
        "pageSize": 1}, kind="lookup:europepmc")
    rs = (j.get("resultList") or {}).get("result") or []
    return _norm(rs[0]) if rs else None


def by_doi(doi: str, doi_raw: str = "") -> dict | None:
    """Resolve a DOI, tolerating Europe PMC's case-sensitive DOI index.

    DOIs are case-insensitive by specification (ISO 26324) and every normaliser
    lowercases them, but Europe PMC's DOI field does an exact match. So
    `10.7554/elife.85579` returns nothing while `10.7554/eLife.85579` returns
    the paper. Left unhandled this silently loses full text for every journal
    with capitals in its DOI: eLife, PLoS, mBio, JBC and many more.

    Tries the as-published casing first, then the normalised form, then falls
    back to a title-free PMID route via the caller. Returns None only when the
    record genuinely is not in Europe PMC.
    """
    for candidate in [c for c in (doi_raw, doi) if c]:
        if (r := _doi_query(candidate)):
            return r
    return None


def fulltext_xml(pmcid: str) -> bytes | None:
    """JATS XML for an open-access article. Returns None when not in the OA set."""
    if not pmcid:
        return None
    data = net.get(f"{BASE}/{pmcid}/fullTextXML", kind="fulltext:europepmc")
    if data and data.lstrip()[:5] in (b"<?xml", b"<!DOC", b"<arti"):
        return data
    return None


def supplementary_files(pmcid: str) -> bytes | None:
    """Zip of supplementary material, where deposited."""
    if not pmcid:
        return None
    return net.get(f"{BASE}/{pmcid}/supplementaryFiles", kind="suppl:europepmc")
