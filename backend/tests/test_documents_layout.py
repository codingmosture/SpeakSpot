"""Spatial reading order, diagram boundaries and real-PDF regressions."""
import os
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.documents.grouping import BlockStyle, group_blocks
from app.documents.layout import extract_layout_boxes, reading_streams, region_for_bbox
from app.documents.ocr import OCRConfig
from app.documents.parser import PDFParser
from app.schemas.models import Block


def source(seq, text, x, y, *, width=0.34, kind="paragraph"):
    bbox = (x, y, x + width, y + 0.025)
    return Block(block_id=f"b{seq}", page=1, text=text, block_type=kind,
                 bbox=bbox, rects=[bbox])


def columns():
    blocks = [source(1, "Left topic", 0.08, 0.2), source(2, "Right topic", 0.56, 0.21),
              source(3, "Left explanation.", 0.08, 0.24), source(4, "Right explanation.", 0.56, 0.25),
              source(5, "Left lower text.", 0.08, 0.5), source(6, "Right lower text.", 0.56, 0.51)]
    styles = {b.block_id: BlockStyle(12, b.block_id in {"b1", "b2"}) for b in blocks}
    return blocks, styles


def test_interleaved_columns_join_only_their_own_explanations():
    blocks, styles = columns()
    before = [b.model_dump() for b in blocks]
    groups = group_blocks(blocks, styles)
    assert [g.source_block_ids for g in groups] == [("b1", "b3"), ("b5",), ("b2", "b4"), ("b6",)]
    assert [b.model_dump() for b in blocks] == before


def test_numbered_items_do_not_merge_after_column_reordering():
    blocks, styles = columns()
    blocks[2].text = "1 First independent step"
    blocks[4].text = "2 Second independent step"
    assert all(len(g.source_block_ids) == 1 for g in group_blocks(blocks, styles)
               if any(s in {"b1", "b3", "b5"} for s in g.source_block_ids))


def test_full_width_section_heading_is_a_reading_barrier():
    blocks, styles = columns()
    heading = source(7, "New section", 0.04, 0.4, width=0.92, kind="title")
    groups = group_blocks([*blocks, heading], styles)
    ids = [g.source_block_ids for g in groups]
    assert ids.index(("b7",)) > ids.index(("b2", "b4"))
    assert ids.index(("b7",)) < ids.index(("b5",))


def test_indentation_and_sparse_labels_do_not_force_columns():
    blocks = [source(1, "A topic:", 0.08, 0.2, width=0.7),
              source(2, "Indented explanation.", 0.12, 0.24, width=0.65)]
    assert len(reading_streams(blocks, {})) == 1
    assert len(group_blocks(blocks)) == 1
    # Two side-by-side labels alone supply no sustained column evidence.
    assert len(reading_streams([source(1, "Label", 0.08, 0.2), source(2, "Label", 0.56, 0.2)], {})) == 1


def test_separate_diagram_cells_never_merge_despite_indent():
    boxes = [(0.05, 0.15, 0.48, 0.6), (0.08, 0.19, 0.44, 0.23), (0.08, 0.23, 0.44, 0.28)]
    blocks = [source(1, "First field", 0.1, 0.195, width=0.3),
              source(2, "Second field", 0.13, 0.24, width=0.28)]
    styles = {b.block_id: BlockStyle(12, region_id=region_for_bbox(b.bbox, boxes)) for b in blocks}
    assert len(group_blocks(blocks, styles)) == 2


def test_boundary_straddlers_and_overlapping_boxes_are_ambiguous():
    boxes = [(0.1, 0.1, 0.5, 0.3), (0.1, 0.3, 0.5, 0.5)]
    assert region_for_bbox((0.2, 0.28, 0.4, 0.32), boxes).startswith("boundary:")
    assert region_for_bbox((0.2, 0.2, 0.4, 0.3), [(0.1, 0.1, 0.5, 0.4), (0.15, 0.15, 0.6, 0.45)]).startswith("boundary:")


