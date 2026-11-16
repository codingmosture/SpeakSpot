"""Project semantic units onto the existing public Block contract."""
from collections import Counter
from typing import Sequence

from app.documents.grouping import SemanticGroup
from app.schemas.models import Block


def groups_to_blocks(sources: Sequence[Block], groups: Sequence[SemanticGroup]) -> list[Block]:
    """Create independent copies; retain provenance in sources/groups, not JSON fields.

    Singletons retain original metadata, using the group's repaired search text.
    Merged explanatory units are paragraphs
    with a group ID, group text/bbox, exact source rects and ordered unique keywords.
    """
    by_id = {b.block_id: b for b in sources}
    membership = Counter(s for g in groups for s in g.source_block_ids)
    if len(by_id) != len(sources) or membership != Counter(by_id.keys()):
        raise ValueError("Groups must partition unique source block IDs exactly once")
    result = []
    for group in groups:
        if not group.source_block_ids:
            raise ValueError("Empty semantic groups cannot become Blocks")
        members = [by_id[s] for s in group.source_block_ids]
        if any(b.page != group.page for b in members):
            raise ValueError("A semantic Block cannot span pages")
        if len(members) == 1:
            result.append(members[0].model_copy(update={"text": group.text}, deep=True))
        else:
            result.append(Block(
                block_id=group.group_id, page=group.page, text=group.text,
                block_type="paragraph", bbox=group.bbox,
                rects=[rect for b in members for rect in b.rects],
                keywords=list(dict.fromkeys(word for b in members for word in b.keywords)),
            ))
    if len({b.block_id for b in result}) != len(result):
        raise ValueError("Converted semantic Block IDs must be unique")
    return result
