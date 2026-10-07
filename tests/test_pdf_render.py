"""Actual PDFium rendering and extraction with bounded output."""
import io
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image
from reportlab.lib.utils import ImageReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import materials
from pdf_fixture import pdf_bytes


class PdfTests(unittest.TestCase):
    def test_text_and_page_order_survive_rendering(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages, texts, images = materials.render_pdf(pdf_bytes("Northstar Mina Park", pages=2), Path(tmp) / "pages", 5)
            self.assertEqual(len(pages), 2)
            self.assertEqual(texts, ["Northstar Mina Park"] * 2)
            self.assertEqual([Path(p).name for p in images], ["1.jpg", "2.jpg"])
            self.assertEqual(pages[0]["url"], "/decks/pages/1.jpg")
            for path in images:
                with Image.open(path) as image:
                    self.assertEqual(image.format, "JPEG")
                    self.assertLessEqual(image.width, 1920)
                    self.assertLessEqual(image.height, 2400)

    def test_image_only_rotated_and_tall_pages_stay_bounded(self):
        with tempfile.TemporaryDirectory() as tmp, Image.new("RGB", (16, 16), "orange") as marker:
            output = io.BytesIO()
            marker.save(output, format="PNG")
            cases = [
                pdf_bytes("", image=ImageReader(output)),
                pdf_bytes("Rotated", size=(900, 500), rotation=90),
                pdf_bytes("Tall", size=(100, 12000)),
            ]
            for index, data in enumerate(cases):
                _, texts, images = materials.render_pdf(data, Path(tmp) / str(index), 5)
                if index == 0:
                    self.assertEqual(texts, [""])
                with Image.open(images[0]) as image:
                    self.assertLessEqual(image.width, 1920)
                    self.assertLessEqual(image.height, 2400)

    def test_invalid_and_excess_pages_leave_no_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index, data in enumerate((b"not a PDF", pdf_bytes(pages=2))):
                directory = Path(tmp) / str(index)
                with self.assertRaises(Exception):
                    materials.render_pdf(data, directory, 1)
                self.assertFalse(directory.exists())


if __name__ == "__main__":
    unittest.main()
