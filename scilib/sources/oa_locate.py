"""Crossref and Unpaywall: authoritative metadata, licence, and free-copy location.

Crossref is the DOI registration agency, so its licence and funder fields are the
primary record rather than a third party's guess. Unpaywall indexes legal free
copies (publisher OA and author manuscripts in repositories) for ~50M DOIs and is
the standard tool for this; it does not index pirated copies.

Both ask for a contact address and both are free.
"""
from __future__ import annotations
from .. import net, config


def crossref(doi: str) -> dict | None:
    params = {"mailto": config.contact()} if config.contact() else {}
    j = net.get_json(f"https://api.crossref.org/works/{doi}", params,
                      kind="lookup:crossref")
    m = j.get("message")
    if not m:
        return None
    lic = [l.get("URL", "") for l in (m.get("license") or [])]
    auth = ", ".join(
        f"{a.get('given','')} {a.get('family','')}".strip()
        for a in (m.get("author") or [])[:12])
    date = (m.get("issued") or {}).get("date-parts") or [[None]]
    return {
        "source": "crossref",
        "doi": (m.get("DOI") or "").lower(),
        "doi_raw": m.get("DOI") or "",
        "title": (m.get("title") or [""])[0],
        "journal": (m.get("container-title") or [""])[0],
        "year": str(date[0][0] or ""),
        "authors": auth,
        "licence": "; ".join(sorted(set(lic)))[:300],
        "abstract": (m.get("abstract") or "").replace("<jats:p>", "").replace("</jats:p>", ""),
        "type": m.get("type", ""),
        "publisher": m.get("publisher", ""),
        "funders": [f.get("name", "") for f in (m.get("funder") or [])],
        "cited_by": m.get("is-referenced-by-count", 0),
        # A funded-by-NIH paper is very likely in PMC even when the publisher
        # page is paywalled, because of the NIH public access policy.
        "nih_funded": any("National Institutes of Health" in (f.get("name") or "")
                          for f in (m.get("funder") or [])),
    }


def unpaywall(doi: str) -> dict | None:
    """Legal free locations for a DOI. Requires an email by their terms."""
    email = config.contact()
    if not email:
        return {"__error__": "Unpaywall requires a contact email; set it with configure()"}
    j = net.get_json(f"https://api.unpaywall.org/v2/{doi}",
                      {"email": email}, kind="lookup:unpaywall")
    if j.get("__error__"):
        return None
    locs = []
    for loc in (j.get("oa_locations") or []):
        locs.append({
            "pdf_url": loc.get("url_for_pdf") or "",
            "landing_url": loc.get("url_for_landing_page") or "",
            "host": loc.get("host_type") or "",       # publisher | repository
            "version": loc.get("version") or "",      # publishedVersion | acceptedVersion
            "licence": loc.get("license") or "",
            "repository": loc.get("repository_institution") or "",
        })
    return {
        "source": "unpaywall",
        "is_oa": bool(j.get("is_oa")),
        "oa_status": j.get("oa_status", ""),          # gold | green | hybrid | bronze | closed
        "journal_is_oa": bool(j.get("journal_is_oa")),
        "title": j.get("title") or "",
        "year": str(j.get("year") or ""),
        "locations": locs,
        "best": (locs[0] if locs else None),
    }
