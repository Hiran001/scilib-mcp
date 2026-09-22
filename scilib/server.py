#!/usr/bin/env python3
"""scilib: an MCP server for open scientific literature.

Design constraints, all deliberate:

* Only documented public APIs, used as their operators intend. No scraping of
  publisher article pages, no pirate mirrors, no shared or scraped credentials.
  Those routes get an institution's entire IP range cut off, which costs the
  whole lab the legitimate access it already has.
* Everything fetched is stamped with source, licence and sha256, so a number
  read from a paper can be traced to the exact bytes it came from.
* Every outbound query is logged locally, so the user can audit what left the
  machine.
* The local index is a first-class feature, not an afterthought: the papers you
  already hold are the ones most often missed.
"""
from __future__ import annotations
import json, pathlib, sys

# Add the PACKAGE PARENT, and drop the script's own directory. Python puts the
# script directory on sys.path[0] when this is run as a file, which puts
# scilib/*.py at top level where they can shadow stdlib modules.
_root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
_here = str(pathlib.Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if p not in ("", ".", _here)]

# The SDK renamed FastMCP to MCPServer in 2.x. Support both, because a lab
# sharing this will install it at different times and a rename is not a reason
# for one person's copy to fail to start.
try:
    from mcp.server.mcpserver import MCPServer as _Server      # mcp >= 2
except ModuleNotFoundError as _e:                              # pragma: no cover
    # Narrow deliberately. A bare `except ImportError` here swallows an
    # ImportError raised deep inside the dependency chain and re-raises it from
    # the fallback line, which points the traceback at the wrong module. That
    # cost a debugging cycle when `scilib/http.py` shadowed the stdlib `http`
    # package and starlette failed to import.
    if _e.name and not _e.name.startswith("mcp"):
        raise
    from mcp.server.fastmcp import FastMCP as _Server          # mcp 1.x

from scilib import config, db, extract, fetch as fetchmod, net, ids, index, resolve
from scilib.sources import (openalex, europepmc, oa_locate, preprints, structures,
                            repositories)

mcp = _Server("scilib")

MAX_CHARS = 6000          # keep tool results readable rather than context-eating


def _trim(s: str, n: int = MAX_CHARS) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + f"\n\n[... truncated, {len(s)-n} more characters]"


# ---------------------------------------------------------------- discovery

@mcp.tool()
def lit_search(query: str, limit: int = 15, open_access_only: bool = False,
               year_from: str = "", year_to: str = "",
               sources: str = "openalex,europepmc,semanticscholar",
               allow_external: bool = True) -> str:
    """Search the open scholarly record across several indexes at once.

    `sources` is a comma-separated list. Available:
      openalex        ~250M works, all disciplines, open catalogue
      europepmc       ~45M biomedical records, ~6M with open full text
      semanticscholar citation graph, open-PDF pointer, one-line summaries
      arxiv           physics/quant-bio/CS preprints
      doaj            articles in fully open-access journals
      openaire        European repositories and funder outputs
      core            ~300M repository records (needs a free API key)

    The repository sources matter for paywalled work: a closed paper very often
    has a legal author manuscript deposited under a funder mandate.

    Results are merged and de-duplicated on DOI. Each carries open-access
    status, licence, and whether full text is retrievable.

    `allow_external` exists because a free-text query leaves this machine. Set
    confidential_mode in configure() to make that an explicit decision per call.
    """
    net.guard_freetext(query, allow_external)
    want = {s.strip().lower() for s in sources.split(",") if s.strip()}
    rows: list[dict] = []
    if "openalex" in want:
        rows += openalex.search(query, limit=limit, year_from=year_from, year_to=year_to)
    if "europepmc" in want:
        rows += europepmc.search(query, limit=limit, open_only=open_access_only)
    if "arxiv" in want:
        rows += preprints.arxiv(query=query, limit=min(limit, 20))
    if "semanticscholar" in want or "s2" in want:
        rows += repositories.s2_search(query, limit=limit)
    if "doaj" in want:
        rows += repositories.doaj_search(query, limit=limit)
    if "openaire" in want:
        rows += repositories.openaire_search(query, limit=limit)
    if "core" in want:
        rows += [r for r in repositories.core_search(query, limit=limit)
                 if not r.get("__note__")]
    rows = [r for r in rows if r.get("title")]

    merged: dict[str, dict] = {}
    for r in rows:
        key = ids.norm_doi(r.get("doi", "")) or f"t:{(r.get('title') or '')[:70].lower()}"
        if key in merged:
            for k, v in r.items():
                if v and not merged[key].get(k):
                    merged[key][k] = v
            merged[key]["seen_in"] = merged[key].get("seen_in", []) + [r.get("source", "")]
        else:
            r["seen_in"] = [r.get("source", "")]
            merged[key] = r

    out = list(merged.values())
    if open_access_only:
        out = [r for r in out if r.get("is_oa")]
    out.sort(key=lambda r: (-int(r.get("cited_by") or 0), r.get("year", "")))
    out = out[:limit]

    lines = [f"{len(out)} result(s) for: {query}", ""]
    for i, r in enumerate(out, 1):
        held = db.find_work(doi=r.get("doi", ""), pmid=r.get("pmid", ""))
        mark = " [IN YOUR LIBRARY]" if held and db.files_for(held["work_id"]) else ""
        lines += [
            f"{i}. {r.get('title','')[:150]}{mark}",
            f"   {r.get('journal','')} {r.get('year','')} | cited {r.get('cited_by',0)}"
            f" | {'OA' if r.get('is_oa') else 'closed'}"
            f"{' ' + r['licence'] if r.get('licence') else ''}",
            f"   doi:{r.get('doi','-')}  pmid:{r.get('pmid','-')}  pmcid:{r.get('pmcid','-')}",
        ]
        if r.get("abstract"):
            lines.append(f"   {r['abstract'][:240]}...")
        lines.append("")
    return _trim("\n".join(lines))


