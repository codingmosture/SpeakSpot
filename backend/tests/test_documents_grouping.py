"""Rule-only topic grouping and source/highlight compatibility regressions."""
from collections import Counter
import os
from pathlib import Path

import pymupdf as fitz
import pytest

from app.documents.grouping import BlockStyle, group_blocks
from app.documents.ocr import OCRConfig
from app.documents.parser import PDFParser
from app.schemas.models import Block


def block(seq, text, x=0.1, y=0.2, kind="paragraph", page=1, width=0.7):
    bbox = (x, y, x + width, y + 0.025)
    return Block(block_id=f"p{page:04d}_b{seq:04d}", page=page, text=text,
                 block_type=kind, bbox=bbox, rects=[bbox], keywords=["original"])


@pytest.mark.parametrize("page,labels", [
    (1, ("Blocked/Asleep state", "Deadlock state")),
    (3, ("Preemptible resources", "Non-preemptible resources")),
    (5, ("Exclusive allocation resources", "Shared allocation resource")),
    (7, ("Memory model", "CPU model")),
    (2, ("Alpha category", "Beta category")),
])
def test_peer_topics_keep_their_own_descriptions(page, labels):
    blocks = [block(1, "Lecture overview", y=0.1, kind="title", page=page),
              block(2, "- " + labels[0], y=0.22, kind="bullet", page=page),
              block(3, "First supporting explanation.", x=0.14, y=0.26, page=page),
              block(4, "- " + labels[1], y=0.32, kind="bullet", page=page),
              block(5, "Second supporting explanation.", x=0.14, y=0.36, page=page)]
    before = [b.model_dump() for b in blocks]
    groups = group_blocks(blocks)
    assert [g.source_block_ids for g in groups] == [
        (blocks[0].block_id,), (blocks[1].block_id, blocks[2].block_id),
        (blocks[3].block_id, blocks[4].block_id)]
    assert [b.model_dump() for b in blocks] == before
    assert Counter(i for g in groups for i in g.source_block_ids) == Counter(b.block_id for b in blocks)
    assert groups[1].rects == tuple(r for b in blocks[1:3] for r in b.rects)


def test_close_peer_bullets_are_independent():
    blocks = [block(i, f"- Independent item {i}", y=0.2 + i * 0.03, kind="bullet") for i in range(1, 5)]
    assert len(group_blocks(blocks)) == 4


def test_bold_heading_joins_same_indent_description():
    blocks = [block(1, "A category"), block(2, "A longer explanation of this category.", y=0.24),
              block(3, "Another category", y=0.28), block(4, "Another explanation.", y=0.32)]
    styles = {b.block_id: BlockStyle(12, i % 2 == 0) for i, b in enumerate(blocks)}
    assert [len(g.source_block_ids) for g in group_blocks(blocks, styles)] == [2, 2]


def test_large_font_heading_joins_description():
    blocks = [block(1, "Resource category"), block(2, "Description.", y=0.24)]
    styles = {blocks[0].block_id: BlockStyle(16), blocks[1].block_id: BlockStyle(12)}
    assert len(group_blocks(blocks, styles)) == 1


@pytest.mark.parametrize("kind", ["title", "table", "caption", "equation", "other"])
def test_protected_types_are_never_absorbed(kind):
    blocks = [block(1, "Category:"), block(2, "Protected content", x=0.14, y=0.24, kind=kind),
              block(3, "Supporting text.", x=0.14, y=0.28)]
    assert len(group_blocks(blocks)) == 3


def test_unknown_plain_labels_remain_unchanged():
    assert len(group_blocks([block(1, "Short text"), block(2, "More text", y=0.23)])) == 2


@pytest.mark.parametrize("left,right,expected", [
    ("The process waits for", "a resource.", "The process waits for a resource."),
    ("A non-preempt-", "ible resource", "A non-preemptible resource"),
    ("자원을 할당하고", "다음 작업을 수행한다.", "자원을 할당하고 다음 작업을 수행한다."),
])
def test_sentence_continuations(left, right, expected):
    groups = group_blocks([block(1, left), block(2, right, y=0.23)])
    assert len(groups) == 1
    assert groups[0].text == expected


def test_complete_sentences_do_not_join_by_proximity():
    assert len(group_blocks([block(1, "First sentence."), block(2, "Second sentence.", y=0.23)])) == 2


def test_sentence_cannot_cross_column_gap_or_page():
    source = block(1, "A process waits for", width=0.3)
    for target in [block(2, "a resource", x=0.6, y=0.23, width=0.3),
                   block(2, "a resource", y=0.5), block(2, "a resource", y=0.23, page=2)]:
        assert len(group_blocks([source, target])) == 2


def test_nested_explanations_stop_at_parent_peer():
    blocks = [block(1, "- Parent", kind="bullet"),
              block(2, "- Child explanation", x=0.14, y=0.24, kind="bullet"),
              block(3, "- Different parent", y=0.28, kind="bullet")]
    assert [len(g.source_block_ids) for g in group_blocks(blocks)] == [2, 1]


def test_bold_resource_labels_under_broad_heading_are_independent():
    blocks = [block(1, "- Resource types", kind="bullet"),
              block(2, "- First category", x=0.14, y=0.24, kind="bullet"),
              block(3, "- Second category", x=0.14, y=0.28, kind="bullet"),
              block(4, "- Model", y=0.4, kind="bullet"),
              block(5, "- Explanation!", x=0.14, y=0.44, kind="bullet")]
    styles = {b.block_id: BlockStyle(12, i < 4) for i, b in enumerate(blocks)}
    assert [len(g.source_block_ids) for g in group_blocks(blocks, styles)] == [1, 1, 1, 2]


