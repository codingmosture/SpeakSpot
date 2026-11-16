"""Default semantic Blocks through the unchanged upload/search/highlight contract."""
from dataclasses import replace
from pathlib import Path

import pymupdf as fitz
import pytest

from app.documents.adapter import groups_to_blocks
from app.documents.grouping import group_blocks
from app.documents.ocr import OCRConfig
from app.documents.parser import PDFParser
from app.schemas.models import Block


def sources():
    return [Block(block_id=f"b{i}", page=1, text=text, block_type=kind,
                  bbox=box, rects=[box], keywords=words)
            for i, (text, kind, box, words) in enumerate([
                ("Lecture", "title", (0.1, 0.1, 0.8, 0.15), ["lecture"]),
                ("Topic:", "bullet", (0.1, 0.2, 0.8, 0.225), ["topic", "shared"]),
                ("A supporting explanation.", "paragraph", (0.14, 0.24, 0.84, 0.265), ["shared", "explanation"]),
            ], 1)]


def test_projection_preserves_sources_and_exact_highlights():
    original = sources()
    snapshot = [b.model_dump() for b in original]
    groups = group_blocks(original)
    blocks = groups_to_blocks(original, groups)
    assert [b.model_dump() for b in original] == snapshot
    assert blocks[0].model_dump() == original[0].model_dump()
    assert blocks[0] is not original[0]
    assert blocks[1].block_id == groups[1].group_id
    assert blocks[1].block_type == "paragraph"
    assert blocks[1].keywords == ["topic", "shared", "explanation"]
    assert blocks[1].rects == original[1].rects + original[2].rects
    assert blocks[1].bbox == groups[1].bbox
    assert blocks[1].text == groups[1].text
    blocks[0].keywords.append("changed")
    blocks[1].rects.clear()
    assert [b.model_dump() for b in original] == snapshot


def test_projection_rejects_missing_duplicate_cross_page_and_colliding_ids():
    original = sources()
    groups = group_blocks(original)
    for bad in [groups[:1], [*groups, groups[0]], [groups[0], replace(groups[1], page=2)],
                [groups[0], replace(groups[1], group_id="b1")]]:
        with pytest.raises(ValueError):
            groups_to_blocks(original, bad)
    assert groups_to_blocks([], []) == []


@pytest.mark.parametrize("name,count", [("ch5_Deadlock_test.pdf", 43), ("process_creation.pdf", 489)])
def test_real_pdf_default_projection_and_raw_comparison(name, count):
    path = Path("test_pdfs") / name
    if not path.is_file():
        pytest.skip("Local reference PDF not available")
    parser = PDFParser(OCRConfig(enabled=False))
    pages, blocks = parser.parse(str(path))
    raw_pages, raw = parser.parse_raw(str(path))
    grouped_pages, original, groups = parser.parse_with_groups(str(path))
    assert pages == raw_pages == grouped_pages
    assert len(blocks) == len(groups) == count
    assert [b.model_dump() for b in original] == [b.model_dump() for b in raw]
    by_id = {b.block_id: b for b in raw}
    assert len({b.block_id for b in blocks}) == count
    for block, group in zip(blocks, groups):
        assert Block.model_validate(block.model_dump()).model_dump() == block.model_dump()
        members = [by_id[s] for s in group.source_block_ids]
        assert block.block_id == (members[0].block_id if len(members) == 1 else group.group_id)
        assert block.rects == [r for b in members for r in b.rects]
        assert block.bbox == group.bbox
        assert set(block.model_dump()) == {"block_id", "page", "text", "block_type", "bbox", "rects", "keywords"}


def test_real_parse_upload_search_and_highlight_contract(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    import app.documents.service as service_module

    path = tmp_path / "semantic.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=700)
        page.insert_text((60, 100), "Lecture title", fontsize=24)
        page.insert_text((60, 200), "Synchronization:", fontsize=14)
        page.insert_text((84, 225), "Semaphore synchronization protects shared memory.", fontsize=12)
        doc.save(path)
    parser = PDFParser(OCRConfig(enabled=False))
    _, raw, groups = parser.parse_with_groups(str(path))
    merged = next(g for g in groups if len(g.source_block_ids) > 1)
    expected_rects = [r for b in raw if b.block_id in merged.source_block_ids for r in b.rects]
    # Keep the real DocumentService -> parse() call. Only local configuration
    # and file destination are isolated; search/API production code is untouched.
    monkeypatch.setattr(service_module, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(service_module.document_service, "parser", parser)
    with TestClient(app) as client:
        response = client.post("/api/v1/documents", files={"file": (path.name, path.read_bytes(), "application/pdf")})
        assert response.status_code == 201, response.text
        doc_id = response.json()["document_id"]
        response = client.get(f"/api/v1/documents/{doc_id}/blocks")
        assert response.status_code == 200
        received = response.json()["blocks"]
        # Same ID-to-Block lookup as the frontend's blockMap.
        block_map = {b["block_id"]: b for b in received}
        assert merged.group_id in block_map
        session = client.post("/api/v1/sessions", json={"document_id": doc_id,
            "config": {"search_strategy": "sliding_queue", "search_model": None}})
        assert session.status_code == 201, session.text
        result = client.post("/api/v1/search", json={"session_id": session.json()["session_id"],
            "text": "Semaphore synchronization protects shared memory", "segment_id": None})
        assert result.status_code == 200, result.text
        match = result.json()
        assert match["status"] == "MATCH"
        assert match["block_id"] == merged.group_id
        highlight = block_map[match["block_id"]]
        assert highlight["page"] == match["page"] == 1
        assert highlight["rects"] == [list(r) for r in expected_rects]
        assert len(highlight["rects"]) == 2
        assert len(service_module.document_service.blocks[doc_id]) == len(groups)
