"""Identifier normalisation.

Every source names the same paper differently. Getting this wrong silently
splits one paper into several records, so normalisation is centralised here and
tested rather than reimplemented per source.
"""
from __future__ import annotations
import re

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9<>\[\]]+)", re.I)
PMID_RE = re.compile(r"\b(?:PMID[:\s]*)?(\d{7,8})\b", re.I)
PMCID_RE = re.compile(r"\b(PMC\d{6,9})\b", re.I)
ARXIV_RE = re.compile(r"\b(?:arXiv[:\s]*)?(\d{4}\.\d{4,5})(v\d+)?\b", re.I)


def norm_doi(s: str | None) -> str:
    """Lowercase, strip URL prefix and trailing punctuation.

    Trailing punctuation matters: a DOI harvested from prose usually carries the
    sentence's full stop, and `10.1/x.` and `10.1/x` hash to different records.
    """
    if not s:
        return ""
    s = s.strip()
    s = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    m = DOI_RE.search(s)
    if not m:
        return ""
    # Trailing punctuation from prose. A colon is included: DOIs may contain one
    # internally, but a trailing colon is always the sentence's, and leaving it
    # on splits one paper into two records.
    return m.group(1).rstrip(".,;:)]>\u2019\"'").lower()


def norm_pmid(s: str | None) -> str:
    if not s:
        return ""
    m = PMID_RE.search(str(s).strip())
    return m.group(1) if m else ""


def norm_pmcid(s: str | None) -> str:
    if not s:
        return ""
    s = str(s).strip().upper()
    if s.isdigit():
        return "PMC" + s
    m = PMCID_RE.search(s)
    return m.group(1).upper() if m else ""


def classify(s: str) -> tuple[str, str]:
    """Return (kind, normalised) for a user-supplied identifier.

    kind is one of doi / pmid / pmcid / arxiv / title.
    """
    s = (s or "").strip()
    if not s:
        return "title", ""
    if (d := norm_doi(s)):
        return "doi", d
    # Bare digits are a PMID, never a PMCID. norm_pmcid() will happily prepend
    # "PMC" to any digit string, so it must not be reached before this test or
    # every bare PMID is looked up as a PMC record that does not exist.
    if re.fullmatch(r"(?:PMID[:\s]*)?\d{7,8}", s, re.I) and (p := norm_pmid(s)):
        return "pmid", p
    if "PMC" in s.upper() and (p := norm_pmcid(s)):
        return "pmcid", p
    if (m := ARXIV_RE.fullmatch(s.strip())):
        return "arxiv", m.group(1)
    return "title", s


def slug(title: str, year: str = "", ident: str = "", ext: str = "pdf") -> str:
    """Stable, human-readable filename.

    Uses the widely-seen convention `<year>_<slugged title>_PMID<pmid>.pdf`, so
    files already sitting on disk under that naming are recognised rather than
    re-fetched.
    """
    t = re.sub(r"[^a-z0-9]+", "_", (title or "untitled").lower())[:48].strip("_")
    tag = f"_{ident}" if ident else ""
    return f"{year or 'nd'}_{t}{tag}.{ext}"