@mcp.tool()
def lit_get(identifier: str) -> str:
    """Resolve a DOI, PMID, PMCID or title to full metadata plus every legal
    route to its full text. Does not download anything."""
    meta = resolve.identify(identifier)
    if not meta.get("found"):
        return json.dumps(meta, indent=2)
    rs = resolve.routes(meta)
    view = {k: meta.get(k) for k in
            ("title", "authors", "journal", "year", "doi", "pmid", "pmcid",
             "licence", "is_oa", "oa_status", "cited_by", "publisher", "nih_funded")}
    view["abstract"] = _trim(meta.get("abstract", ""), 1200)
    view["fulltext_routes"] = [
        {"via": r["via"], "kind": r["kind"], "licence": r.get("licence", ""),
         "note": r.get("note", "")} for r in rs]
    view["routes_available"] = len(rs)
    if not rs:
        view["no_open_copy"] = ("No legal open copy found. Options: your institution's "
                               "subscription, interlibrary loan, or lit_request_copy().")
    return json.dumps(view, indent=2, default=str)


@mcp.tool()
def lit_fetch(identifier: str, prefer_pdf: bool = False) -> str:
    """Download the best legal full text for a paper into the local library,
    extract its text, and index it. Prefers sectioned XML over PDF because XML
    preserves headings, which makes 'what did the Methods say' answerable."""
    return json.dumps(fetchmod.fetch(identifier, prefer_pdf=prefer_pdf),
                      indent=2, default=str)


@mcp.tool()
def lit_cited_by(doi: str, limit: int = 25) -> str:
    """Papers that cite this one. The route to the paper you did not know to
    search for: a keyword sweep only returns what you already thought to ask."""
    rows = openalex.cited_by(ids.norm_doi(doi), limit=limit)
    lines = [f"{len(rows)} citing work(s) for {doi}", ""]
    for r in rows:
        lines.append(f"- {r.get('year','')} {r.get('journal','')[:34]} | "
                     f"{r.get('title','')[:110]}\n  doi:{r.get('doi','-')} "
                     f"{'OA' if r.get('is_oa') else 'closed'}")
    return _trim("\n".join(lines))


@mcp.tool()
def lit_references(identifier: str, limit: int = 40) -> str:
    """The bibliography of a paper, resolved to real records.

    Reading a paper's reference list is how you find the work that nobody in
    your subfield cites. Accepts DOI:10.x, PMID:123, arXiv:1234.5678.
    """
    kind, val = ids.classify(identifier)
    key = {"doi": f"DOI:{val}", "pmid": f"PMID:{val}",
           "pmcid": f"PMCID:{val}", "arxiv": f"arXiv:{val}"}.get(kind, val)
    rows = repositories.s2_references(key, limit=limit)
    if not rows:
        return f"No references returned for {identifier}."
    lines = [f"{len(rows)} reference(s) from {identifier}", ""]
    for r in rows:
        held = db.find_work(doi=r.get("doi", ""), pmid=r.get("pmid", ""))
        mark = " [HELD]" if held and db.files_for(held["work_id"]) else ""
        lines.append(f"- {r.get('year','')} {r.get('journal','')[:30]} | "
                     f"{r.get('title','')[:100]}{mark}\n  doi:{r.get('doi','-')} "
                     f"{'OA' if r.get('is_oa') else 'closed'}")
    return _trim("\n".join(lines), 9000)


