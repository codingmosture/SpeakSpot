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
    with patch.object(fitz.Pixmap, "pdfocr_tobytes", side_effect=AssertionError("unnecessary OCR")):
        count, blocks = PDFParser().parse(str(path))
    assert count == 1
    assert [b.text for b in blocks] == ["Native document title"]
    assert_valid_blocks(blocks)


def test_disabled_ocr_keeps_existing_text(tmp_path):
    path = make_pdf(tmp_path / "mixed.pdf", image=True)
    with patch.object(fitz.Pixmap, "pdfocr_tobytes", side_effect=AssertionError("disabled OCR")):
        _, blocks = PDFParser(OCRConfig(enabled=False)).parse(str(path))
    assert [b.text for b in blocks] == ["Native document title"]


def test_mixed_document_does_not_silently_omit_images_without_ocr_data(tmp_path):
    path = make_pdf(tmp_path / "mixed.pdf", image=True)
    config = OCRConfig(tessdata=str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="1페이지.*setup_ocr.py"):
        PDFParser(config).parse(str(path))


def test_scan_without_ocr_data_has_actionable_error(tmp_path):
    path = make_pdf(tmp_path / "scan.pdf", image=True, native=False)
    with pytest.raises(ValueError, match="1페이지.*setup_ocr.py"):
        PDFParser(OCRConfig(tessdata=str(tmp_path / "missing"))).parse(str(path))


@pytest.mark.parametrize("error", [RuntimeError("engine unavailable"), Exception("engine unavailable")])
def test_engine_failure_is_visible_for_mixed_documents(tmp_path, error):
    path = make_pdf(tmp_path / "mixed.pdf", image=True)
    (tmp_path / "eng.traineddata").touch()
    config = OCRConfig(language="eng", tessdata=str(tmp_path))
    with patch.object(fitz.Pixmap, "pdfocr_tobytes", side_effect=error):
        with pytest.raises(ValueError, match="setup_ocr.py"):
            PDFParser(config).parse(str(path))


def test_decorative_rules_do_not_require_ocr(tmp_path):
    path = tmp_path / "decoration.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=700)
        page.insert_text((50, 60), "Normal document text", fontsize=20)
        pixmap = fitz.Pixmap(fitz.csRGB, 3, 1, bytes([80, 100, 120] * 3), False)
        page.insert_image(fitz.Rect(20, 100, 580, 101.5), pixmap=pixmap)
        doc.save(path)
    _, blocks = PDFParser(OCRConfig(tessdata=str(tmp_path / "missing"))).parse(str(path))
    assert [b.text for b in blocks] == ["Normal document text"]


