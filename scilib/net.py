"""Polite HTTP client.

Three things this enforces, all of which matter for a tool shared across a lab:

1. **Per-host rate limiting.** These APIs are free and run on public money.
   Hammering them is how a whole institution gets blocked, which is the outcome
   this tool exists to avoid.
2. **Identification.** Crossref, Unpaywall, OpenAlex and NCBI all ask for a
   contact address and grant a faster pool in return.
3. **An audit trail of everything that leaves the machine.** Identifier lookups
   are innocuous; free-text queries can carry unpublished reasoning. Both are
   logged so the user can see exactly what was sent.
"""
from __future__ import annotations
import json, logging, pathlib, threading, time, datetime
import httpx
from . import config

# httpx logs every request at INFO. On a stdio MCP server that noise reaches the
# client's log, and anything that escaped to stdout would corrupt the protocol
# stream outright. Silence the transport loggers at import.
for _n in ("httpx", "httpcore", "httpcore.http11", "httpcore.connection"):
    logging.getLogger(_n).setLevel(logging.WARNING)

# Minimum seconds between requests to each host. NCBI is the strict one: 3/s
# without an API key, and they do enforce it.
_MIN_INTERVAL = {
    "eutils.ncbi.nlm.nih.gov": 0.34,
    "api.crossref.org": 0.10,
    "api.openalex.org": 0.10,
    "api.unpaywall.org": 0.10,
    "www.ebi.ac.uk": 0.15,
    "api.semanticscholar.org": 3.20,   # anonymous pool 429s well below its nominal 1/s
    "api.core.ac.uk": 0.60,
    "api.biorxiv.org": 0.25,
    "export.arxiv.org": 3.60,          # arXiv asks for 1 req / 3 s; it replies 406 when exceeded
    "pmc-oa-opendata.s3.amazonaws.com": 0.05,
}
_DEFAULT_INTERVAL = 0.50
_last: dict[str, float] = {}
# Last transport failure, so a None return can be explained rather than guessed.
LAST_ERROR: dict[str, str] = {"url": "", "error": ""}
_lock = threading.Lock()


def _throttle(host: str) -> None:
    """Rate limit per host, shared across processes.

    An in-memory limiter is not enough. These services throttle by IP, so two
    invocations of this tool, or two people in the same lab behind one
    institutional NAT, collectively exceed the limit while each believes it is
    well behaved. arXiv answers that with a 406 that looks like a malformed
    query. The timestamp therefore lives in a file under an flock so every
    process on the machine queues against the same clock.
    """
    gap = _MIN_INTERVAL.get(host, _DEFAULT_INTERVAL)
    with _lock:
        prev = _last.get(host, 0.0)
        wait = gap - (time.monotonic() - prev)
        if wait > 0:
            time.sleep(wait)
        _last[host] = time.monotonic()
    try:
        import fcntl
        d = config.library() / ".throttle"
        d.mkdir(parents=True, exist_ok=True)
        f = d / host.replace("/", "_")
        with open(f, "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.seek(0)
                prev = float((fh.read() or "0").strip() or 0)
                wait = gap - (time.time() - prev)
                if wait > 0:
                    time.sleep(min(wait, gap))
                fh.seek(0); fh.truncate()
                fh.write(str(time.time()))
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except Exception:
        pass        # a throttle that fails must not break the fetch


def audit(kind: str, target: str, detail: str = "") -> None:
    """Append one line to the outbound-query log."""
    if not config.CFG.get("audit_log", True):
        return
    try:
        p = config.library() / "outbound_queries.log"
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with open(p, "a") as fh:
            fh.write(f"{ts}\t{kind}\t{target}\t{detail}\n")
    except Exception:
        pass


class Blocked(RuntimeError):
    """Raised when confidential_mode would let free text leave the machine."""


def guard_freetext(query: str, allow: bool) -> None:
    if config.CFG.get("confidential_mode") and not allow:
        raise Blocked(
            "confidential_mode is on and this is a free-text query, which would "
            "send your wording to an external index. Re-run with allow_external=true "
            "if the phrasing carries nothing unpublished, or search locally instead.")


_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            follow_redirects=True,
            timeout=float(config.CFG.get("timeout", 45)),
            headers={"User-Agent": config.user_agent(),
                     "Accept": "*/*"},
        )
    return _client


def get(url: str, *, params: dict | None = None, kind: str = "http",
        expect_json: bool = False, headers: dict | None = None):
    """GET with throttling and logging. Returns parsed JSON, bytes, or None."""
    host = httpx.URL(url).host or "?"
    audit(kind, url, json.dumps(params, sort_keys=True) if params else "")
    # Retry the codes that mean "you are going too fast" rather than "no".
    # arXiv answers a throttle with 406, not 429, which is easy to misread as a
    # malformed query; NCBI and Crossref use 429/503.
    RETRY = {406, 429, 500, 502, 503, 504}
    r = None
    for attempt in range(4):
        _throttle(host)
        try:
            r = client().get(url, params=params, headers=headers or {})
        except Exception as e:
            if attempt == 3:
                break
            time.sleep(2.0 * (2 ** attempt))
            continue
        if r.status_code in RETRY and attempt < 3:
            time.sleep(2.0 * (2 ** attempt))
            continue
        break
    try:
        if r is None:
            raise RuntimeError("no response after retries")
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        LAST_ERROR["url"], LAST_ERROR["error"] = url, msg
        audit("error", url, msg)
        return {"__error__": msg} if expect_json else None
    if r.status_code >= 400:
        # A failed lookup is an error, not an absence. Record the status so a
        # caller that gets None can find out why instead of defaulting past it.
        msg = f"HTTP {r.status_code}"
        LAST_ERROR["url"], LAST_ERROR["error"] = url, msg
        audit("error", url, msg)
        return {"__error__": msg} if expect_json else None
    if expect_json:
        try:
            return r.json()
        except Exception:
            return {"__error__": "non-JSON response"}
    return r.content


def get_json(url: str, params: dict | None = None, kind: str = "api") -> dict:
    out = get(url, params=params, kind=kind, expect_json=True,
              headers={"Accept": "application/json"})
    return out if isinstance(out, dict) else {"__error__": "unexpected payload"}