@mcp.tool()
def lit_request_copy(identifier: str) -> str:
    """Draft a reprint request to the corresponding author.

    The pre-internet norm and still the highest-yield route for a genuinely
    unavailable paper. Authors may lawfully share their own work for scholarly
    correspondence. Returns the draft; it does not send anything.
    """
    meta = resolve.identify(identifier)
    if not meta.get("found"):
        return json.dumps(meta, indent=2)
    return resolve.author_request(meta)


# ---------------------------------------------------------------- local library

@mcp.tool()
def lib_search(query: str, limit: int = 12) -> str:
    """Full-text search across every paper already on this machine.

    This is the tool to reach for FIRST. A paper on disk that nobody searched is
    invisible in exactly the way a paywalled one is, and the failure is quieter.
    Supports FTS5 syntax: phrases in "quotes", AND/OR/NOT, prefix*.
    """
    rows = db.search(query, limit=limit)
    if not rows:
        st = db.stats()
        return (f"No matches for: {query}\n\nIndex holds {st['chunks']} chunks from "
                f"{st['indexed_files']} files. If that looks low, run lib_index() "
                f"over your paper directories first.")
    lines = [f"{len(rows)} match(es) for: {query}", ""]
    for r in rows:
        lines += [f"- {r['title'][:120]}",
                  f"  {r['journal'][:40]} {r['year']} | doi:{r['doi'] or '-'} "
                  f"pmid:{r['pmid'] or '-'} | {r['loc']}",
                  f"  {pathlib.Path(r['path']).name}",
                  f"  ...{r['snippet']}...", ""]
    return _trim("\n".join(lines), 9000)


@mcp.tool()
def lib_read(identifier: str, section: str = "", max_chars: int = 6000) -> str:
    """Read a paper held locally, optionally one section of it.

    `section` matches a heading case-insensitively, e.g. 'methods', 'surface
    plasmon', 'results'. With no section, returns the abstract plus a list of
    available headings, so a long paper can be read deliberately rather than
    dumped into context.
    """
    kind, val = ids.classify(identifier)
    w = db.find_work(doi=val if kind == "doi" else "", pmid=val if kind == "pmid" else "",
                     pmcid=val if kind == "pmcid" else "")
    if not w and kind == "title":
        hits = db.search(val, limit=1)
        w = db.get_work(hits[0]["work_id"]) if hits else None
    if not w:
        return (f"Not held locally: {identifier}\nTry lit_fetch('{identifier}') "
                f"to retrieve it, or lib_search() to find it under another identifier.")
    files = db.files_for(w["work_id"])
    if not files:
        return f"Metadata known but no file on disk for: {w['title']}"

    xml = next((f for f in files if f["kind"] == "xml"), None)
    if xml:
        parsed = extract.jats_sections(pathlib.Path(xml["path"]).read_bytes())
        heads = [s["heading"] for s in parsed.get("sections", []) if s["heading"]]
        if section:
            want = section.lower()
            got = [s for s in parsed["sections"] if want in (s["heading"] or "").lower()]
            if not got:
                return (f"No section matching '{section}'.\nAvailable headings:\n  - "
                        + "\n  - ".join(heads))
            body = "\n\n".join(f"## {s['heading']}\n{s['text']}" for s in got)
            return _trim(f"{w['title']}\n[{xml['source']}, licence: {xml['licence']}]\n\n{body}",
                         max_chars)
        return _trim(f"{w['title']}\n{w['journal']} {w['year']} | licence: {xml['licence']}\n"
                     f"source: {xml['source_url']}\n\nABSTRACT\n{parsed.get('abstract','')}\n\n"
                     f"SECTIONS ({len(heads)}):\n  - " + "\n  - ".join(heads)
                     + f"\n\nCall lib_read('{identifier}', section='...') for any of these.",
                     max_chars)

    f = files[0]
    text = (extract.pdf_to_text(pathlib.Path(f["path"])) if f["kind"] == "pdf"
            else pathlib.Path(f["path"]).read_text(errors="replace"))
    if section:
        low = text.lower(); i = low.find(section.lower())
        if i < 0:
            return f"'{section}' not found in the text of {w['title']}"
        text = text[max(0, i - 200): i + max_chars]
    return _trim(f"{w['title']}\n[{f['source']}, {f['kind']}, licence: {f['licence']}]\n\n{text}",
                 max_chars)


