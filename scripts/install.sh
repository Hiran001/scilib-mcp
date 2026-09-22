#!/usr/bin/env bash
# Set up scilib in a self-contained virtualenv.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

PY="${PYTHON:-python3}"
echo "==> python: $($PY -V)"

if ! command -v pdftotext >/dev/null 2>&1; then
  echo "!! pdftotext not found. PDF indexing will be skipped until you install it:"
  echo "     Debian/Ubuntu: sudo apt install poppler-utils"
  echo "     macOS:         brew install poppler"
fi

echo "==> creating venv at $HERE/.venv"
"$PY" -m venv .venv
./.venv/bin/pip -q install --upgrade pip
./.venv/bin/pip -q install "mcp" "httpx"

echo "==> verifying"
./.venv/bin/python - <<'PYCHK'
import sys, pathlib
sys.path.insert(0, str(pathlib.Path.cwd()))
import scilib.server  # noqa: F401
print("   server imports cleanly")
PYCHK

cat <<MSG

==> installed.

Register with Claude Code:

  claude mcp add scilib -- $HERE/.venv/bin/python $HERE/scilib/server.py

Or add to an MCP client config:

  {"mcpServers": {"scilib": {"command": "$HERE/.venv/bin/python",
                             "args": ["$HERE/scilib/server.py"]}}}

Then set your contact address (required by Unpaywall, and it raises rate
limits on Crossref/OpenAlex/NCBI):

  configure(email="you@university.edu")

And index the papers you already have:

  lib_index("/path/to/your/papers")
MSG
