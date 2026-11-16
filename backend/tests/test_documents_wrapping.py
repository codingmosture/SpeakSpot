"""Line wrapping repairs search text, never source text or highlight geometry."""
from pathlib import Path

import pytest

from app.documents.parser import PDFParser, kiwi
from app.documents.ocr import OCRConfig
from app.documents.grouping import BlockStyle, group_blocks
from app.documents.adapter import groups_to_blocks
from app.documents.wrapping import TextLine, prepare_wraps, wrap_separator
from app.schemas.models import Block


def line(text, y, *, x=.1, right=.94, font="Body", bold=False):
    return TextLine(text, (x, y, right, y + .03), 12, font, bold)


@pytest.mark.parametrize("left,right,expected", [
    ("결과를 반", "환", ""), ("표현에 바이어", "스(Bias)", ""),
    ("처리", "결과를 확인한다", " "), ("검사를 수행하고", "다음 단계를 실행한다", " "),
    ("결과를 반 ", "환", " "),
])
def test_word_and_sentence_boundaries(left, right, expected):
    assert wrap_separator(line(left, .2), line(right, .24, x=.12, right=.5), kiwi.tokenize) == expected


@pytest.mark.parametrize("right", ["▪ 독립 항목", "1. 독립 항목", "2 독립 항목", "⇒ 새 결론"])
def test_explicit_boundaries_are_not_wraps(right):
    assert wrap_separator(line("내용을 처리하고", .2), line(right, .24), kiwi.tokenize) is None


@pytest.mark.parametrize("case", ["font", "gap", "column", "short_line", "sentence_end"])
def test_uncertain_wraps_are_rejected(case):
    a, b = line("결과를 반", .2), line("환", .24, x=.12)
    if case == "font": b = line("환", .24, font="Different")
    if case == "gap": b = line("환", .5)
    if case == "column": b = line("환", .24, x=.6)
    if case == "short_line": a = line("결과를 반", .2, right=.4)
    if case == "sentence_end": a = line("완료했다.", .2)
    assert wrap_separator(a, b, kiwi.tokenize) is None


@pytest.mark.parametrize("kind,region", [("paragraph", None), ("table", None), ("paragraph", "box:other")])
def test_internal_block_repair_and_region_protection(kind, region):
    lines = (line("결과를 반", .2), line("환", .24, x=.12, right=.3))
    b = Block(block_id="b", page=1, text="결과를 반 환", block_type=kind,
              bbox=(.1, .2, .94, .27), rects=[l.bbox for l in lines])
    before = b.model_dump()
    styles = prepare_wraps([b], {"b": BlockStyle(12, lines=lines, region_id=region)}, kiwi.tokenize)
    groups = group_blocks([b], styles)
    assert groups[0].text == ("결과를 반 환" if kind == "table" else "결과를 반환")
    converted = groups_to_blocks([b], groups)[0]
    assert converted.text == groups[0].text
    assert converted.block_id == b.block_id and converted.rects == b.rects
    assert b.model_dump() == before


@pytest.mark.parametrize("protected", ["title", "table", "caption", "equation", "other", "region"])
def test_cross_block_wrap_cannot_cross_protected_boundary(protected):
    a, b = line("결과를 반", .2), line("환", .24, x=.12, right=.3)
    blocks = [Block(block_id=str(i), page=1, text=l.text,
                    block_type=(protected if protected != "region" else "paragraph") if i == 1 else "paragraph",
                    bbox=l.bbox, rects=[l.bbox]) for i, l in enumerate((a, b))]
    styles = {str(i): BlockStyle(12, lines=(l,), region_id="box:other" if protected == "region" and i else None)
              for i, l in enumerate((a, b))}
    assert len(group_blocks(blocks, prepare_wraps(blocks, styles, kiwi.tokenize))) == 2


def test_arithmetic_references_and_original_highlights():
    path = Path("test_pdfs/computer_arithmetic.pdf")
    if not path.is_file(): pytest.skip("Local arithmetic PDF unavailable")
    parser = PDFParser(OCRConfig(enabled=False))
    _, raw = parser.parse_raw(str(path))
    _, sources, groups = parser.parse_with_groups(str(path))
    _, blocks = parser.parse(str(path))
    assert [b.model_dump() for b in raw] == [b.model_dump() for b in sources]
    bias = next(g for g in groups if "p0018_b0007" in g.source_block_ids)
    assert bias.source_block_ids == tuple(f"p0018_b{i:04d}" for i in (7, 8, 9, 10))
    assert "바이어스(Bias)" in bias.text
    returned = next(g for g in groups if "p0010_b0010" in g.source_block_ids)
    assert "반환" in returned.text and "반 환" not in returned.text
    assert "p0010_b0012" not in returned.source_block_ids
    by_id = {b.block_id: b for b in raw}
    for group, block in zip(groups, blocks):
        assert block.text == group.text
        assert block.rects == [r for s in group.source_block_ids for r in by_id[s].rects]
        assert block.bbox == group.bbox
