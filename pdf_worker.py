"""One bounded PDF rendering job. No AWS clients or document content in error output."""
import json
import sys
from pathlib import Path

from materials import MAX_UPLOAD_BYTES, render_pdf


if __name__ == "__main__":
    try:
        data = sys.stdin.buffer.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError("upload too large")
        pages, texts, images = render_pdf(data, Path(sys.argv[1]), int(sys.argv[2]))
        print(json.dumps({"pages": pages, "texts": texts, "images": images}, ensure_ascii=False))
    except Exception:
        sys.exit(1)
