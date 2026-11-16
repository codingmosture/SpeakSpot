"""Conservative text-only repair using original line geometry and local lexicon."""
import re
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class TextLine:
    text: str
    bbox: tuple[float, float, float, float]
    size: float
    font: str
    bold: bool


def wrap_separator(left: TextLine, right: TextLine, tokenize) -> str | None:
    """None means no evidence of wrapping; never infer from Korean text alone."""
    a, b = left.text.rstrip(), right.text.lstrip()
    height = min(left.bbox[3] - left.bbox[1], right.bbox[3] - right.bbox[1])
    gap = right.bbox[1] - left.bbox[3]
    if (not a or not b or not left.font or left.size <= 0 or abs(left.size - right.size) > left.size * .1
            or left.font != right.font or left.bold != right.bold
            or not (-.15 * height <= gap <= .8 * height)
            or not (0 <= right.bbox[0] - left.bbox[0] <= .065)
            or left.bbox[2] < .8 or left.bbox[2] - left.bbox[0] < .45
            or re.match(r"^(?:[◼•●○▪▫◆◇▶▷*\-⇒→=]|\d+[.)]|\d+\s|[①-⑳])", b)
            or re.search(r"[.!?。！？][\"'’”)]*$", a)):
        return None
    end = re.search(r"[가-힣]+$", a)
    start = re.match(r"[가-힣]+", b)
    if (end and start and min(len(end[0]), len(start[0])) == 1
            and not left.text[-1].isspace() and not right.text[0].isspace()):
        word = end[0] + start[0]
        tokens = tokenize(word)
        if len(tokens) == 1 and tokens[0].form == word and tokens[0].tag in {"NNG", "NNP"}:
            return ""
    # Retain a normal word boundary unless a lexical join is well supported.
    return None if right.bold else " "


def repair_lines(lines, tokenize):
    if not lines:
        return ""
    text = lines[0].text.strip()
    for left, right in zip(lines, lines[1:]):
        separator = wrap_separator(left, right, tokenize)
        text += (" " if separator is None else separator) + right.text.strip()
    return text


def prepare_wraps(blocks, styles, tokenize):
    from app.documents.layout import reading_streams
    result = dict(styles)
    protected = {"title", "table", "caption", "equation", "other"}
    for stream in reading_streams(blocks, styles):
        for block in stream:
            style = styles[block.block_id]
            if (block.block_type not in protected and style.horizontal and style.lines
                    and " ".join(line.text.strip() for line in style.lines) == block.text):
                result[block.block_id] = replace(style, search_text=repair_lines(style.lines, tokenize))
        for a, b in zip(stream, stream[1:]):
            left, right = result[a.block_id], result[b.block_id]
            if (a.block_type not in protected and b.block_type not in protected
                    and left.horizontal and right.horizontal and left.lines and right.lines):
                result[b.block_id] = replace(right, wrap_before=wrap_separator(left.lines[-1], right.lines[0], tokenize))
    return result
