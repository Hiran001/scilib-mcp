# Changelog

## 0.1.0

First release.

### Sources
- **OpenAlex** (~250M works) for discovery, citation graph and OA location
- **Europe PMC** (~45M biomedical records) including sectioned JATS full text
- **PubMed Central OA subset** via the AWS Open Data bucket, an NCBI-permitted
  automated route
- **Crossref** for authoritative metadata, licence and funder information
- **Unpaywall** for legal free copies across ~50M DOIs
- **Semantic Scholar** for the citation graph, open-PDF pointers and summaries
- **bioRxiv / medRxiv / arXiv** for preprints, including published-DOI mapping
- **DOAJ** and **OpenAIRE** for fully-open journals and European repositories
- **CORE** (~300M repository records, optional free API key)
- **RCSB PDB, UniProt, AlphaFold DB** for structures and sequences

### Features
- Resolution ladder ordered by fidelity: local file, then sectioned XML, then
  PDF. Reports every legal route it found rather than only the one it used.
- Provenance per file: source, URL, licence and sha256.
- Local full-text index over papers already on disk, with both chunk-level
  (precise snippets) and document-level (cross-section recall) matching.
- `pdb_entry` resolves a structure to its primary citation, falling back to a
  title match when RCSB omits the DOI, which it commonly does for older entries.
- Author reprint-request drafting when no open copy exists.
- Cross-process rate limiting, so several processes or several users behind one
  NAT queue against a single clock.
- Confidential mode: free-text queries do not leave the machine unless a call
  explicitly opts in. Identifier lookups are unaffected.
- Local audit log of every outbound request.

### Notes
- Requires `pdftotext` (poppler-utils) for PDF text extraction. Scanned PDFs
  without OCR are reported as failures by name, never counted as successes.
- Licence strings are recorded as each source reports them and are not
  normalised, so the same licence can appear under two spellings.
