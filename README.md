# scilib

[![tests](https://github.com/Hiran001/scilib-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/Hiran001/scilib-mcp/actions/workflows/tests.yml)
[![licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-server-000000.svg)](https://modelcontextprotocol.io)

**An MCP server for open-access scientific literature.**

scilib gives an AI assistant three capabilities it does not have by default:
search across the open scholarly record, retrieve full text through legal
open-access routes with full provenance, and search the papers you already have
on disk by their contents.

It runs entirely on your own machine. Nothing is uploaded, and the only data
that leaves is the search terms and identifiers you ask it to look up.

---

## Contents

- [Why](#why)
- [What it does not do](#what-it-does-not-do)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Quick start](#quick-start)
- [Tool reference](#tool-reference)
- [How full-text resolution works](#how-full-text-resolution-works)
- [Data, privacy and provenance](#data-privacy-and-provenance)
- [Sources](#sources)
- [Troubleshooting](#troubleshooting)
- [Acknowledgements](#acknowledgements)
- [Contributing](#contributing)
- [Licence](#licence)

---

## Why

Two different problems get confused with one another.

**Acquisition.** Finding a free, legal copy of a paper. This is largely solved,
and the solution is underused. Roughly half of all literature, and a higher
share of recent biomedical work, is legally available through PubMed Central,
Europe PMC, preprint servers, institutional repositories and publisher
open-access programmes. Much of it is invisible to a search that only checks the
publisher's own page, because a closed paper very often has a legal author
manuscript deposited in a repository under a funder mandate.

**Retrieval.** Knowing what is inside the papers you already hold. This is not
solved, and it is where the expensive mistakes happen. A keyword search over
titles and abstracts can only return answers to questions you already thought to
ask, so it is structurally incapable of surfacing the result that reframes your
problem. Papers sit unread in project folders for months.

scilib addresses both, and treats the second as the more important one.

## What it does not do

scilib does not use Sci-Hub, LibGen or any other pirate mirror. It does not
scrape publisher article pages, and it does not use shared or scraped
credentials.

This is an engineering decision as much as a legal one. Publishers monitor
institutional proxy traffic for bulk retrieval, and the standard response is to
suspend access for **the entire institution**, after which the university traces
it to an individual account. A tool shared across a research group that behaved
this way would put every member's legitimate access at risk in order to reach a
small number of papers the legal routes largely cover anyway.

Where a paper genuinely has no open copy, `lit_request_copy` drafts a reprint
request to the corresponding author. This was the normal way to obtain a paper
before the web, it is entirely lawful, and it still works.

---

## Requirements

| | |
|---|---|
| Python | 3.10 or newer |
| `pdftotext` | from poppler-utils, required for indexing PDFs |
| Disk | a few hundred MB for a typical personal library |
| Accounts | none required; two optional free API keys raise rate limits |

Install `pdftotext`:

```bash
sudo apt install poppler-utils      # Debian / Ubuntu
brew install poppler                # macOS
```

## Installation

```bash
git clone https://github.com/Hiran001/scilib-mcp.git
cd scilib-mcp
./scripts/install.sh
```

The installer creates a self-contained virtualenv at `.venv`, installs the two
dependencies (`mcp` and `httpx`), and verifies that the server imports cleanly.
It does not modify anything outside the project directory.

### Register with your MCP client

**Claude Code**

```bash
claude mcp add scilib -- "$(pwd)/.venv/bin/python" "$(pwd)/scilib/server.py"
```

**Claude Desktop**, or any client using a JSON config, add to `mcpServers`:

```json
{
  "mcpServers": {
    "scilib": {
      "command": "/absolute/path/to/scilib-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/scilib-mcp/scilib/server.py"]
    }
  }
}
```

Use absolute paths. Restart the client, and confirm the server shows as
connected before continuing.

## Configuration

Run once, in your assistant:

```
configure(email="you@university.edu")
```

The address is sent to the polite pools operated by Crossref, Unpaywall,
OpenAlex and NCBI. These services request a contact address and grant higher
rate limits in return. It is the only personal data scilib transmits, Unpaywall
requires it, and everything else works without it.

Configuration is stored at `~/.config/scilib/config.json`.

| setting | default | purpose |
|---|---|---|
| `email` | empty | contact address for API polite pools |
| `library` | `~/scilib-library` | where retrieved papers and the index live |
| `confidential_mode` | `false` | block free-text queries from leaving the machine |
| `core_api_key` | empty | optional, free, raises the CORE rate limit |
| `s2_api_key` | empty | optional, free, raises the Semantic Scholar rate limit |
| `audit_log` | `true` | log every outbound request locally |

### Confidential mode

```
configure(confidential_mode="on")
```

With this enabled, free-text searches will not leave the machine unless a call
explicitly passes `allow_external=true`. Identifier lookups are unaffected,
since a DOI carries no unpublished reasoning. This matters for groups working on
unpublished hypotheses, where the wording of a search query is itself a
disclosure.

## Quick start

Index the papers you already have:

```
lib_index("/path/to/your/papers")
```

Then search their full text:

```
lib_search("thermal denaturation midpoint")
```

Find and retrieve something new:

```
lit_search("bacterial cell division regulators", limit=10)
lit_get("10.1038/s41467-018-08056-2")     # metadata plus every legal route
lit_fetch("10.1038/s41467-018-08056-2")   # download, store, index
lib_read("10.1038/s41467-018-08056-2", section="methods")
```

Start from a structure:

```
pdb_entry("4HHB")      # returns the entry AND its primary citation
```

Check what the server holds:

```
lib_status()
```

## Tool reference

### Discovery and retrieval

| tool | description |
|---|---|
| `lit_search` | Federated search across the configured sources, merged and de-duplicated on DOI. Marks results already in your library. |
| `lit_get` | Resolve a DOI, PMID, PMCID or title to full metadata plus every legal full-text route, without downloading. |
| `lit_fetch` | Retrieve the best available full text, store it with provenance, extract and index it. |
| `lit_cited_by` | Papers citing a given DOI. |
| `lit_references` | The bibliography of a paper, resolved, marking what you already hold. |
| `lit_request_copy` | Draft a reprint request to the corresponding author. Returns the text; sends nothing. |

### Local library

| tool | description |
|---|---|
| `lib_search` | Full-text search across every indexed paper on your machine. |
| `lib_read` | Read a held paper, optionally one section: `lib_read(doi, section="methods")`. |
| `lib_index` | Index a directory of PDF, JATS XML or text files. |
| `lib_status` | Holdings, licence and source breakdown, current configuration. |

### Structures and sequences

| tool | description |
|---|---|
| `pdb_entry` | PDB metadata and its **primary citation**, resolving the DOI by title when RCSB omits it. |
| `uniprot_entry` | Sequence, annotated features, and cross-referenced PDB entries. |

### Administration

| tool | description |
|---|---|
| `configure` | Contact address, library path, confidential mode, optional API keys. |
| `audit_log` | Every request scilib has made, most recent last. |

## How full-text resolution works

`lit_fetch` works down an ordered ladder and stops at the first route that
returns a real document. The order is by fidelity, not convenience.

1. **Already on disk.** No network request.
2. **Europe PMC `fullTextXML`.** Sectioned JATS, the highest-fidelity form.
3. **PubMed Central OA subset** via the AWS Open Data bucket, preferring XML,
   then text, then PDF.
4. **Unpaywall**, publisher open access first, then repository copies.
5. **OpenAlex** best open-access location.
6. **Preprint** on bioRxiv, medRxiv or arXiv, clearly labelled as not the
   version of record.
7. **No open copy.** Reports this honestly and drafts an author request.

XML is preferred over PDF because it preserves section structure, which is what
makes `lib_read(paper, section="surface plasmon resonance")` a real operation. A
PDF flattens into an undifferentiated block of text.

A route that promises a PDF and returns HTML is a login wall, not a paper.
scilib detects this and moves to the next route rather than storing it.

## Data, privacy and provenance

- **Everything stays local.** The library, the index and the logs are files on
  your machine. scilib has no server component and no telemetry.
- **Every file is stamped** with its source, source URL, licence and SHA-256, so
  a figure or a number quoted from a paper can be traced to the exact bytes it
  came from.
- **Licences are recorded, not assumed.** Reusing a figure requires the licence,
  not merely the citation. `lib_status` breaks holdings down by licence.
- **Outbound requests are logged** to `<library>/outbound_queries.log`. Read it
  with `audit_log()` at any time.
- **Rate limiting is shared across processes** through a lock file, so several
  instances, or several colleagues behind one institutional NAT, queue against a
  single clock rather than collectively tripping a limit.

## Sources

All sources are public, documented, and used as their operators intend.

| source | coverage | notes |
|---|---|---|
| [OpenAlex](https://openalex.org) | ~250M works, all disciplines | CC0, no key required |
| [Europe PMC](https://europepmc.org) | ~45M biomedical records | ~6M with open full text as JATS |
| [PubMed Central OA subset](https://registry.opendata.aws/ncbi-pmc) | open-access articles | via the NCBI Cloud Service, a permitted automated route |
| [Crossref](https://www.crossref.org) | DOI registration metadata | authoritative licence and funder data |
| [Unpaywall](https://unpaywall.org) | ~50M DOIs | legal free copies; requires a contact email |
| [Semantic Scholar](https://www.semanticscholar.org) | citation graph | open-PDF pointers, optional free key |
| [bioRxiv / medRxiv](https://www.biorxiv.org) | preprints | includes published-version mapping |
| [arXiv](https://arxiv.org) | preprints | one request per three seconds |
| [DOAJ](https://doaj.org) | fully open-access journals | |
| [OpenAIRE](https://www.openaire.eu) | European repositories | |
| [CORE](https://core.ac.uk) | ~300M repository records | free API key required |
| [RCSB PDB](https://www.rcsb.org), [UniProt](https://www.uniprot.org), [AlphaFold DB](https://alphafold.ebi.ac.uk) | structures and sequences | |

PMC content carries the attribution its terms require: NIH NLM NCBI PubMed
Central (PMC) Article Datasets, <https://registry.opendata.aws/ncbi-pmc>.

## Troubleshooting

**The server shows as failed or disconnected.**
Run it directly to see the error, since MCP clients usually report only that the
connection closed:

```bash
.venv/bin/python scilib/server.py < /dev/null
```

It should produce no output and wait. Any traceback is the real cause.

**`lib_search` returns nothing.**
Check that something is actually indexed with `lib_status()`. If `chunks` is 0,
run `lib_index()` over your paper directories first.

**A PDF indexed with no text.**
It is almost certainly a scan without OCR. These are reported by name in the
`failures` list rather than counted as successes. Run OCR over them, for example
with `ocrmypdf`, and re-index.

**Searches return fewer results than expected.**
`lib_search` accepts FTS5 syntax: `"exact phrase"`, `AND`, `OR`, `NOT`, and
`prefix*`. A query that FTS5 cannot parse is retried in a quoted form rather
than failing, which can broaden it. Matching happens at chunk level first and
falls back to document level, so terms in different sections of one paper are
still found.

**Rate-limit errors.**
Set a contact address with `configure(email=...)`, which grants access to the
polite pools. arXiv answers a rate limit with HTTP 406 rather than 429, which
can look like a malformed query; scilib backs off and retries automatically.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). In short: new sources are welcome if
they are public APIs whose terms permit programmatic use. Additions that bypass
access controls are out of scope and will not be merged.

Run the offline test suite before opening a pull request:

```bash
.venv/bin/python tests/test_offline.py
```

Every test corresponds to a bug that actually occurred. If you fix one, add the
test that would have caught it.

## Acknowledgements

scilib is a thin client. Everything it is useful for was built by other people,
most of it public infrastructure funded by grants and sustained by non-profits.
Citations are a large part of how that funding is justified, so if scilib
contributes to published work, please cite the sources it drew on rather than
only this tool.

| source | citation |
|---|---|
| OpenAlex | Priem J, Piwowar H, Orr R. OpenAlex: A fully-open index of scholarly works, authors, venues, institutions, and concepts. arXiv 2022. [10.48550/arXiv.2205.01833](https://doi.org/10.48550/arXiv.2205.01833) |
| Europe PMC | Ferguson C, et al. Europe PMC in 2020. *Nucleic Acids Research* 2020. [10.1093/nar/gkaa994](https://doi.org/10.1093/nar/gkaa994) |
| PubMed Central | NIH NLM NCBI PubMed Central (PMC) Article Datasets, accessed via the AWS Open Data registry. <https://registry.opendata.aws/ncbi-pmc> |
| Crossref | Hendricks G, et al. Crossref: The sustainable source of community-owned scholarly metadata. *Quantitative Science Studies* 2020. [10.1162/qss_a_00022](https://doi.org/10.1162/qss_a_00022) |
| Unpaywall | Piwowar H, et al. The state of OA: a large-scale analysis of the prevalence and impact of Open Access articles. *PeerJ* 2018. [10.7717/peerj.4375](https://doi.org/10.7717/peerj.4375) |
| Semantic Scholar | Kinney R, et al. The Semantic Scholar Open Data Platform. arXiv 2023. [10.48550/arXiv.2301.10140](https://doi.org/10.48550/arXiv.2301.10140) |
| bioRxiv | Sever R, et al. bioRxiv: the preprint server for biology. *bioRxiv* 2019. [10.1101/833400](https://doi.org/10.1101/833400) |
| CORE | Knoth P, Zdrahal Z. CORE: Three Access Levels to Underpin Open Access. *D-Lib Magazine* 2012. [10.1045/november2012-knoth](https://doi.org/10.1045/november2012-knoth) |
| OpenAIRE | Manghi P, et al. The OpenAIRE Research Graph Data Model. 2019. [10.5281/zenodo.2643199](https://doi.org/10.5281/zenodo.2643199) |
| RCSB PDB | Berman HM, et al. The Protein Data Bank. *Nucleic Acids Research* 2000. [10.1093/nar/28.1.235](https://doi.org/10.1093/nar/28.1.235) |
| UniProt | The UniProt Consortium. UniProt: the Universal Protein Knowledgebase in 2025. *Nucleic Acids Research* 2024. [10.1093/nar/gkae1010](https://doi.org/10.1093/nar/gkae1010) |
| AlphaFold DB | Varadi M, et al. AlphaFold Protein Structure Database in 2024. *Nucleic Acids Research* 2023. [10.1093/nar/gkad1011](https://doi.org/10.1093/nar/gkad1011) |
| arXiv, DOAJ | No canonical citation; both are acknowledged here with thanks. |

Every DOI above was verified against Crossref and OpenAlex before being written,
rather than recalled. Four of an initial set of remembered DOIs turned out to
point at unrelated papers, which is the argument for checking them.

Built on the [Model Context Protocol](https://modelcontextprotocol.io) Python
SDK (MIT) and [httpx](https://www.python-httpx.org) (BSD-3-Clause).

## Licence

[MIT](LICENSE) for the code.

Retrieved content keeps whatever licence it arrived with. That licence is
recorded per file and reported by `lib_status`. scilib does not relicense
anything it downloads, and it is your responsibility to honour the terms
attached to each item, particularly when reusing figures.
