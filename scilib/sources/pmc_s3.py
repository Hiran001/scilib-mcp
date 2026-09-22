"""PMC Open Access subset via the AWS Open Data bucket.

NCBI is explicit that only the Cloud Service, OAI-PMH, E-Utilities and BioC API
may be used for automated retrieval of PMC content; scraping article pages is
not permitted. This module uses the Cloud Service bucket, which is public and
needs no credentials.

Bucket: s3://pmc-oa-opendata (us-east-1)
Layout: PMC<id>.<version>/PMC<id>.<version>.{pdf,txt,xml}
Attribution, per the dataset terms: NIH NLM NCBI PubMed Central (PMC) Article
Datasets, https://registry.opendata.aws/ncbi-pmc.
"""
from __future__ import annotations
import re
from .. import net

BASE = "https://pmc-oa-opendata.s3.amazonaws.com"


def list_keys(pmcid: str) -> list[str]:
    if not pmcid:
        return []
    xml = net.get(f"{BASE}/?list-type=2&prefix={pmcid}.", kind="list:pmc-s3")
    if not xml:
        return []
    return re.findall(r"<Key>([^<]+)</Key>", xml.decode("utf-8", "replace"))


def fetch(pmcid: str, prefer=("xml", "txt", "pdf")) -> tuple[bytes, str, str] | None:
    """Return (bytes, kind, url) for the best available representation.

    XML first: it keeps section structure, which a PDF loses.
    """
    keys = list_keys(pmcid)
    if not keys:
        return None
    for ext in prefer:
        for k in keys:
            if k.endswith("." + ext):
                url = f"{BASE}/{k}"
                data = net.get(url, kind="fetch:pmc-s3")
                if not data:
                    continue
                # A PDF that is not a PDF is a truncated or error payload.
                if ext == "pdf" and data[:5] != b"%PDF-":
                    continue
                return data, ext, url
    return None
