"""Document conversion for untrusted session text, without resource loading."""
import json
import re
import subprocess
import tempfile
from urllib.parse import urlsplit


def literal_markdown(text):
    """Represent transcript text literally, preserving the stored source."""
    return re.sub(r"([\\`*_{}\[\]<>()#+\-.!|~$])", r"\\\1", text).replace("\n", "  \n")


def _safe_nodes(value):
    if isinstance(value, list):
        return [_safe_nodes(item) for item in value]
    if not isinstance(value, dict):
        return value
    kind, content = value.get("t"), value.get("c")
    if kind in ("RawBlock", "RawInline"):
        return {"t": "Str", "c": ""} if kind == "RawInline" else {"t": "Plain", "c": []}
    if kind == "Image":
        return {"t": "Span", "c": [["", [], []], _safe_nodes(content[1])]}
    if kind == "Link":
        target = content[2][0]
        try:
            permitted = target.startswith("#") or urlsplit(target).scheme.lower() in ("https", "http")
        except ValueError:
            permitted = False
        if not permitted:
            return {"t": "Span", "c": [["", [], []], _safe_nodes(content[1])]}
    return {key: _safe_nodes(item) for key, item in value.items()}


def render_docx(markdown):
    """Use stdin, a private working directory and Pandoc's IO sandbox in both passes.

    Images and raw nodes are removed before writing. No filters, user templates,
    reference documents, resource paths or input file paths are accepted.
    Pandoc must be built with embedded data files, as in its official releases.
    """
    with tempfile.TemporaryDirectory(prefix="writeup-") as directory:
        parsed = subprocess.run(
            ["pandoc", "--sandbox", "--from=gfm-raw_html", "--to=json"],
            input=markdown, text=True, capture_output=True, check=True, timeout=20, cwd=directory)
        ast = json.loads(parsed.stdout)
        ast["meta"] = {}
        ast["blocks"] = _safe_nodes(ast["blocks"])
        return subprocess.run(
            ["pandoc", "--sandbox", "--from=json", "--to=docx"],
            input=json.dumps(ast).encode(), capture_output=True, check=True, timeout=40, cwd=directory).stdout
