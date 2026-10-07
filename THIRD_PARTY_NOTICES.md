# Third-party software

Live Interpreter's own source is licensed under Apache-2.0. Dependencies retain their original licenses.

| Component | Use | License |
| --- | --- | --- |
| pypdfium2 | PDF text extraction and rendering | Apache-2.0 or BSD-3-Clause; included PDFium and its dependencies have separate notices |
| PDFium | Rendering engine provided by pypdfium2 | BSD-style license and bundled third-party notices |
| Pillow | JPEG page output | MIT-CMU and bundled codec notices |
| amazon-transcribe, awscrt, boto3, botocore | AWS clients | Apache-2.0 |
| aiohttp | HTTP and WebSocket server | Apache-2.0 AND MIT |
| qrcode | Attendee QR code | BSD |
| Pandoc | Sandboxed document conversion | GPL-2.0-or-later |
| aws-cdk-lib, constructs | Optional infrastructure tooling | Apache-2.0 |
| Playwright | Development browser checks | Apache-2.0 |
| ReportLab | Synthetic PDF fixtures for development | BSD |

The public ZIP contains project source, not these dependency distributions. Installation and container builds obtain dependencies from their original package distributions. Preserve their license files and notices when redistributing an installed environment or container. Review the obligations of the actual binaries included, including source availability requirements for GPL components.

pypdfium2 wheels include license files in their distribution metadata under `licenses/LICENSES` and platform-specific `BUILD_LICENSES`, as well as in the PDFium package. Do not remove these directories from installed environments or images. The Python source, PDFium engine, third-party engine components and documentation do not all use the same license.

Pandoc runs as a separate executable. Reader and writer IO is restricted with `--sandbox`, images and raw nodes are removed before Word conversion, and user templates or filters are not accepted. This is a security boundary, not a change to Pandoc's license.

PyMuPDF is not a dependency of this release.

Original projects and license information:

- https://github.com/pypdfium2-team/pypdfium2
- https://pdfium.googlesource.com/pdfium/+/refs/heads/main/LICENSE
- https://github.com/python-pillow/Pillow/blob/main/LICENSE
- https://github.com/awslabs/amazon-transcribe-streaming-sdk
- https://github.com/boto/boto3
- https://github.com/aio-libs/aiohttp
- https://github.com/lincolnloop/python-qrcode
- https://github.com/jgm/pandoc/blob/main/COPYING.md
- https://github.com/aws/aws-cdk
- https://github.com/microsoft/playwright-python
- https://pypi.org/project/reportlab/
