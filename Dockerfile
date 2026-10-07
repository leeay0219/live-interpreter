# Live caption server for AWS (ECS on Fargate, ARM64). Credentials come from the ECS task role.
FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app
# Official Pandoc embeds the data files needed for Word output in --sandbox.
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
 && case "$TARGETARCH" in \
      amd64) checksum=91903ff19f1b1d4db4129797c7e18f71212990d7394fcff1787719aebf04e372 ;; \
      arm64) checksum=9c9165d5eb627b2ccc12868478f847487bda9dc043aa09a964105d5aebfc7b79 ;; \
      *) echo "Unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac \
 && curl --fail --location --retry 3 "https://github.com/jgm/pandoc/releases/download/3.12/pandoc-3.12-1-${TARGETARCH}.deb" -o /tmp/pandoc.deb \
 && echo "$checksum  /tmp/pandoc.deb" | sha256sum --check - \
 && apt-get install -y --no-install-recommends /tmp/pandoc.deb \
 && rm -f /tmp/pandoc.deb && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
# amazon-transcribe 0.6.4 pins awscrt~=0.26 but runs fine on the newer awscrt in requirements.txt (same set as the laptop)
RUN pip install --no-cache-dir --no-deps amazon-transcribe==0.6.4 \
 && grep -v amazon-transcribe requirements.txt > /tmp/req.txt && pip install --no-cache-dir -r /tmp/req.txt
COPY VERSION LICENSE NOTICE THIRD_PARTY_NOTICES.md server.py settings.py logtext.py captioning.py translation.py live.py access.py routes_live.py routes_materials.py routes_session.py routes_records.py security.py event.py deck_context.py materials.py pdf_worker.py workloads.py ./
COPY events ./events
COPY glossary ./glossary
COPY skills ./skills
COPY postprocess ./postprocess
COPY static ./static
# Fail the build if this Pandoc binary cannot produce Word inside its IO sandbox.
RUN python - <<'PY'
import subprocess
from postprocess.document import render_docx
try:
    assert render_docx("# Document").startswith(b"PK")
except subprocess.CalledProcessError as error:
    print(error.stderr.decode(), flush=True)
    raise
PY
RUN useradd --uid 10001 app
USER app
EXPOSE 8080
# the event is chosen with the EVENT environment variable (events/<EVENT>.toml)
CMD ["python", "server.py", "--host", "0.0.0.0", "--auto-lang"]
