"""Generate a synthetic PDF and compare none/text/multimodal translation context.

    .venv/bin/python tools/evaluate_context.py          # fixtures only, no AWS calls
    .venv/bin/python tools/evaluate_context.py --run    # metered Bedrock calls; outputs contain synthetic text only

The lexical checks are regression signals, not a translation-quality score. Review each output using its rubric.
"""
import argparse
import asyncio
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import boto3
from botocore.config import Config
import pypdfium2 as pdfium
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
import deck_context as dc
import event
import materials
import server


def fixtures(directory, dataset):
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "evaluation.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(960, 540), invariant=True)
    for index, text in enumerate(dataset["pages"]):
        pdf.setFont("Helvetica", 24)
        for line, content in enumerate(text.splitlines()):
            pdf.drawString(45, 490 - line * 36, content)
        if index == 2:
            for x, label in [(80, "Gateway"), (360, "Retriever"), (640, "Document store")]:
                pdf.setStrokeColorRGB(.1, .2, .35)
                pdf.setFillColorRGB(.9, .94, .98)
                pdf.rect(x, 100, 220, 75, fill=1)
                pdf.setFillColorRGB(.1, .2, .35)
                pdf.setFont("Helvetica", 17)
                pdf.drawCentredString(x + 110, 130, label)
            for x in (300, 580):
                pdf.line(x, 138, x + 60, 138)
                pdf.line(x + 60, 138, x + 49, 131)
                pdf.line(x + 60, 138, x + 49, 145)
        pdf.showPage()
    pdf.save()
    # Second page is an image-only slide. Native text analysis must not pretend to read its measurements.
    mixed = directory / "evaluation-mixed.pdf"
    with pdfium.PdfDocument(source) as source_pdf, pdfium.PdfDocument.new() as combined:
        combined.import_pages(source_pdf, pages=[0])
        image_pdf = directory / "evaluation-image.pdf"
        page = source_pdf[1]
        bitmap = page.render(scale=1)
        try:
            with bitmap.to_pil() as image:
                raster = canvas.Canvas(str(image_pdf), pagesize=(960, 540), invariant=True)
                raster.drawImage(ImageReader(image), 0, 0, width=960, height=540)
                raster.showPage()
                raster.save()
        finally:
            bitmap.close()
            page.close()
        with pdfium.PdfDocument(image_pdf) as image_document:
            combined.import_pages(image_document)
        combined.import_pages(source_pdf, pages=list(range(2, len(source_pdf))))
        combined.save(mixed)
    return mixed


async def evaluate(options, dataset, pdf):
    session = boto3.Session(region_name=options.region)
    client = session.client("bedrock-runtime", config=Config(connect_timeout=5, read_timeout=60,
                                                            retries={"total_max_attempts": 1}))
    _, texts, images = materials.render_pdf(pdf.read_bytes(), options.output / f"pages-{time.time_ns()}", 10)
    usage = []
    print("Analyzing native text", flush=True)
    text_brief = await dc.analyze(client, options.model, texts, lambda _: None, usage.append)
    print("Analyzing page images", flush=True)
    visual = await dc.analyze(client, options.model, texts, print, usage.append, images=images)
    results = []
    for mode in ("none", "text", "multimodal"):
        tr = server.Translator(session, options.region, options.model, "claude", event.load("general"), False)
        selected = None if mode == "none" else text_brief if mode == "text" else {
            k: visual[k] for k in ("summary", "people", "terms")}
        tr.set_document_brief(selected)
        for case in dataset["cases"]:
            tr.reset_conversation()
            number = case["page"]
            if case.get("focus") == "qa":
                tr.set_topic("Q&A: prioritize current speech; document background is optional. Do not add document facts.")
            elif mode == "multimodal":
                page = next((p for p in visual["page_contexts"] if p["page"] == number), {})
                tr.set_topic(json.dumps(page, ensure_ascii=False))
            else:
                tr.set_topic(texts[number - 1][:600] if mode == "text" else "")
            started = time.monotonic()
            # Deliberately use one model, without fallback, so modes are compared on the same model.
            output = await asyncio.to_thread(tr._claude, case["speech"], "en", "ko", [])
            required = [x for x in case["required"] if x not in output]
            forbidden = [x for x in case["forbidden"] if x in output]
            result = {"mode": mode, **case, "translation": output,
                      "latency_ms": round((time.monotonic() - started) * 1000),
                      "missing": required, "unexpected": forbidden, "lexical_pass": not required and not forbidden}
            results.append(result)
            print(f"{mode}: {case['id']}: {'pass' if result['lexical_pass'] else 'review'}", flush=True)
    report = {"model": options.model, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "scope": "Synthetic transcript-to-translation; no audio/ASR; lexical checks require human review.",
              "analysis_usage": usage, "visual_failed_pages": visual["failed_pages"], "results": results,
              "summary": {mode: {"lexical_pass": sum(r["lexical_pass"] for r in results if r["mode"] == mode),
                                  "cases": len(dataset["cases"]),
                                  "median_ms": statistics.median(r["latency_ms"] for r in results if r["mode"] == mode)}
                          for mode in ("none", "text", "multimodal")}}
    (options.output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--model", default=server.DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, default=Path("out/evaluation"))
    options = parser.parse_args()
    dataset = json.loads((Path(__file__).resolve().parents[1] / "tests/fixtures/interpretation_cases.json").read_text())
    pdf = fixtures(options.output, dataset)
    print(f"Fixture: {pdf}", flush=True)
    if options.run:
        asyncio.run(evaluate(options, dataset, pdf))


if __name__ == "__main__":
    main()
