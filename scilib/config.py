"""Runtime configuration for scilib.

Everything a second user must change lives here and is read from the
environment or ~/.config/scilib/config.json, never hardcoded. The package is
meant to be shared, so no path, address or credential is baked into the source.
"""
from __future__ import annotations
import json, os, pathlib

APP = "scilib"
CONFIG_DIR = pathlib.Path(os.environ.get("SCILIB_CONFIG_DIR",
                                         pathlib.Path.home() / ".config" / APP))
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS = {
    # Contact address sent to the "polite pools" of Crossref, Unpaywall,
    # OpenAlex and NCBI. These APIs ask for it and give higher rate limits in
    # return. It is the only personal datum that leaves the machine.
    "email": "",
    # Where fetched papers and the index live.
    "library": str(pathlib.Path.home() / "scilib-library"),
    # Extra directories to index read-only, i.e. papers already on disk.
    "extra_paths": [],
    # Optional free API keys. None is required; each only raises a rate limit.
    "core_api_key": "",          # core.ac.uk, free, for repository search
    "ncbi_api_key": "",          # eutils, free, 3/s -> 10/s
    "s2_api_key": "",            # Semantic Scholar, free on request
    # Log every outbound query so the user can audit what left the machine.
    "audit_log": True,
    # Refuse to send free-text queries outside the machine unless explicitly
    # allowed per call. Identifier lookups (DOI/PMID) are always allowed.
    "confidential_mode": False,
    "timeout": 45,
    "max_pdf_mb": 80,
}


def _load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text()))
        except Exception:
            pass
    for k in cfg:
        env = os.environ.get(f"SCILIB_{k.upper()}")
        if env:
            cfg[k] = json.loads(env) if k == "extra_paths" else (
                env.lower() in ("1", "true", "yes") if isinstance(cfg[k], bool) else env)
    return cfg


CFG = _load()


def save(updates: dict) -> dict:
    """Persist config changes; used by the `configure` tool."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cur = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    cur.update(updates)
    CONFIG_FILE.write_text(json.dumps(cur, indent=2))
    CFG.update(updates)
    return cur


def library() -> pathlib.Path:
    p = pathlib.Path(CFG["library"]).expanduser()
    (p / "pdf").mkdir(parents=True, exist_ok=True)
    (p / "fulltext").mkdir(parents=True, exist_ok=True)
    return p


def contact() -> str:
    """The mailto used for polite pools. Empty is legal but rate-limited."""
    return CFG.get("email") or ""


def user_agent() -> str:
    c = contact()
    base = "scilib-mcp/0.1 (open-access literature client; +https://github.com/)"
    return f"{base} mailto:{c}" if c else base