@mcp.tool()
def lib_index(path: str, recursive: bool = True, limit: int = 0) -> str:
    """Index a directory of papers already on disk so lib_search can find them.

    Handles PDF, JATS XML and text. `limit` of 0 means no cap; any other value
    caps the scan and the result says so explicitly, because a capped scan
    reported as a total is how an inventory lies.
    """
    r = index.index_path(pathlib.Path(path), recursive=recursive,
                         limit=(limit or None))
    return json.dumps(r, indent=2)


@mcp.tool()
def lib_status() -> str:
    """What the local library holds, and the current configuration."""
    st = db.stats()
    st["config"] = {"email": config.contact() or "(unset: set one, APIs rate-limit anonymous use)",
                    "library": config.CFG["library"],
                    "confidential_mode": config.CFG.get("confidential_mode"),
                    "audit_log": str(config.library() / "outbound_queries.log")}
    con = db.con()
    st["licences"] = {row[0] or "(unrecorded)": row[1] for row in con.execute(
        "SELECT licence, count(*) FROM files GROUP BY licence ORDER BY 2 DESC LIMIT 12")}
    st["sources"] = {row[0] or "(unrecorded)": row[1] for row in con.execute(
        "SELECT source, count(*) FROM files GROUP BY source ORDER BY 2 DESC LIMIT 12")}
    return json.dumps(st, indent=2)


# ---------------------------------------------------------------- structures

@mcp.tool()
def pdb_entry(pdb_id: str) -> str:
    """Metadata and the PRIMARY CITATION for a PDB entry.

    Coordinates do not carry the analysis. If a design rests on a structure it
    rests on the paper that deposited it, and that paper is one lookup away.
    Resolves the DOI by title when RCSB omits it, which it often does for older
    entries, i.e. exactly the ones nobody has read.
    """
    return json.dumps(structures.pdb_entry(pdb_id), indent=2, default=str)


@mcp.tool()
def uniprot_entry(accession: str) -> str:
    """UniProt record: sequence, features, and cross-referenced PDB entries."""
    r = structures.uniprot(accession)
    if r.get("sequence") and len(r["sequence"]) > 2000:
        r["sequence"] = r["sequence"][:2000] + "...[truncated]"
    return json.dumps(r, indent=2, default=str)


# ---------------------------------------------------------------- admin

@mcp.tool()
def configure(email: str = "", library: str = "", confidential_mode: str = "",
              core_api_key: str = "", s2_api_key: str = "",
              show: bool = False) -> str:
    """Set the contact address, library location, or confidential mode.

    The email goes to the polite pools of Crossref, Unpaywall, OpenAlex and NCBI.
    They ask for it, grant higher rate limits in return, and it is the only
    personal datum this tool sends anywhere.

    confidential_mode 'on' blocks free-text queries from leaving the machine
    unless a call passes allow_external=true. Identifier lookups still work,
    since a DOI carries no unpublished reasoning.
    """
    if show or not (email or library or confidential_mode):
        shown = {k: config.CFG[k] for k in
                 ("email", "library", "confidential_mode", "audit_log", "extra_paths")}
        # Never echo a key back; confirm presence only.
        shown["core_api_key"] = "set" if config.CFG.get("core_api_key") else "(unset)"
        shown["s2_api_key"] = "set" if config.CFG.get("s2_api_key") else "(unset)"
        return json.dumps(shown, indent=2)
    up: dict = {}
    if email:
        up["email"] = email.strip()
    if library:
        up["library"] = str(pathlib.Path(library).expanduser())
    if confidential_mode:
        up["confidential_mode"] = confidential_mode.strip().lower() in ("on", "true", "1", "yes")
    if core_api_key:
        up["core_api_key"] = core_api_key.strip()
    if s2_api_key:
        up["s2_api_key"] = s2_api_key.strip()
    config.save(up)
    return json.dumps({"updated": up, "config_file": str(config.CONFIG_FILE)}, indent=2)


@mcp.tool()
def audit_log(lines: int = 40) -> str:
    """Show what this tool has sent off the machine, most recent last."""
    p = config.library() / "outbound_queries.log"
    if not p.exists():
        return "No outbound queries logged yet."
    rows = p.read_text(errors="replace").splitlines()[-lines:]
    return f"Last {len(rows)} outbound requests ({p}):\n\n" + "\n".join(rows)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
