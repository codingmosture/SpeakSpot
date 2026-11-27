"""Read native PDF text and geometry for search while keeping Docling highlight IDs."""
from dataclasses import dataclass
import re
import unicodedata

import pymupdf

from lecture_terms import additions

PREFIX = re.compile(r'^(?:[•·▪]\s*|\d{1,2}[.)]\s*)')
PUNCT = re.compile(r'[\W_]+')
NEIGHBOR_SEPARATORS = ('상단', '위쪽', '가운데', '아래쪽')


@dataclass(frozen=True)
class IndexPage:
    header: str
    native: str
    structure: str
    focus: str


def normalized(text):
    return PUNCT.sub('', text.casefold())


def clean(text, limit=15):
    return ' '.join(PREFIX.sub('', text).split()[:limit])


def unique(lines):
    output = {}
    for text in lines:
        key = normalized(text)
        if len(key) > 1 and key not in output:
            output[key] = text
    return list(output.values())


def bilingual(text):
    aliases = sorted(set(additions(text)))
    return text if not aliases else f'{text}. {" | ".join(aliases[:12])}'


def compact_lines(lines):
    text = []
    for i, row in enumerate(lines):
        item = clean(row['text'])
        if not item:
            continue
        if (i and lines[i-1]['group'] == row['group'] and text and row['box'][1] - lines[i-1]['box'][3] < .032
                and row['box'][0] <= lines[i-1]['box'][0] + .035
                and abs(row['size'] - lines[i-1]['size']) <= .15
                and not PREFIX.match(row['text'])):
            text[-1] += ' ' + item
        else:
            text.append(item)
    return unique(text)


def build_one(rows, raw_lines):
    lines = sorted((line for line in raw_lines
                    if .012 <= line['box'][1] <= .935 and abs(line['rotation']) <= .05
                    and not line['text'].isdecimal()), key=lambda r: (round(r['box'][1], 2), r['box'][0]))
    if not lines:
        raw = ' | '.join(unique(clean(row.text) for row in rows)[:18])
        return IndexPage(clean(rows[0].text), raw, raw, raw)
    prominent = [line for line in lines if line['box'][1] <= .16]
    best = max(prominent, key=lambda row: row['size'], default=lines[0])
    title = clean(best['text'])
    body = [line for line in lines if line is not best]
    raw = compact_lines(body)
    old = unique(clean(row.text) for row in rows if normalized(row.text) != normalized(title))
    use_old = len(raw) <= 2 or sum(len(text) for text in raw) < .35 * sum(len(text) for text in old)
    if use_old:
        raw = unique([*raw, *old])
    letters = sum(2 <= len(line['text'].split()) <= 5 for line in body)
    columns = {min(4, int(5 * (line['box'][0] + line['box'][2]) / 2)) for line in body}
    diagram = len(columns) >= 3 and len(body) >= 7 and letters >= .45 * len(body)
    regular = [line['size'] for line in body]
    normal_size = sorted(regular)[len(regular)//2] if regular else best['size']
    keys = [line for line in body if line['bold'] and line['size'] >= normal_size * .94 and line['box'][0] <= .65]
    if diagram:
        bands = [[] for _ in NEIGHBOR_SEPARATORS]
        for line in body:
            if len(line['text']) > 2:
                bands[min(3, int((line['box'][1] + line['box'][3]) * 2))].append(clean(line['text']))
        pieces = [f'{side}에 {" · ".join(unique(text)[:5])}'
                  for side, text in zip(NEIGHBOR_SEPARATORS, bands, strict=True) if unique(text)]
        order = ' ; '.join(pieces)
        structured = f'{title}. 도식: {order}. 아래에서 위로 {"; ".join(p for p in reversed(pieces))}.'
    else:
        grouped = []
        pending = []
        for line in body:
            if line in keys and pending:
                grouped.append(' · '.join(unique(pending)[:8]))
                pending = []
            pending.append(clean(line['text']))
        if pending:
            grouped.append(' · '.join(unique(pending)[:8]))
        if use_old or not grouped:
            grouped = raw[:16]
        structured = f'{title}. {" / ".join(grouped[:10])}'
    core = unique([*map(lambda line: clean(line['text']), keys), *raw[:10]])[:12]
    native = f'{title}. {" / ".join(raw[:20])}'
    focus = f'{title}. {" · ".join(core)}'
    return IndexPage(title, bilingual(native), bilingual(structured), bilingual(focus))


def clean_line(text):
    text = unicodedata.normalize('NFKC', text)
    return re.sub(r'\s+', ' ', text.replace('\u00ad', '')).strip()


def lines_for(page):
    width, height = page.rect.width, page.rect.height
    lines = []
    flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    for group_index, block in enumerate(page.get_text('dict', flags=flags)['blocks']):
        for line in block.get('lines', []):
            spans = [(span, clean_line(span['text'])) for span in line['spans']]
            spans = [(span, text) for span, text in spans if text]
            if not spans:
                continue
            fragments = []
            prev = None
            for span, text in spans:
                if prev is not None:
                    x_gap = span['bbox'][0] - prev['bbox'][2]
                    if prev['text'].endswith(' ') or span['text'].startswith(' ') or x_gap >= max(1., span['size'] * .12):
                        fragments.append(' ')
                fragments.append(text)
                prev = span
            text = clean_line(''.join(fragments))
            if len(text) < 2:
                continue
            x1, y1, x2, y2 = line['bbox']
            box = [round(float(coord / (width if i % 2 == 0 else height)), 5)
                   for i, coord in enumerate((x1, y1, x2, y2))]
            lines.append({
                'text': text, 'box': box, 'size': round(max(span['size'] for span, _ in spans), 2),
                'bold': any(span['flags'] & 16 for span, _ in spans), 'group': group_index,
                'rotation': round(float(line['dir'][1]), 3),
            })
    return lines



def read_layout(data):
    with pymupdf.open(stream=data, filetype='pdf') as document:
        return {page.number + 1: lines_for(page) for page in document}


def block_box(block):
    left, top, right, bottom = zip(*block.rects)
    return min(left), min(top), max(right), max(bottom)


def layout_groups(blocks):
    boxes = [block_box(block) for block in blocks]
    groups, active = [], []
    for i, box in enumerate(boxes):
        left, top, _, bottom = box
        if active:
            previous = boxes[active[-1]]
            root = boxes[active[0]]
            gap = top - previous[3]
            same_column = abs(left - previous[0]) <= .08
            deeper = previous[0] + .01 < left <= previous[0] + .13
            dedent = len(active) > 1 and left < previous[0] - .025 and left <= root[0] + .015
            ordered = top > previous[1] + .005 and -.018 < gap <= .075
            similar_height = bottom - top <= max(.10, 1.55 * (previous[3] - previous[1]))
            if not ((same_column or deeper) and ordered and similar_height and not dedent) or len(active) >= 5:
                if len(active) >= 2:
                    groups.append(tuple(active))
                active = []
        active.append(i)
    if len(active) >= 2:
        groups.append(tuple(active))
    return groups
