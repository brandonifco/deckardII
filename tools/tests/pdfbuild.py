"""Minimal synthetic PDF builder for the source tooling tests.

CI never has Brandon's copy of the rulebook, and it must never need one. These tests
therefore exercise source-slice.py against small PDFs generated here, with a test
manifest pinning their hashes. That keeps the tooling tests hermetic while still
testing the real CLI contract end to end.
"""
from __future__ import annotations


def make_pdf(page_texts: list[str]) -> bytes:
    """Build a valid multi-page PDF with one line of Helvetica text per page."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    # Object numbers are assigned up front so /Kids and /Parent can reference them.
    catalog_num = 1
    pages_num = 2
    font_num = 3
    first_page_num = 4

    page_nums = [first_page_num + 2 * i for i in range(len(page_texts))]
    content_nums = [n + 1 for n in page_nums]

    add(f"<< /Type /Catalog /Pages {pages_num} 0 R >>".encode())
    kids = " ".join(f"{n} 0 R" for n in page_nums)
    add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_texts)} >>".encode())
    add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for page_num, content_num, text in zip(page_nums, content_nums, page_texts, strict=True):
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        stream = f"BT /F1 12 Tf 40 700 Td ({escaped}) Tj ET".encode()
        add(
            f"<< /Type /Page /Parent {pages_num} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_num} 0 R >> >> "
            f"/Contents {content_num} 0 R >>".encode()
        )
        add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_num} 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)
