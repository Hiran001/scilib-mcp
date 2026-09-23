"""Offline tests. No network, safe to run anywhere.

Every test here corresponds to a bug that actually occurred during development.
A test suite of hypotheticals is decoration; this one is a regression net.
"""
import pathlib, sqlite3, sys, tempfile, os

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# Point the library at a scratch dir before importing anything that touches it.
_TMP = tempfile.mkdtemp(prefix="scilib-test-")
os.environ["SCILIB_LIBRARY"] = _TMP

from scilib import ids, db, extract          # noqa: E402


def test_bare_digits_are_pmid_not_pmcid():
    """norm_pmcid() prepends 'PMC' to any digit string. If classify() reaches it
    before the PMID test, every bare PMID is looked up as a PMC record that does
    not exist, and the lookup fails silently."""
    assert ids.classify("38639993") == ("pmid", "38639993")
    assert ids.classify("PMID: 38639993") == ("pmid", "38639993")
    assert ids.classify("PMC4466716") == ("pmcid", "PMC4466716")


def test_doi_variants_collapse_to_one_key():
    """A DOI harvested from prose carries the sentence's full stop. If that does
    not normalise away, one paper becomes several records."""
    forms = ["10.7554/eLife.85579", "https://doi.org/10.7554/eLife.85579",
             "doi:10.7554/eLife.85579.", "10.7554/eLife.85579,",
             "  10.7554/ELIFE.85579  "]
    assert len({ids.norm_doi(f) for f in forms}) == 1


def test_no_package_module_shadows_a_stdlib_module():
    """A module named after a stdlib top-level package breaks imports whenever
    the package directory lands on sys.path[0], which is what happens when an
    MCP client runs server.py as a script. This cost a full debugging cycle
    when scilib/http.py shadowed stdlib http and starlette failed to import."""
    import sysconfig
    stdlib = pathlib.Path(sysconfig.get_paths()["stdlib"])
    reserved = {p.stem for p in stdlib.iterdir()
                if p.suffix == ".py" or (p.is_dir() and (p / "__init__.py").exists())}
    pkg = pathlib.Path(__file__).resolve().parent.parent / "scilib"
    clashes = {p.stem for p in pkg.rglob("*.py")} & reserved
    assert not clashes, f"module name shadows stdlib: {sorted(clashes)}"


def test_sparse_record_does_not_erase_a_populated_field():
    """Sources return records of differing richness. A merge that overwrites a
    present field with an empty one silently degrades metadata."""
    wid = db.upsert_work(dict(doi="10.1/x", title="Full title", year="2024"))
    db.upsert_work(dict(doi="10.1/x", journal="Some Journal"))
    w = db.get_work(wid)
    assert w["title"] == "Full title"
    assert w["journal"] == "Some Journal"


def test_malformed_fts_query_does_not_raise():
    """Callers write prose, not FTS5 grammar. 'NEAR/8' is a syntax error and
    must degrade to a result, never a stack trace."""
    db.upsert_work(dict(doi="10.1/y", title="T"))
    assert db.search("trimerisation NEAR/8 motif") == [] or True   # must not raise
    assert db.search('unbalanced "quote') == [] or True
    assert db.search("") == []


def test_document_scope_recovers_cross_chunk_matches():
    """FTS5 matches per ROW. With only a chunk index, a two-term AND fails when
    the terms sit in different chunks of one paper: a silent recall loss."""
    wid = db.upsert_work(dict(doi="10.1/z", title="Two-term paper"))
    p = pathlib.Path(_TMP) / "two_term.txt"
    # Many paragraphs, with the two terms at opposite ends. The chunker keeps a
    # one-paragraph overlap between chunks, so two paragraphs alone is not
    # enough to separate them.
    paras = ["alpha " + "filler " * 120]
    paras += ["filler " * 120 for _ in range(30)]
    paras += ["omega " + "filler " * 120]
    p.write_text("\n\n".join(paras))
    db.record_file(wid, p, "txt")
    n = db.index_text(wid, p, p.read_text())
    assert n > 1, "test needs the text split across multiple chunks"
    chunk_hits = db.con().execute(
        "SELECT count(*) FROM chunks WHERE chunks MATCH ?", ("alpha AND omega",)).fetchone()[0]
    assert chunk_hits == 0, "precondition: no single chunk holds both terms"
    assert db.search("alpha AND omega"), "document scope failed to recover the match"


def test_sniff_rejects_html_masquerading_as_a_pdf():
    """A route promising a PDF that returns a login page must not be stored as a
    paper. That is how a library quietly fills with junk."""
    assert extract.sniff(b"%PDF-1.7\n...") == "pdf"
    assert extract.sniff(b"<!DOCTYPE html><html><body>Sign in") == "html"
    assert extract.sniff(b'<?xml version="1.0"?><article>...</article>') == "xml"


def test_jats_text_does_not_weld_adjacent_tags():
    """'<italic>E. coli</italic>DnaA' must not become 'E. coliDnaA'. Welding
    breaks reading and any n-gram comparison against the source."""
    xml = (b'<article><body><sec><title>Results</title>'
           b'<p>The <italic>E. coli</italic> DnaA protein binds.</p></sec></body></article>')
    parsed = extract.jats_sections(xml)
    text = parsed["sections"][0]["text"]
    assert "E. coli DnaA" in text, f"got: {text!r}"


def test_supplementary_pdf_is_not_mistaken_for_the_article():
    """A publisher's full-text link can serve the SUPPLEMENT instead.

    The bytes are a valid PDF, so every byte-level check passes and the
    supplement is filed under the paper's own name. Two structural papers were
    once recorded as retrieved when what had arrived was a 2-page MD-methods
    supplement and a 3-page sequence listing. Both real cases are here, plus a
    genuine article about dietary supplements, which must NOT be flagged.
    """
    from scilib import fetch
    cases = [
        # (first-page text, PDF metadata title, page count, is_supplement)
        ("Supplementary Information Molecular dynamics simulations performed",
         "20150513_Supplementary_Section", 2, True),
        ("Supplement 1 - Sequences used in this study Construct A MKTII",
         "Microsoft Word - Supplemental_AK2.docx", 3, True),
        ("Structural insights into receptor activation. Activation of the",
         "Nature", 10, False),
        ("Dietary supplement use among adults: a cross-sectional survey of 4000",
         "Dietary supplements and health", 14, False),
    ]
    for text, title, pages, expect_supp in cases:
        got = bool(fetch.looks_like_supplement(text, title, pages))
        assert got == expect_supp, (
            f"{title!r} ({pages}p): expected supplement={expect_supp}, got {got}")



if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL  {name}: {e}")
            except Exception as e:
                fails += 1
                print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{'FAILURES: ' + str(fails) if fails else 'all offline tests passed'}")
    sys.exit(1 if fails else 0)
