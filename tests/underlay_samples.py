"""Underlay test files made by code (no binary fixtures): a vector PDF with optional-content
layers, a form XObject and text; a rotated one; a scan; a DWFx package; and hostile packages."""

from __future__ import annotations

import io
import zipfile

K = 0.5522847498  # a quarter circle as one cubic


def pdf(objs: list[bytes]) -> bytes:
    out = b"%PDF-1.5\n"
    offs = []
    for i, s in enumerate(objs):
        offs.append(len(out))
        out += b"%d 0 obj\n" % (i + 1) + s + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    return out


def vector_pdf(rotate: int = 0) -> bytes:
    """A4 portrait: Walls (3 segments), Doors (a rectangle), a circle r=40pt at (400,400), text,
    and a form XObject placed at (500,500) at scale 2 holding one 10pt line."""
    r = 40
    k = K * r
    cx, cy = 400, 400
    circ = (
        f"{cx + r} {cy} m {cx + r} {cy + k} {cx + k} {cy + r} {cx} {cy + r} c "
        f"{cx - k} {cy + r} {cx - r} {cy + k} {cx - r} {cy} c "
        f"{cx - r} {cy - k} {cx - k} {cy - r} {cx} {cy - r} c "
        f"{cx + k} {cy - r} {cx + r} {cy - k} {cx + r} {cy} c S"
    )
    content = (
        "0.5 w\n/OC /L1 BDC 72 72 m 300 72 l 300 200 l S EMC\n"
        "/OC /L2 BDC 150 150 50 40 re S EMC\n"
        "BT /F1 12 Tf 100 300 Td (Hello PDF) Tj ET\n/Fm1 Do\n" + circ
    ).encode()
    form = b"q 2 0 0 2 0 0 cm 10 10 m 20 10 l S Q"
    return pdf(
        [
            (
                b"<< /Type /Catalog /Pages 2 0 R /OCProperties << /OCGs [6 0 R 7 0 R] "
                b"/D << /Order [6 0 R 7 0 R] /OFF [] >> >> >>"
            ),
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Rotate %d "
                b"/Resources << /Font << /F1 5 0 R >> /Properties << /L1 6 0 R /L2 7 0 R >> "
                b"/XObject << /Fm1 8 0 R >> >> /Contents 4 0 R >>" % rotate
            ),
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Type /OCG /Name (Walls) >>",
            b"<< /Type /OCG /Name (Doors) >>",
            (
                b"<< /Type /XObject /Subtype /Form /BBox [0 0 595 842] /Matrix [1 0 0 1 500 500] "
                b"/Length %d >>\nstream\n" % len(form) + form + b"\nendstream"
            ),
        ]
    )


def scan_pdf() -> bytes:
    """A page that is one picture (no vectors): what a scanner makes."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(buf, "PDF")
    return buf.getvalue()


XPS = "http://schemas.microsoft.com/xps/2005/06"
PAGE = f"""<?xml version="1.0" encoding="utf-8"?>
<FixedPage xmlns="{XPS}" Width="800" Height="600" xml:lang="en-US">
  <Canvas RenderTransform="1,0,0,1,100,50">
    <Path Data="M 0,0 L 200,0 L 200,100 Z" Stroke="#FF0000FF" StrokeThickness="1"/>
    <Path Data="M 300,200 C 300,222.0914 282.0914,240 260,240 C 237.9086,240 220,222.0914 220,200
       C 220,177.9086 237.9086,160 260,160 C 282.0914,160 300,177.9086 300,200 Z" Stroke="#FF000000"/>
    <Glyphs OriginX="10" OriginY="300" FontRenderingEmSize="24" UnicodeString="Hello DWFx"/>
    <Path Data="M 0,400 L 50,400 L 50,450 L 0,450 Z" Fill="#FF808080"/>
  </Canvas>
  <Path Data="M 0,0 L 10,10"><Path.Fill><ImageBrush ImageSource="/img.png"/></Path.Fill></Path>
</FixedPage>"""


def dwfx(page: str = PAGE, extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "FixedDocumentSequence.fdseq",
            f'<FixedDocumentSequence xmlns="{XPS}"><DocumentReference '
            'Source="Documents/1/FixedDocument.fdoc"/></FixedDocumentSequence>',
        )
        z.writestr(
            "Documents/1/FixedDocument.fdoc",
            f'<FixedDocument xmlns="{XPS}"><PageContent Source="Pages/1.fpage"/></FixedDocument>',
        )
        z.writestr("Documents/1/Pages/1.fpage", page)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def classic_dwf() -> bytes:
    return b"(DWF V06.00)(Layer 1 Walls)L 0,0 10,10(EndOfDWF)"


def dwf_package() -> bytes:
    """A DWF 6 package: a zip with manifest.xml and a .w2d stream."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.xml", "<manifest/>")
        z.writestr("sections/1/graphics.w2d", classic_dwf())
    return buf.getvalue()
