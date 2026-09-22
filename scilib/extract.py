"""Turn a fetched file into text, preserving section structure where it exists.

JATS XML keeps headings, so "what did the Methods say about salt" is a real
query. A PDF does not, so it degrades to a flat body. Prefer XML for that reason
alone, independent of file size.
"""
from __future__ import annotations
import pathlib, re, subprocess, shutil
from xml.etree import ElementTree as ET


def pdf_to_text(path: pathlib.Path) -> str:
    """Extract with poppler's pdftotext. Returns '' when unavailable rather than
    raising, so a PDF that cannot be parsed still gets stored and recorded."""
    if not shutil.which("pdftotext"):
        return ""
    try:
        out = subprocess.run(["pdftotext", "-q", "-enc", "UTF-8", str(path), "-"],
                             capture_output=True, timeout=180)
        return out.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def _text_of(el) -> str:
    """Flatten an element, dropping tables/figures inline but keeping their
    captions, and inserting spaces so adjacent tags do not weld words together.

    The welding matters: '<italic>E. coli</italic>DnaA' becomes 'E. coliDnaA'
    without it, which breaks both reading and n-gram matching against the source.
    """
    parts = []
    for node in el.iter():
        if node.tag in ("table-wrap", "graphic", "inline-graphic"):
            continue
        if node.text:
            parts.append(node.text)
        if node.tail:
            parts.append(node.tail)
    s = " ".join(parts)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def jats_sections(xml_bytes: bytes) -> dict:
    """Parse JATS into {title, abstract, sections:[{heading, text}], refs:[...]}"""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return {}
    out = {"title": "", "abstract": "", "sections": [], "refs": []}

    t = root.find(".//article-title")
    if t is not None:
        out["title"] = _text_of(t)
    ab = root.find(".//abstract")
    if ab is not None:
        out["abstract"] = _text_of(ab)

    body = root.find(".//body")
    if body is not None:
        for sec in body.findall(".//sec"):
            ttl = sec.find("title")
            heading = _text_of(ttl) if ttl is not None else ""
            paras = [_text_of(p) for p in sec.findall("p")]
            text = "\n\n".join(x for x in paras if x)
            if heading or text:
                out["sections"].append({"heading": heading, "text": text})
        if not out["sections"]:
            out["sections"] = [{"heading": "Body", "text": _text_of(body)}]

    for ref in root.findall(".//ref-list/ref"):
        cit = _text_of(ref)
        pid = ref.find(".//pub-id")
        out["refs"].append({"text": cit[:400],
                            "id": (pid.text or "") if pid is not None else "",
                            "id_type": (pid.get("pub-id-type", "") if pid is not None else "")})
    return out


def to_plain(parsed: dict) -> str:
    """Flatten a parsed JATS record for indexing, keeping headings as anchors."""
    L = []
    if parsed.get("title"):
        L.append(parsed["title"])
    if parsed.get("abstract"):
        L.append("ABSTRACT\n\n" + parsed["abstract"])
    for s in parsed.get("sections", []):
        L.append((s["heading"].upper() + "\n\n" if s["heading"] else "") + s["text"])
    return "\n\n".join(L)


def sniff(data: bytes) -> str:
    """Identify a payload. A PDF that is not a PDF is a truncated download or an
    HTML error page, and storing it as a paper is how a library quietly fills
    with junk."""
    head = data[:512].lstrip()
    if data[:5] == b"%PDF-":
        return "pdf"
    if head[:5] in (b"<?xml", b"<!DOC") or head[:8].startswith(b"<article"):
        return "xml" if b"<article" in data[:4000] or b"<!DOCTYPE article" in data[:4000] else "html"
    if head[:1] == b"<":
        return "html"
    return "txt"