@pytest.mark.parametrize("label", ["1", "- 1 -", "(1)", "Page 1", "p.1", "1 / 157", "1 페이지", "1 of 157"])
@pytest.mark.parametrize("rotation", [0, 90])
def test_footer_page_labels_are_not_search_blocks(tmp_path, label, rotation):
    path = tmp_path / "footer.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=700)
        page.insert_text((50, 120), "SELECT 1 AS value;", fontsize=14)
        page.insert_text((280, 350), "1", fontsize=12)
        page.insert_text((500, 650), "1000", fontsize=10)
        page.insert_text((500, 680), label, fontsize=9, fontname="korea")
        page.set_rotation(rotation)
        doc.save(path)
    _, blocks = PDFParser(OCRConfig(enabled=False)).parse(str(path))
    texts = [b.text for b in blocks]
    assert texts.count("1") == 1  # Numeric body content is preserved.
    assert "1000" in texts
    assert "SELECT 1 AS value;" in texts
    assert len(blocks) == 3


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
def test_image_table_cells_are_grouped_as_one_figure(tmp_path, monkeypatch):
    path = tmp_path / "image-table.pdf"
    with fitz.open() as artwork:
        table = artwork.new_page(width=600, height=240)
        for x in [0, 150, 300, 450, 599]:
            table.draw_line((x, 0), (x, 239), color=(0.7, 0.7, 0.7), width=1)
        for y in [0, 60, 120, 180, 239]:
            table.draw_line((0, y), (599, y), color=(0.7, 0.7, 0.7), width=1)
        rows = [("customer", "name", "age", "balance"), ("apple", "Alice", "20", "1000"),
                ("banana", "Bob", "25", "2500"), ("orange", "Carol", "22", "0")]
        for row, values in enumerate(rows):
            for col, value in enumerate(values):
                table.insert_text((col * 150 + 12, row * 60 + 38), value, fontsize=20)
        raster = table.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=700)
        page.insert_text((50, 60), "Database lecture", fontsize=20)
        page.insert_text((560, 680), "1", fontsize=10)
        page.insert_image(fitz.Rect(40, 180, 560, 388), stream=raster.tobytes("png"))
        doc.save(path)
    parser = PDFParser(OCRConfig(tessdata=str(DEFAULT_TESSDATA)))
    monkeypatch.setattr(parser, "_detect_tables_fast", lambda *args: [(0, 0.2, 1, 0.7)])
    _, blocks = parser.parse(str(path))
    text = " ".join(b.text for b in blocks)
    assert text.count("Database lecture") == 1
    for value in ["customer", "apple", "banana", "orange", "1000", "2500"]:
        assert value in text
    assert not any(b.text == "1" for b in blocks)
    assert len(blocks) == 2  # Native heading and one complete table image.
    figure = next(b for b in blocks if "apple" in b.text)
    assert "banana" in figure.text and "orange" in figure.text
    assert figure.bbox == pytest.approx((40 / 600, 180 / 700, 560 / 600, 388 / 700), abs=0.0001)
    assert figure.rects == [figure.bbox]
    assert_valid_blocks(blocks)


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
    assert "운영체제" in image_block.text
    assert len(blocks) == 1 + int(native)
    with fitz.open(path) as doc:
        page = doc[0]
        expected = fitz.Rect(50, 200, 550, 400) * page.rotation_matrix
        cx = (image_block.bbox[0] + image_block.bbox[2]) / 2 * page.rect.width
        cy = (image_block.bbox[1] + image_block.bbox[3]) / 2 * page.rect.height
        assert expected.contains(fitz.Point(cx, cy))
        assert image_block.bbox == pytest.approx(
            (expected.x0 / page.rect.width, expected.y0 / page.rect.height,
             expected.x1 / page.rect.width, expected.y1 / page.rect.height), abs=0.0001)
        assert image_block.rects == [image_block.bbox]


@pytest.mark.parametrize("image", [False, True])
def test_only_image_bullets_stay_grouped(tmp_path, monkeypatch, image):
    import app.documents.parser as parser_module

    path = make_pdf(tmp_path / "bullets.pdf", native=False)
    block = {
        "type": 0, "bbox": (50, 200, 550, 400), "_ocr_image": image,
        "lines": [
            {"bbox": (60, y, 400, y + 20), "spans": [{"text": text, "size": 12, "font": "test"}]}
            for text, y in [("1. First item", 220), ("2. Second item", 260)]
        ],
    }
    monkeypatch.setattr(parser_module, "extract_page_text", lambda *args: {"blocks": [block]})
    _, blocks = PDFParser().parse(str(path))
    assert len(blocks) == (1 if image else 2)
    if image:
        assert "First item" in blocks[0].text and "Second item" in blocks[0].text
        assert blocks[0].rects == [blocks[0].bbox]


@pytest.mark.skipif(not REAL_OCR, reason="Run python scripts/setup_ocr.py for real OCR tests")
def test_separate_figures_remain_separate_blocks(tmp_path):
    path = make_pdf(tmp_path / "two-figures.pdf", image=True)
    with fitz.open(path) as doc:
        with fitz.open() as artwork:
            page = artwork.new_page(width=600, height=240)
            page.insert_text((40, 70), "SECOND FIGURE", fontsize=28)
            raster = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        doc[0].insert_image(fitz.Rect(50, 450, 550, 650), stream=raster.tobytes("png"))
        doc.saveIncr()
    _, blocks = PDFParser(OCRConfig(tessdata=str(DEFAULT_TESSDATA))).parse(str(path))
    first = next(b for b in blocks if "IMAGE" in b.text)
    second = next(b for b in blocks if "SECOND" in b.text)
    assert first.block_id != second.block_id
    assert "SECOND" not in first.text and "IMAGE" not in second.text
    assert len(blocks) == 3


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
