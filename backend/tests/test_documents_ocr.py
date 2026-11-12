"""PDF OCR regression tests; real OCR cases use optional local language data."""
from pathlib import Path
from unittest.mock import patch

import pymupdf as fitz
import pytest

from app.documents.ocr import DEFAULT_TESSDATA, OCRConfig
from app.documents.parser import PDFParser


def make_pdf(path: Path, *, image=False, native=True, rotation=0):
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=700)
        if native:
            page.insert_text((50, 60), "Native document title", fontsize=20)
        if image:
            with fitz.open() as artwork:
                source = artwork.new_page(width=600, height=240)
                source.insert_text((40, 70), "IMAGE SEARCH TEXT", fontsize=28)
                source.insert_text((40, 130), "운영체제 프로세스 관리", fontsize=26, fontname="korea")
                raster = source.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                page.insert_image(fitz.Rect(50, 200, 550, 400), stream=raster.tobytes("png"))
        page.set_rotation(rotation)
        doc.save(path)
    return path


def assert_valid_blocks(blocks):
    assert blocks
    assert len({b.block_id for b in blocks}) == len(blocks)
    for block in blocks:
        assert block.page == 1
        for rect in [block.bbox, *block.rects]:
            assert all(0 <= value <= 1 for value in rect)
            assert rect[0] < rect[2] and rect[1] < rect[3]


def test_text_only_does_not_invoke_ocr(tmp_path):
    path = make_pdf(tmp_path / "native.pdf")
    with patch.object(fitz.Page, "get_textpage_ocr", side_effect=AssertionError("unnecessary OCR")):
        count, blocks = PDFParser().parse(str(path))
    assert count == 1
    assert [b.text for b in blocks] == ["Native document title"]
    assert_valid_blocks(blocks)


def test_disabled_ocr_keeps_existing_text(tmp_path):
    path = make_pdf(tmp_path / "mixed.pdf", image=True)
    with patch.object(fitz.Page, "get_textpage_ocr", side_effect=AssertionError("disabled OCR")):
        _, blocks = PDFParser(OCRConfig(enabled=False)).parse(str(path))
    assert [b.text for b in blocks] == ["Native document title"]


def test_missing_ocr_data_preserves_native_text_with_warning(tmp_path, caplog):
    path = make_pdf(tmp_path / "mixed.pdf", image=True)
    config = OCRConfig(tessdata=str(tmp_path / "missing"))
    _, blocks = PDFParser(config).parse(str(path))
    assert [b.text for b in blocks] == ["Native document title"]
    assert "기존 텍스트만 사용" in caplog.text


def test_scan_without_ocr_data_has_actionable_error(tmp_path):
    path = make_pdf(tmp_path / "scan.pdf", image=True, native=False)
    with pytest.raises(ValueError, match="1페이지.*setup_ocr.py"):
        PDFParser(OCRConfig(tessdata=str(tmp_path / "missing"))).parse(str(path))


@pytest.mark.parametrize("error", [RuntimeError("engine unavailable"), Exception("engine unavailable")])
def test_engine_failure_preserves_native_text(tmp_path, caplog, error):
    path = make_pdf(tmp_path / "mixed.pdf", image=True)
    (tmp_path / "eng.traineddata").touch()
    config = OCRConfig(language="eng", tessdata=str(tmp_path))
    with patch.object(fitz.Page, "get_textpage_ocr", side_effect=error):
        _, blocks = PDFParser(config).parse(str(path))
    assert blocks[0].text == "Native document title"
    assert "engine unavailable" in caplog.text


def test_ocr_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("SPEAKSPOT_OCR_ENABLED", "off")
    monkeypatch.setenv("SPEAKSPOT_OCR_LANGUAGE", "eng")
    monkeypatch.setenv("SPEAKSPOT_OCR_DPI", "300")
    monkeypatch.setenv("SPEAKSPOT_OCR_TESSDATA", str(tmp_path))
    assert OCRConfig.from_env() == OCRConfig(False, "eng", 300, str(tmp_path))


@pytest.mark.parametrize("dpi", [0, 601])
def test_invalid_ocr_resolution(dpi):
    with pytest.raises(ValueError, match="DPI"):
        OCRConfig(dpi=dpi)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_native_rotated_highlight_coordinates(tmp_path, rotation):
    path = make_pdf(tmp_path / "rotation.pdf", rotation=rotation)
    _, blocks = PDFParser().parse(str(path))
    with fitz.open(path) as doc:
        page = doc[0]
        expected = page.search_for("Native document title")[0] * page.rotation_matrix
        normalized = (expected.x0 / page.rect.width, expected.y0 / page.rect.height,
                      expected.x1 / page.rect.width, expected.y1 / page.rect.height)
    assert blocks[0].bbox == pytest.approx(normalized, abs=0.0002)


REAL_OCR = all((DEFAULT_TESSDATA / f"{lang}.traineddata").is_file() for lang in ("eng", "kor"))


@pytest.mark.skipif(not REAL_OCR, reason="Run python scripts/setup_ocr.py for real OCR tests")
@pytest.mark.parametrize("native,rotation", [(False, 0), (True, 0), (True, 90), (False, 270)])
def test_real_image_text_and_coordinates(tmp_path, native, rotation):
    path = make_pdf(tmp_path / "ocr.pdf", image=True, native=native, rotation=rotation)
    config = OCRConfig(tessdata=str(DEFAULT_TESSDATA))
    count, blocks = PDFParser(config).parse(str(path))
    assert count == 1
    assert_valid_blocks(blocks)
    joined = " ".join(b.text for b in blocks)
    assert "IMAGE" in joined
    assert "운영체제" in joined
    assert joined.count("Native document title") == int(native)
    image_block = next(b for b in blocks if "IMAGE" in b.text)
    with fitz.open(path) as doc:
        page = doc[0]
        expected = fitz.Rect(50, 200, 550, 400) * page.rotation_matrix
        cx = (image_block.bbox[0] + image_block.bbox[2]) / 2 * page.rect.width
        cy = (image_block.bbox[1] + image_block.bbox[3]) / 2 * page.rect.height
        assert expected.contains(fitz.Point(cx, cy))


@pytest.mark.skipif(not REAL_OCR, reason="Run python scripts/setup_ocr.py for real OCR tests")
def test_scanned_document_upload_is_searchable(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.documents.service import document_service

    path = make_pdf(tmp_path / "searchable-scan.pdf", image=True, native=False)
    monkeypatch.setattr(document_service, "parser", PDFParser(OCRConfig(tessdata=str(DEFAULT_TESSDATA))))
    with TestClient(app) as client:
        uploaded = client.post("/api/v1/documents", files={
            "file": (path.name, path.read_bytes(), "application/pdf"),
        })
        assert uploaded.status_code == 201, uploaded.text
        doc_id = uploaded.json()["document_id"]
        response = client.get(f"/api/v1/documents/{doc_id}/blocks")
        assert response.status_code == 200
        image_ids = {b["block_id"] for b in response.json()["blocks"] if "IMAGE" in b["text"]}
        session = client.post("/api/v1/sessions", json={
            "document_id": doc_id,
            "config": {"search_strategy": "sliding_queue", "search_model": None},
        })
        assert session.status_code == 201, session.text
        found = client.post("/api/v1/search", json={
            "session_id": session.json()["session_id"], "text": "IMAGE SEARCH TEXT", "segment_id": None,
        })
        assert found.status_code == 200, found.text
        assert found.json()["status"] == "MATCH"
        assert found.json()["block_id"] in image_ids
