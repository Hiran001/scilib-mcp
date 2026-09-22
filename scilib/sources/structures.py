"""Structural and sequence databases: RCSB PDB, UniProt, AlphaFold DB.

All fully open. The PDB entry point matters here for one specific reason: a
design that rests on a structure rests on the paper that deposited it, and the
citation is one lookup away but is routinely skipped. This turns a PDB accession
straight into its primary citation so the paper can be fetched.
"""
from __future__ import annotations
from .. import net


def pdb_entry(pdb_id: str, resolve_citation: bool = True) -> dict:
    pid = (pdb_id or "").strip().upper()
    j = net.get_json(f"https://data.rcsb.org/rest/v1/core/entry/{pid}", kind="lookup:rcsb")
    if j.get("__error__"):
        return {"error": j["__error__"], "pdb_id": pid}
    cite = (j.get("citation") or [{}])
    primary = next((c for c in cite if c.get("id") == "primary"), cite[0] if cite else {})
    info = j.get("struct") or {}
    ex = j.get("exptl") or [{}]
    refine = (j.get("refine") or [{}])[0]
    out = {
        "pdb_id": pid,
        "title": info.get("title", ""),
        "method": ex[0].get("method", ""),
        "resolution": (j.get("rcsb_entry_info") or {}).get("resolution_combined", [None])[0],
        "r_free": refine.get("ls_rfactor_rfree"),
        "deposited": (j.get("rcsb_accession_info") or {}).get("deposit_date", ""),
        "released": (j.get("rcsb_accession_info") or {}).get("initial_release_date", ""),
        "polymer_entities": (j.get("rcsb_entry_info") or {}).get("polymer_entity_count", 0),
        "primary_citation": {
            "title": primary.get("title", ""),
            "journal": primary.get("journal_abbrev", ""),
            "year": primary.get("year", ""),
            "doi": (primary.get("pdbx_database_id_doi") or "").lower(),
            "pmid": str(primary.get("pdbx_database_id_pub_med") or ""),
            "authors": ", ".join(primary.get("rcsb_authors") or [])[:400],
        },
        "note": "Coordinates do not carry the analysis. Fetch the primary "
                "citation before designing anything from this entry.",
    }
    return _backfill_citation(out) if resolve_citation else out


def _backfill_citation(rec: dict) -> dict:
    """RCSB often omits the DOI and PMID of an older primary citation.

    Without this the chain PDB -> paper breaks exactly where it matters, on the
    old structures whose depositing paper nobody has read. Resolve by title
    instead of giving up.
    """
    c = rec.get("primary_citation") or {}
    if (c.get("doi") or c.get("pmid")) or not c.get("title"):
        return rec
    from . import openalex, europepmc
    hits = openalex.search(c["title"], limit=3)
    for h in hits:
        # Require a real title match, not merely the top hit for the query.
        a = "".join(ch for ch in (h.get("title") or "").lower() if ch.isalnum())
        b = "".join(ch for ch in c["title"].lower() if ch.isalnum())
        if a[:60] and (a[:60] in b or b[:60] in a):
            c["doi"] = h.get("doi", "") or c.get("doi", "")
            c["pmid"] = h.get("pmid", "") or c.get("pmid", "")
            c["resolved_by"] = "openalex title match"
            break
    else:
        for h in europepmc.search(c["title"], limit=3):
            a = "".join(ch for ch in (h.get("title") or "").lower() if ch.isalnum())
            b = "".join(ch for ch in c["title"].lower() if ch.isalnum())
            if a[:60] and (a[:60] in b or b[:60] in a):
                c["doi"], c["pmid"] = h.get("doi", ""), h.get("pmid", "")
                c["resolved_by"] = "europepmc title match"
                break
    rec["primary_citation"] = c
    return rec


def uniprot(acc: str) -> dict:
    a = (acc or "").strip().upper()
    j = net.get_json(f"https://rest.uniprot.org/uniprotkb/{a}.json", kind="lookup:uniprot")
    if j.get("__error__"):
        return {"error": j["__error__"], "accession": a}
    seq = (j.get("sequence") or {})
    names = (j.get("proteinDescription") or {}).get("recommendedName", {})
    feats = [{"type": f.get("type"), "start": (f.get("location") or {}).get("start", {}).get("value"),
              "end": (f.get("location") or {}).get("end", {}).get("value"),
              "description": f.get("description", "")}
             for f in (j.get("features") or [])[:60]]
    return {
        "accession": a,
        "id": j.get("uniProtkbId", ""),
        "protein": (names.get("fullName") or {}).get("value", ""),
        "organism": (j.get("organism") or {}).get("scientificName", ""),
        "length": seq.get("length"),
        "sequence": seq.get("value", ""),
        "features": feats,
        "pdb": [x.get("id") for x in (j.get("uniProtKBCrossReferences") or [])
                if x.get("database") == "PDB"][:40],
    }


def alphafold(acc: str) -> dict:
    a = (acc or "").strip().upper()
    j = net.get(f"https://alphafold.ebi.ac.uk/api/prediction/{a}", kind="lookup:alphafold",
                 expect_json=True)
    if isinstance(j, dict) and j.get("__error__"):
        return {"error": j["__error__"], "accession": a}
    rec = j[0] if isinstance(j, list) and j else {}
    return {"accession": a, "model_url": rec.get("pdbUrl", ""),
            "cif_url": rec.get("cifUrl", ""), "pae_url": rec.get("paeImageUrl", ""),
            "version": rec.get("latestVersion", ""),
            "note": "A prediction, not an observation. Label it as such."}