def test_panel_header_body_and_shadow_do_not_split_a_callout():
    def drawing(rect, *, rounded=False, opacity=1):
        return {"rect": rect, "items": [("c",)] * 2 + [("l",)] * 3 if rounded else [("re",)],
                "fill": (0.5, 0.5, 0.5), "fill_opacity": opacity}
    page = SimpleNamespace(rect=SimpleNamespace(width=100, height=100), rotation=0)
    page.get_cdrawings = lambda: [drawing((50, 20, 95, 25), rounded=True),
                                 drawing((50, 26, 95, 40), rounded=True),
                                 drawing((51, 21, 96, 41)),  # Offset backplate.
                                 drawing((10, 20, 40, 25)), drawing((10, 26, 40, 31))]
    boxes = extract_layout_boxes(page)
    header = region_for_bbox((0.52, 0.21, 0.9, 0.24), boxes)
    body = region_for_bbox((0.52, 0.27, 0.9, 0.39), boxes)
    assert header == body
    assert region_for_bbox((0.12, 0.21, 0.38, 0.24), boxes) != region_for_bbox((0.12, 0.27, 0.38, 0.30), boxes)


def test_stroke_only_rectangle_without_fill_opacity_is_a_boundary():
    page = SimpleNamespace(rect=SimpleNamespace(width=100, height=100), rotation=0)
    # get_drawings() fallback represents stroke-only paths with None opacity.
    page.get_drawings = lambda: [{"rect": (10, 20, 40, 30), "items": [("re",)],
                                  "fill": None, "fill_opacity": None}]
    assert region_for_bbox((0.12, 0.22, 0.38, 0.28), extract_layout_boxes(page)).startswith("box:")


@pytest.fixture(scope="module")
def process_pdf():
    path = Path(os.environ.get("SPEAKSPOT_PROCESS_TEST_PDF", "test_pdfs/process_creation.pdf"))
    if not path.is_file():
        pytest.skip("Set SPEAKSPOT_PROCESS_TEST_PDF to the local process PDF")
    parser = PDFParser(OCRConfig(enabled=False))
    count, sources, groups = parser.parse_with_groups(str(path))
    _, baseline = parser.parse_raw(str(path))
    return count, sources, groups, baseline


@pytest.mark.parametrize("page,expected", [
    (4, [(1,), (3,), (4,), (6,), (7,), (9,), (11,), (12,), (14,), (16,), (2, 5), (8, 10), (13, 15)]),
    (8, [(1,), (3, 4), (14, 16), (24,), (2,), (5,), (6,), (7,), (8,), (9,), (10,),
         (11,), (12,), (13,), (15,), (17,), (18,), (19,), (20,), (21,), (22,), (23,), (25, 26), (27,), (28,)]),
])
def test_process_columns_and_diagram_reference(process_pdf, page, expected):
    _, _, groups, _ = process_pdf
    assert [g.source_block_ids for g in groups if g.page == page] == [
        tuple(f"p{page:04d}_b{i:04d}" for i in members) for members in expected]


def test_process_all_sources_and_highlights_preserved(process_pdf):
    count, sources, groups, baseline = process_pdf
    assert count == 35
    assert [b.model_dump() for b in sources] == [b.model_dump() for b in baseline]
    assert Counter(s for g in groups for s in g.source_block_ids) == Counter(b.block_id for b in sources)
    by_id = {b.block_id: b for b in sources}
    for group in groups:
        members = [by_id[s] for s in group.source_block_ids]
        assert group.group_id == "sg_" + members[0].block_id
        assert group.rects == tuple(r for b in members for r in b.rects)
        assert group.bbox == (min(b.bbox[0] for b in members), min(b.bbox[1] for b in members),
                              max(b.bbox[2] for b in members), max(b.bbox[3] for b in members))
        assert all(b.page == group.page for b in members)


def test_process_numbered_siblings_remain_separate(process_pdf):
    _, _, groups, _ = process_pdf
    for page, numbers in [(9, (13, 14)), (18, (3, 6, 8, 10, 13)), (26, (6, 8, 11, 14))]:
        for number in numbers:
            block_id = f"p{page:04d}_b{number:04d}"
            assert next(g for g in groups if block_id in g.source_block_ids).source_block_ids == (block_id,)
