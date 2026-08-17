# SPDX-License-Identifier: Apache-2.0
"""PDF 추출 (SPEC §5.3). 픽스처는 테스트가 직접 조립하는 자작 PDF(A등급)."""

from __future__ import annotations

import io

import pypdf
import pytest

from anchor.errors import UnsupportedContent
from anchor.normalize.extract import PDF_PIPELINE_VERSION, to_normalized


def build_text_pdf(text: str) -> bytes:
    """텍스트 한 줄이 든 최소 PDF를 바이트 오프셋까지 계산해 조립한다."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_at = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n".encode()
    )
    return out.getvalue()


def build_blank_pdf() -> bytes:
    """텍스트 레이어가 없는 PDF — 스캔본과 동일한 케이스."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=72, height=72)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_pdf_text_extraction():
    raw = build_text_pdf("Memento bridges the present and past Web.")
    doc = to_normalized(raw, "application/pdf")
    assert "Memento bridges the present and past Web." in doc.text
    assert doc.pipeline_version == PDF_PIPELINE_VERSION
    assert doc.pipeline_version.startswith("pypdf/")


def test_scanned_pdf_is_unsupported():
    with pytest.raises(UnsupportedContent):
        to_normalized(build_blank_pdf(), "application/pdf")


def test_pdf_content_type_with_parameters():
    raw = build_text_pdf("Parameters in content type.")
    doc = to_normalized(raw, "application/pdf; charset=binary")
    assert "Parameters in content type." in doc.text
