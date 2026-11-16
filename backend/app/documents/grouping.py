"""Conservative layout grouping. Source Blocks and public schemas stay untouched."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace
from statistics import median
from typing import TYPE_CHECKING, Mapping, Sequence
from app.documents.layout import reading_streams
from app.documents.wrapping import TextLine

if TYPE_CHECKING:
    from app.schemas.models import BBox, Block


_BULLET = re.compile(r"^(?:[◼•●○▪▫◆◇▶▷\-*]|\d+[.)]|\([0-9a-zA-Z가-힣]+\))\s*")
_TERMINAL = re.compile(r"[.!?。！？][\"'’”)]*$")
_CONNECTOR = re.compile(r"(?:[,;:]|\b(?:and|or|of|to|the|a|an|with|for|in|on)|(?:으며|하고|하는|되는|위한|따라|및))$", re.I)
_PROTECTED = {"table", "caption", "equation", "other"}
_NUMBERED = re.compile(r"^(?:\d+(?:[.)]|\u20dd|\u20e3)|[①-⑳])\s*|^\d+\s+")


@dataclass(frozen=True)
class BlockStyle:
    """Parser-only evidence; sizes are PDF points, not schema fields."""

    font_size: float = 0.0
    bold: bool = False
    horizontal: bool = True
    region_id: str | None = None
    lines: tuple[TextLine, ...] = ()
    search_text: str | None = None
    wrap_before: str | None = None
    lead_bold: bool = False


@dataclass(frozen=True)
class SemanticGroup:
    """A separate search-unit candidate, with lossless highlight provenance."""

    group_id: str
    page: int
    source_block_ids: tuple[str, ...]
    text: str
    bbox: BBox
    rects: tuple[BBox, ...]
    parent_group_id: str | None = None
    supplements_group_ids: tuple[str, ...] = ()


def _content(block: Block) -> str:
    return _BULLET.sub("", block.text.strip(), count=1).strip()


def _short_label(block: Block) -> bool:
    text = _content(block)
    return bool(text) and len(text) <= 100 and len(text.split()) <= 12 and not _TERMINAL.search(text)


def _line_height(block: Block) -> float:
    return median([r[3] - r[1] for r in block.rects] or [block.bbox[3] - block.bbox[1]])


def _near(a: Block, b: Block, *, heading: bool = False) -> bool:
    """Reject overlaps, other columns and large whitespace before language rules."""
    height = min(_line_height(a), _line_height(b))
    gap = b.bbox[1] - a.bbox[3]
    overlap = min(a.bbox[2], b.bbox[2]) - max(a.bbox[0], b.bbox[0])
    narrow = min(a.bbox[2] - a.bbox[0], b.bbox[2] - b.bbox[0])
    return (a.page == b.page and -0.15 * height <= gap <= min(0.055, height * (1.8 if heading else 0.8))
            and overlap >= 0.5 * narrow)


def _continues(a: Block, b: Block) -> bool:
    left, right = a.text.rstrip(), b.text.lstrip()
    if not left or not right or _TERMINAL.search(left) or _BULLET.match(right):
        return False
    # Case alone is insufficient: the previous fragment also needs an unfinished
    # grammatical ending. Korean uses explicit connective endings here.
    return bool(_CONNECTOR.search(left) or
                (re.search(r"[A-Za-z]-$", left) and re.match(r"[a-z]", right)))


def _join(left: str, right: str, *, continuation: bool) -> str:
    if continuation and re.search(r"[A-Za-z]-$", left) and re.match(r"[a-z]", right):
        return left[:-1] + right
    return left + (" " if continuation else "\n") + right


def _list_relations(ordered, styles, protected, headings):
    """Recognize explicit bold labels or repeated parallel label endings.

    Plain descriptive bullets are deliberately left to the existing merger.
    Relations use source IDs here and are resolved to group IDs after grouping.
    """
    parents, notes = {}, {}
    for i, root in enumerate(ordered):
        root_style = styles.get(root.block_id, BlockStyle())
        if protected(root) or root.block_id not in headings or not root_style.bold:
            continue
        children = []
        previous = root
        note = None
        for candidate in ordered[i + 1:]:
            if protected(candidate) or not _near(previous, candidate, heading=True):
                break
            if candidate.bbox[0] - root.bbox[0] < 0.018:
                break
            if children and abs(candidate.bbox[0] - children[0].bbox[0]) > 0.012:
                break
            if re.match(r"^\*\s+", candidate.text.strip()):
                note = candidate
                break
            if candidate.block_type != "bullet" or not _short_label(candidate):
                break
            children.append(candidate)
            previous = candidate
        if not children:
            continue
        bold_labels = all(styles.get(c.block_id, BlockStyle()).bold for c in children)
        # A shared multi-character suffix in three or more short, unpunctuated
        # labels supplies parallel-list evidence without topic vocabulary.
        labels = [_content(c) for c in children]
        suffix = _common_suffix(labels)
        parallel = (len(children) >= 3 and len(suffix) >= 4
                    and all(len(s) <= 60 and not re.search(r"[,;.!?]", s) for s in labels))
        if not (bold_labels or parallel):
            continue
        for child in children:
            parents[child.block_id] = root.block_id
        if (note is not None and len(children) >= 2
                and not styles.get(note.block_id, BlockStyle()).bold):
            notes[note.block_id] = (root.block_id, *(c.block_id for c in children))
    return parents, notes


def _common_suffix(labels):
    """Shared literal ending, used only as structural parallelism evidence."""
    shortest = min(labels, key=len)
    for size in range(len(shortest), 0, -1):
        if all(label.endswith(shortest[-size:]) for label in labels):
            return shortest[-size:]
    return ""


def group_blocks(blocks: Sequence[Block], styles: Mapping[str, BlockStyle] | None = None) -> list[SemanticGroup]:
    """Group independent spatial streams without changing the source Blocks."""
    styles = styles or {}
    if len({b.block_id for b in blocks}) != len(blocks):
        raise ValueError("Semantic grouping requires unique source block IDs")
    ordered = sorted(blocks, key=lambda b: (b.page, b.bbox[1], b.bbox[0], b.block_id))
    streams = reading_streams(ordered, styles)
    flattened = [b for stream in streams for b in stream]
    reordered_pages = {b.page for a, b in zip(ordered, flattened) if a.block_id != b.block_id}
    page_counts = Counter(b.page for b in ordered)
    return [group for stream in streams
            for group in _group_stream(stream, styles,
                                       stream[0].page in reordered_pages or len(stream) < page_counts[stream[0].page])]


def _group_stream(blocks: Sequence[Block], styles: Mapping[str, BlockStyle], spatial=False) -> list[SemanticGroup]:
    """Detect topic starts first, then attach only structurally supported children.

    Every input ID appears once. Titles, figures, tables and uncertain blocks are
    singleton groups. No source object, coordinate or keyword is mutated.
    """
    styles = styles or {}
    if len({b.block_id for b in blocks}) != len(blocks):
        raise ValueError("Semantic grouping requires unique source block IDs")
    ordered = sorted(blocks, key=lambda b: (b.page, b.bbox[1], b.bbox[0], b.block_id))

    def style(b: Block) -> BlockStyle:
        return styles.get(b.block_id, BlockStyle())

    def protected(b: Block) -> bool:
        return b.block_type in _PROTECTED or b.block_type == "title" or not style(b).horizontal

    def font_heading(a: Block, b: Block) -> bool:
        same_panel = (style(a).region_id is not None and style(a).region_id.startswith("box:")
                      and style(a).region_id == style(b).region_id)
        return (style(b).font_size > 0 and
                style(a).font_size >= style(b).font_size * (1.05 if same_panel else 1.15))

    # Precompute boundaries independently of accumulated groups. A short bullet
    # is always a boundary; a plain label needs typography, colon or indentation.
    headings: set[str] = set()
    strong_labels: set[str] = set()
    for i, block in enumerate(ordered):
        if protected(block) or not _short_label(block):
            continue
        following = ordered[i + 1] if i + 1 < len(ordered) else None
        if (following is not None and style(block).lead_bold and style(following).font_size > 0
                and style(block).font_size >= style(following).font_size * 1.1):
            strong_labels.add(block.block_id)
        indented_child = (following is not None and not protected(following)
                          and _near(block, following, heading=True)
                          and following.bbox[0] - block.bbox[0] >= 0.018)
        typography = (following is not None and following.page == block.page and
                      ((style(block).bold and not style(following).bold) or
                       font_heading(block, following)))
        if block.block_type == "bullet" or _BULLET.match(block.text.strip()) or block.text.rstrip().endswith(":") or indented_child or typography:
            if style(block).wrap_before is None:
                headings.add(block.block_id)

    parents, notes = _list_relations(ordered, styles, protected, headings)
    separate = set(parents) | set(parents.values()) | set(notes)
    members: list[list[Block]] = []
    joins: list[list[bool]] = []
    for block in ordered:
        attach = False
        continuation = False
        if members:
            root, previous = members[-1][0], members[-1][-1]
            if (not protected(block) and not protected(previous) and not protected(root)
                    and block.block_id not in separate and root.block_id not in separate):
                indent = block.bbox[0] - root.bbox[0]
                root_heading = root.block_id in headings
                # A deeper bullet can be a child of a topic, while peers are
                # boundaries even when separated by only a few PDF points.
                new_heading = block.block_id in headings
                # Bold child labels are independent subtopics, even below a
                # broader bold heading (e.g. a list of resource categories).
                independent_label = new_heading and (style(block).bold or block.block_id in strong_labels)
                child = root_heading and indent >= 0.018 and not independent_label
                same_style = (not style(block).font_size or not style(previous).font_size or
                              abs(style(block).font_size - style(previous).font_size) <= style(previous).font_size * 0.1)
                continuation = (_near(previous, block) and same_style and
                                abs(block.bbox[0] - previous.bbox[0]) <= 0.012 and
                                not new_heading and _continues(previous, block))
                wrapped = style(block).wrap_before is not None
                continuation = continuation or wrapped
                # Same-indent descriptions require typographic or explicit
                # heading evidence; short ordinary text alone is ambiguous.
                labelled = (root.text.rstrip().endswith(":") or
                            (style(root).bold and not style(block).bold) or
                            font_heading(root, block))
                description = (root_heading and not new_heading and labelled and
                               block.block_type == "paragraph" and abs(indent) <= 0.012)
                attach = (_near(previous, block, heading=True) and
                          ((child and (not new_heading or block.block_type == "bullet")) or description or continuation))
                # Reordering must not create new mergers between independently
                # numbered list items that the source parser called paragraphs.
                if spatial and not wrapped and (_NUMBERED.match(block.text.strip()) or _NUMBERED.match(root.text.strip())):
                    attach = False
        if attach:
            members[-1].append(block)
            joins[-1].append(continuation)
        else:
            members.append([block])
            joins.append([])

    result = []
    for group, continuation_flags in zip(members, joins):
        text = style(group[0]).search_text if style(group[0]).search_text is not None else group[0].text
        for b, continuation in zip(group[1:], continuation_flags):
            right = style(b).search_text if style(b).search_text is not None else b.text
            if continuation and style(b).wrap_before is not None:
                text += style(b).wrap_before + right
            else:
                text = _join(text, right, continuation=continuation)
        result.append(SemanticGroup(
            group_id=f"sg_{group[0].block_id}", page=group[0].page,
            source_block_ids=tuple(b.block_id for b in group), text=text,
            bbox=(min(b.bbox[0] for b in group), min(b.bbox[1] for b in group),
                  max(b.bbox[2] for b in group), max(b.bbox[3] for b in group)),
            rects=tuple(r for b in group for r in b.rects),
        ))
    source_groups = {source: g.group_id for g in result for source in g.source_block_ids}
    return [replace(g,
                    parent_group_id=source_groups[parents[g.source_block_ids[0]]]
                    if g.source_block_ids[0] in parents else None,
                    supplements_group_ids=tuple(source_groups[s] for s in notes.get(g.source_block_ids[0], ())))
            for g in result]
