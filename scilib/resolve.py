"""Given an identifier, find the paper and every legal route to its full text.

The ladder is ordered by fidelity first and licence clarity second, not by
convenience:

  1. already on disk                  no network, already provenance-stamped
  2. Europe PMC fullTextXML           sectioned JATS, the best representation
  3. PMC OA subset via the S3 bucket  xml > txt > pdf, NCBI's permitted route
  4. Unpaywall best location          publisher OA, then repository copies
  5. OpenAlex best_oa_location        occasionally has a route Unpaywall lacks
  6. preprint (bioRxiv/medRxiv/arXiv) different version, same science, legal
  7. repository aggregators           CORE and friends
  8. nothing free                     say so, and offer the author-request route

Nothing here scrapes a publisher's article page and nothing consults a pirate
mirror. When the ladder ends empty the honest output is a request letter, not a
workaround.
"""
from __future__ import annotations
from . import ids, db, net
from .sources import openalex, europepmc, pmc_s3, oa_locate, preprints


def identify(query: str) -> dict:
    """Resolve any identifier or title to a merged metadata record."""
    kind, val = ids.classify(query)
    rec: dict = {"query": query, "id_kind": kind, "id_value": val}

    if kind == "title":
        hits = openalex.search(val, limit=1)
        if not hits:
            hits = europepmc.search(val, limit=1)
        if not hits:
            return rec | {"found": False,
                          "note": "no match; try a DOI or PMID, or lit_search first"}
        base = hits[0]
        kind = "doi" if base.get("doi") else "pmid"
        val = base.get("doi") or base.get("pmid")
        rec |= {"id_kind": kind, "id_value": val}

    merged: dict = {}
    doi_raw = ""

    if kind == "doi":
        if (c := oa_locate.crossref(val)):
            doi_raw = c.get("doi_raw", "")
            merged |= {k: v for k, v in c.items() if v}
        if (o := openalex.by_id("doi", val)):
            merged = {**{k: v for k, v in o.items() if v}, **merged}
        if (e := europepmc.by_doi(val, doi_raw=doi_raw)):
            for k, v in e.items():
                merged.setdefault(k, v) if not merged.get(k) else None
            merged["pmcid"] = merged.get("pmcid") or e.get("pmcid", "")
            merged["pmid"] = merged.get("pmid") or e.get("pmid", "")
    elif kind in ("pmid", "pmcid"):
        got = europepmc.by_ids([val]) if kind == "pmid" else {}
        e = got.get(val) or (europepmc.search(val, limit=1) or [None])[0]
        if e:
            merged |= {k: v for k, v in e.items() if v}
        if merged.get("doi") and (c := oa_locate.crossref(merged["doi"])):
            doi_raw = c.get("doi_raw", "")
            for k, v in c.items():
                if v and not merged.get(k):
                    merged[k] = v

    if not merged:
        return rec | {"found": False, "note": f"{kind} {val} not found in any index",
                      "last_http_error": net.LAST_ERROR.get("error", "")}

    # JATS titles arrive with inline markup (<i>Staphylococcus aureus</i>).
    # Left in, it corrupts filenames, citations and any n-gram comparison
    # against the source.
    import re as _re
    for f in ("title", "abstract", "journal"):
        if merged.get(f):
            merged[f] = _re.sub(r"\s+", " ", _re.sub(r"<[^>]+>", "", merged[f])).strip()

    merged["doi_raw"] = doi_raw or merged.get("doi", "")
    merged["doi"] = ids.norm_doi(merged.get("doi", ""))
    merged["pmid"] = ids.norm_pmid(merged.get("pmid", ""))
    merged["pmcid"] = ids.norm_pmcid(merged.get("pmcid", ""))
    merged["work_id"] = db.work_id(merged["doi"], merged["pmid"], merged.get("title", ""))
    return rec | {"found": True} | merged


