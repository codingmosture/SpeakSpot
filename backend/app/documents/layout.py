"""Internal layout evidence for semantic grouping; never rewrites source blocks."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.models import BBox, Block


def extract_layout_boxes(page) -> list[BBox]:
    """Use closed rectangles/rounded panels, ignoring rules and faint shadows."""
    width, height = page.rect.width, page.rect.height
    boxes = []
    panels = []
    drawings = page.get_cdrawings() if hasattr(page, "get_cdrawings") else page.get_drawings()
    for drawing in drawings:
        items = drawing.get("items", [])
        rectangle = len(items) == 1 and items[0][0] == "re"
        rounded = (sum(item[0] == "c" for item in items) >= 2
                   and sum(item[0] == "l" for item in items) >= 2
                   and drawing.get("fill") is not None)
        opacity = drawing.get("fill_opacity")
        if not (rectangle or rounded) or (opacity is not None and opacity < 0.5):
            continue
        # Semantic layout currently handles only unrotated horizontal pages.
        if page.rotation:
            return []
        rect = drawing["rect"]
        box = (rect[0] / width, rect[1] / height, rect[2] / width, rect[3] / height)
        w, h = box[2] - box[0], box[3] - box[1]
        if not (0.06 <= w <= 0.95 and 0.025 <= h <= 0.85):
            continue
        (panels if rounded else boxes).append(box)

    # A rounded header strip and a taller rounded body can be one callout.
    # Rectangle cells are never coalesced, even if their edges align perfectly.
    consumed = set()
    for i, header in enumerate(panels):
        if i in consumed:
            continue
        matches = [(j, body) for j, body in enumerate(panels) if j != i and j not in consumed
                   and abs(header[0] - body[0]) <= 0.003 and abs(header[2] - body[2]) <= 0.003
                   and 0 <= body[1] - header[3] <= 0.015
                   and body[3] - body[1] >= 1.5 * (header[3] - header[1])]
        if len(matches) == 1:
            j, body = matches[0]
            boxes.append((header[0], header[1], body[2], body[3]))
            consumed.update((i, j))
    boxes.extend(panel for i, panel in enumerate(panels) if i not in consumed)
    # Offset backplates/shadows can be emitted as opaque rectangles. Collapse
    # only near-duplicate panel-sized boxes, never small nested diagram cells.
    panel_boxes = [b for b in boxes if b in panels or any(
        abs(b[0] - p[0]) <= 0.003 and abs(b[1] - p[1]) <= 0.003 for p in panels)]
    boxes = [b for b in boxes if not any(
        b != panel and abs(b[0] - panel[0]) <= 0.025 and abs(b[2] - panel[2]) <= 0.025
        and 0 <= b[1] - panel[1] <= 0.025 and abs(b[3] - panel[3]) <= 0.025
        and b[3] - b[1] >= 0.65 * (panel[3] - panel[1]) for panel in panel_boxes)]
    return sorted(set(boxes))


def region_for_bbox(bbox: BBox, boxes: list[BBox]) -> str | None:
    """Prefer the smallest enclosing cell; ambiguous intersecting boxes isolate."""
    tolerance = 0.003
    containing = [box for box in boxes
                  if box[0] - tolerance <= bbox[0] and box[1] - tolerance <= bbox[1]
                  and box[2] + tolerance >= bbox[2] and box[3] + tolerance >= bbox[3]]
    if containing:
        containing.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
        smallest = containing[0]
        if any(not (b[0] <= smallest[0] + tolerance and b[1] <= smallest[1] + tolerance
                       and b[2] >= smallest[2] - tolerance and b[3] >= smallest[3] - tolerance)
               for b in containing[1:]):
            return f"boundary:{bbox}"
        return f"box:{smallest}"
    # A block straddling a boundary must not become a bridge between regions.
    if any(min(bbox[2], b[2]) > max(bbox[0], b[0]) + tolerance
           and min(bbox[3], b[3]) > max(bbox[1], b[1]) + tolerance for b in boxes):
        return f"boundary:{bbox}"
    return None


def reading_streams(blocks: list[Block], styles) -> list[list[Block]]:
    """Split only sustained, empty column gutters; wide blocks remain barriers."""
    result = []
    pages = {}
    for block in blocks:
        pages.setdefault(block.page, []).append(block)
    for page_blocks in pages.values():
        ordered = sorted(page_blocks, key=lambda b: (b.bbox[1], b.bbox[0], b.block_id))
        horizontal = all(styles[b.block_id].horizontal for b in ordered if b.block_id in styles)
        candidates = [b for b in ordered if b.block_type not in {"title", "other"}
                      and b.bbox[2] - b.bbox[0] <= 0.72]
        cuts = []
        if horizontal:
            intervals = sorted((b.bbox[0], b.bbox[2]) for b in candidates)
            right_edge = -1.0
            for left, right in intervals:
                if right_edge >= 0 and left - right_edge >= 0.025:
                    cut = (right_edge + left) / 2
                    lhs = [b for b in candidates if b.bbox[2] <= cut]
                    rhs = [b for b in candidates if b.bbox[0] >= cut]
                    if len(lhs) >= 2 and len(rhs) >= 2:
                        overlap = min(max(b.bbox[3] for b in lhs), max(b.bbox[3] for b in rhs)) - max(
                            min(b.bbox[1] for b in lhs), min(b.bbox[1] for b in rhs))
                        if overlap >= 0.12:
                            cuts.append((left - right_edge, cut))
                right_edge = max(right_edge, right)
        if not cuts:
            streams = [ordered]
        else:
            _, cut = max(cuts)
            streams, band = [], []

            def flush():
                for side in (False, True):
                    column = [b for b in band if (b.bbox[0] >= cut) == side]
                    if column:
                        streams.append(column)
                band.clear()

            for block in ordered:
                if block.block_type in {"title", "other"} or block.bbox[0] < cut < block.bbox[2]:
                    flush()
                    streams.append([block])
                else:
                    band.append(block)
            flush()
        # Box boundaries also constrain single-column pages. Keep source order
        # inside each run and never skip a foreign/ambiguous region to merge.
        for stream in streams:
            run = []
            region = None
            for block in stream:
                current = getattr(styles.get(block.block_id), "region_id", None)
                if run and current != region:
                    result.append(run)
                    run = []
                run.append(block)
                region = current
            if run:
                result.append(run)
    return result