def test_rotated_text_remains_separate():
    blocks = [block(1, "Topic:"), block(2, "Description.", y=0.24)]
    assert len(group_blocks(blocks, {b.block_id: BlockStyle(horizontal=False) for b in blocks})) == 2


def test_duplicate_ids_rejected_and_empty_supported():
    b = block(1, "Content")
    with pytest.raises(ValueError, match="unique"):
        group_blocks([b, b])
    assert group_blocks([]) == []


def test_parser_entrypoint_preserves_original_blocks(tmp_path):
    path = tmp_path / "topics.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=700)
        page.insert_text((60, 100), "Lecture title", fontsize=24)
        page.insert_text((60, 200), "First category:", fontsize=14)
        page.insert_text((84, 225), "A supporting description.", fontsize=12)
        page.insert_text((60, 300), "Second category:", fontsize=14)
        page.insert_text((84, 325), "A different description.", fontsize=12)
        doc.save(path)
    parser = PDFParser(OCRConfig(enabled=False))
    old_count, old_blocks = parser.parse_raw(str(path))
    count, blocks, groups = parser.parse_with_groups(str(path))
    assert count == old_count == 1
    assert [b.model_dump() for b in blocks] == [b.model_dump() for b in old_blocks]
    assert [len(g.source_block_ids) for g in groups] == [1, 2, 2]


@pytest.mark.parametrize("page,expected", [
    (1, [(1,), (2, 3, 4), (5, 6, 7, 8, 9), (10,), (11,)]),
    (3, [(1,), (2,), (3, 4, 5), (6, 7, 8, 9), (10,)]),
    (2, [(1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,), (9,)]),
    (4, [(1,), (2,), (3, 4, 5), (6, 7, 8), (9,)]),
    (5, [(1,), (2,), (3, 4, 5), (6, 7, 8), (9,), (10,)]),
    (6, [(1,), (2,), (3, 4, 5, 6), (7, 8, 9), (10,)]),
    (7, [(1,), (2,), (3,), (4,), (5,), (6,), (7, 8), (9,)]),
])
def test_local_deadlock_reference(page, expected):
    """Optional private fixture: use the seven-page ch5_Deadlock_test.pdf.

    The numeric IDs are annotations of the original parser output, not rules in
    the algorithm. An exact partition detects both overmerges and missed joins.
    """
    path = os.environ.get("SPEAKSPOT_DEADLOCK_TEST_PDF")
    if not path:
        pytest.skip("Set SPEAKSPOT_DEADLOCK_TEST_PDF to the local seven-page PDF")
    assert Path(path).is_file()
    parser = PDFParser(OCRConfig(enabled=False))
    count, sources, groups = parser.parse_with_groups(path)
    assert count == 7
    assert [g.source_block_ids for g in groups if g.page == page] == [
        tuple(f"p{page:04d}_b{i:04d}" for i in ids) for ids in expected]
    _, baseline = parser.parse_raw(path)
    assert [b.model_dump() for b in sources] == [b.model_dump() for b in baseline]
    by_source = {s: g for g in groups for s in g.source_block_ids}
    if page == 2:
        for child, parent in [(3, 2), (5, 4), (6, 4), (7, 4), (8, 4)]:
            assert by_source[f"p0002_b{child:04d}"].parent_group_id == f"sg_p0002_b{parent:04d}"
    if page == 7:
        assert by_source["p0007_b0006"].supplements_group_ids == tuple(
            f"sg_p0007_b{i:04d}" for i in (2, 3, 4, 5))
    for group in groups:
        assert group.group_id == f"sg_{group.source_block_ids[0]}"
        original = [b for b in sources if b.block_id in group.source_block_ids]
        assert group.rects == tuple(r for b in original for r in b.rects)


def test_parallel_list_relations_preserve_sources():
    blocks = [block(1, "- Classification", kind="bullet")]
    blocks += [block(i + 2, f"- {label}による分類", x=0.14, y=0.24 + i * 0.04, kind="bullet")
               for i, label in enumerate(("Alpha", "Beta", "Gamma"))]
    blocks.append(block(5, "* Applies to the whole list", x=0.14, y=0.36, kind="bullet"))
    before = [b.model_dump() for b in blocks]
    styles = {b.block_id: BlockStyle(12, i == 0) for i, b in enumerate(blocks)}
    groups = group_blocks(blocks, styles)
    assert len(groups) == 5
    assert all(g.parent_group_id == groups[0].group_id for g in groups[1:4])
    assert groups[-1].supplements_group_ids == tuple(g.group_id for g in groups[:4])
    assert [b.model_dump() for b in blocks] == before


@pytest.mark.parametrize("case", ["unknown_style", "page", "column", "gap", "protected"])
def test_uncertain_relationships_stay_empty(case):
    root = block(1, "- Parent", kind="bullet")
    child = block(2, "- Child", x=0.14, y=0.24, kind="bullet")
    styles = {b.block_id: BlockStyle(12, True) for b in (root, child)}
    if case == "unknown_style":
        styles = {}
    elif case == "page":
        child.page = 2
    elif case == "column":
        child.bbox = (0.85, 0.24, 0.99, 0.265)
    elif case == "gap":
        child.bbox = (0.14, 0.6, 0.84, 0.625)
    else:
        child.block_type = "other"
    groups = group_blocks([root, child], styles)
    assert all(g.parent_group_id is None and not g.supplements_group_ids for g in groups)
