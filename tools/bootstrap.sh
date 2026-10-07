#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v uv >/dev/null || ! command -v pandoc >/dev/null; then
  echo "Install uv and Pandoc, then run tools/bootstrap.sh again." >&2
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  uv venv --python 3.12 .venv
fi
# Match Docker: the streaming SDK declares an older CRT pin, while this app uses
# the newer CRT for login credentials. Install its fixed version without that pin.
uv pip install --python .venv/bin/python --no-deps amazon-transcribe==0.6.4
runtime_requirements=$(mktemp)
trap 'rm -f "$runtime_requirements"' EXIT
awk '!/^amazon-transcribe[=<>]/' requirements.txt > "$runtime_requirements"
uv pip install --python .venv/bin/python -r "$runtime_requirements"
if [ "${1:-}" = "--dev" ]; then
  uv pip install --python .venv/bin/python -r requirements-dev.txt
fi
.venv/bin/python -c 'import aiohttp, boto3, botocore, amazon_transcribe, awscrt, pypdfium2, PIL, qrcode'
.venv/bin/python - <<'PY'
import subprocess
from postprocess.document import render_docx
try:
    assert render_docx("# Document").startswith(b"PK")
except (subprocess.SubprocessError, OSError, AssertionError):
    raise SystemExit(
        "Pandoc cannot create Word output in its sandbox. Install the official "
        "Pandoc release with embedded data files, then rerun tools/bootstrap.sh.")
PY
echo "Environment ready. Start with ./start.sh --port 8083"
