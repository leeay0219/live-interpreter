"""Small synthetic PDFs for local tests; no user material or bundled fonts."""
import io
import textwrap

from reportlab.pdfgen import canvas


def pdf_bytes(text="Northstar", pages=1, size=(595, 842), rotation=0, image=None):
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=size, invariant=True)
    for _ in range(pages):
        pdf.setPageRotation(rotation)
        if image:
            pdf.drawImage(image, 20, 20, width=100, height=100)
        pdf.setFont("Helvetica", 10)
        y = size[1] - 40
        for line in textwrap.wrap(text, 85):
            pdf.drawString(30, y, line)
            y -= 12
        pdf.showPage()
    pdf.save()
    return output.getvalue()