def routes(meta: dict) -> list[dict]:
    """Ordered, de-duplicated list of legal full-text routes for a work."""
    out: list[dict] = []
    seen: set[str] = set()

    def add(**kw):
        key = kw.get("url") or kw.get("via", "")
        if key and key in seen:
            return
        seen.add(key)
        out.append(kw)

    wid = meta.get("work_id", "")
    for f in (db.files_for(wid) if wid else []):
        add(via="local", kind=f["kind"], url="", path=f["path"],
            licence=f["licence"], note="already on disk")

    if (pmcid := meta.get("pmcid")):
        add(via="europepmc-jats", kind="xml",
            url=f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML",
            licence=meta.get("licence", ""), note="sectioned full text, best fidelity")
        for k in pmc_s3.list_keys(pmcid):
            for ext in ("xml", "txt", "pdf"):
                if k.endswith("." + ext):
                    add(via="pmc-s3", kind=ext,
                        url=f"https://pmc-oa-opendata.s3.amazonaws.com/{k}",
                        licence=meta.get("licence", ""),
                        note="NCBI-permitted automated route")

    if (doi := meta.get("doi")):
        u = oa_locate.unpaywall(doi)
        if u and not u.get("__error__"):
            for loc in u.get("locations", []):
                if loc.get("pdf_url"):
                    add(via=f"unpaywall:{loc['host']}", kind="pdf", url=loc["pdf_url"],
                        licence=loc.get("licence", ""),
                        note=f"{loc.get('version','')} {loc.get('repository','')}".strip())
                elif loc.get("landing_url"):
                    # A repository copy with a landing page but no direct PDF link.
                    # Keeping only pdf_url discarded these, and they are the GREEN
                    # OA layer: author manuscripts deposited under funder mandates,
                    # which is precisely what exists for a paywalled paper. One
                    # paper had three such copies and was reported unreachable.
                    add(via=f"unpaywall:{loc['host']}", kind="landing",
                        url=loc["landing_url"], licence=loc.get("licence", ""),
                        note=f"{loc.get('version','')} {loc.get('repository','')} "
                             f"(landing page, may need a click)".strip())
    if meta.get("pdf_url"):
        add(via="openalex", kind="pdf", url=meta["pdf_url"],
            licence=meta.get("licence", ""), note="OpenAlex best OA location")

    if (doi := meta.get("doi")) and not any(r["via"].startswith(("unpaywall", "pmc", "europepmc"))
                                            for r in out):
        if (pp := preprints.find_preprint_of(doi, meta.get("title", ""))):
            add(via="preprint", kind="pdf", url=pp["pdf_url"],
                licence=pp.get("licence", ""),
                note=f"preprint version {pp.get('doi','')}, not the version of record")
    return out


def author_request(meta: dict) -> str:
    """Draft a reprint request. The pre-internet norm, still the highest-yield
    route for a genuinely unavailable paper, and entirely legitimate: authors
    may share their own work for scholarly correspondence."""
    t = meta.get("title", "the paper")
    j = meta.get("journal", "")
    y = meta.get("year", "")
    first = (meta.get("authors", "").split(",") or [""])[0].strip()
    ident = meta.get("doi") or (f"PMID {meta['pmid']}" if meta.get("pmid") else "")
    return (
        f"Subject: Reprint request: {t[:80]}\n\n"
        f"Dear Dr {first.split()[-1] if first else '[author]'},\n\n"
        f"I am a researcher working on bacterial cell division and antimicrobial "
        f"target discovery. I would like to read your paper \"{t}\""
        f"{f' ({j}, {y})' if j else ''}, but I do not have access through my "
        f"institution.\n\n"
        f"Would you be willing to send a copy for my own scholarly use? "
        f"{f'Identifier: {ident}.' if ident else ''}\n\n"
        f"I would be glad to share our related work in return.\n\n"
        f"With thanks,\n[your name]\n[your institution]\n")
