"""One bounded PDF rendering job. No AWS clients or document content in error output."""
import json
import os
import resource
import sys
from pathlib import Path

from materials import MAX_UPLOAD_BYTES, render_pdf

MEMORY_LIMIT = 1024 ** 3  # address space; a 300-page deck at the render scale stays well below it
CPU_SECONDS = 120  # matches the parent's wall-clock timeout


def limit_resources():
    """Set in the worker itself: a preexec_fn is unsafe in the threaded server process."""
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS + 5))
    os.nice(10)


if __name__ == "__main__":
    try:
        limit_resources()
        data = sys.stdin.buffer.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError("upload too large")
        pages, texts, images = render_pdf(data, Path(sys.argv[1]), int(sys.argv[2]))
        print(json.dumps({"pages": pages, "texts": texts, "images": images}, ensure_ascii=False))
    except Exception:
        sys.exit(1)
