"""PDF 구조, OCR 블록, 제목과 도식의 좌표를 추출한다."""
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
import logging
import re
import unicodedata
import warnings

import pymupdf


@dataclass
class Block:
    block_id: str
    page: int
    text: str
    rects: list


PREFIX = re.compile(r'^(?:[•·▪]\s*|\d{1,2}[.)]\s*)')
PUNCT = re.compile(r'[\W_]+')
NEIGHBOR_SEPARATORS = ('상단', '위쪽', '가운데', '아래쪽')


@dataclass(frozen=True)
class IndexPage:
    header: str
    native: str
    structure: str
    focus: str


# 공백과 구두점을 제거해 PDF 제목이나 텍스트를 비교할 수 있게 한다.
def normalized(text):
    return PUNCT.sub('', text.casefold())


# 목록 기호를 빼고 검색에 사용할 줄의 길이를 제한한다.
def clean(text, limit=15):
    return ' '.join(PREFIX.sub('', text).split()[:limit])


# 정규화한 텍스트가 중복되는 줄을 첫 등장 순서대로 제거한다.
def unique(lines):
    output = {}
    for text in lines:
        key = normalized(text)
        if len(key) > 1 and key not in output:
            output[key] = text
    return list(output.values())


# 같은 PDF 텍스트 상자에서 줄바꿈된 문장을 이어 붙인다.
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


# 한 페이지의 제목, 전체 본문, 배치 설명, 핵심 텍스트를 구성한다.
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
        labels = (unique(band) for band in bands)
        pieces = [f'{side}에 {" · ".join(text[:5])}'
                  for side, text in zip(NEIGHBOR_SEPARATORS, labels, strict=True) if text]
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
    return IndexPage(title, native, structured, focus)


# PDF에서 추출한 줄의 유니코드와 공백을 정규화한다.
def clean_line(text):
    text = unicodedata.normalize('NFKC', text)
    return re.sub(r'\s+', ' ', text.replace('\u00ad', '')).strip()


# 페이지에서 텍스트 줄과 상대 위치, 글자 크기, 굵기를 읽는다.
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


# PDF 원본에서 모든 페이지의 텍스트 배치를 읽는다.
def read_layout(data):
    with pymupdf.open(stream=data, filetype='pdf') as document:
        return {page.number + 1: lines_for(page) for page in document}


# 한 블록에 속한 모든 강조 사각형을 감싸는 경계를 구한다.
def block_box(block):
    left, top, right, bottom = zip(*block.rects)
    return min(left), min(top), max(right), max(bottom)


# 읽기 순서와 위치가 이어지는 본문 블록들을 묶는다.
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


# 한글·영문 OCR을 설정한 Docling 변환기를 준비해 재사용한다.
@lru_cache(maxsize=1)
def converter():
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import EasyOcrOptions, PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    # EasyOCR requests pinned memory on CPU too; PyTorch already disables it.
    warnings.filterwarnings('ignore',
        message=r"'pin_memory' argument is set as true but no accelerator is found.*",
        category=UserWarning, module=r'torch\.utils\.data\.dataloader$')
    options = PdfPipelineOptions(ocr_options=EasyOcrOptions(lang=['ko', 'en']))
    return DocumentConverter(allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})


# 페이지 번호처럼 바뀌는 숫자를 지워 반복 머리말과 꼬리말을 비교한다.
def _pattern(text):
    return ' '.join(re.sub(r'\d+', '#', text.casefold()).split())


# PDF를 변환해 강조할 블록과 좌표를 얻고 필요하면 원본 배치도 반환한다.
def parse_pdf(data, *, include_layout=False):
    from docling.datamodel.base_models import ConversionStatus, DocumentStream
    from docling_core.types.doc import ContentLayer, DocItemLabel, TableItem

    try:
        result = converter().convert(DocumentStream(name='document.pdf', stream=BytesIO(data)))
    except Exception as exc:
        logging.getLogger(__name__).exception('PDF layout/OCR conversion failed')
        raise ValueError('PDF 구조 분석 또는 OCR을 실행하지 못했습니다. '
            '가상환경에서 python pdf.py를 실행해 모델을 준비하고 PDF 파일을 확인하세요.') from exc
    if result.status != ConversionStatus.SUCCESS:
        raise ValueError('일부 PDF 페이지를 읽지 못했습니다. 손상되거나 암호화된 파일인지 확인하세요.')
    document = result.document
    items = list(document.iterate_items(traverse_pictures=True,
        included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE}))
    # Let model labels identify furniture; learn its recurring text across pages.
    furniture = {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER}
    recurring = defaultdict(set)
    for item, _ in items:
        if item.label in furniture and getattr(item, 'text', '').strip():
            recurring[_pattern(item.text)].update(prov.page_no for prov in item.prov)
    repeated = {pattern for pattern, pages in recurring.items() if len(pages) > 1}
    blocks = []
    for item, _ in items:
        text = item.export_to_markdown(doc=document) if isinstance(item, TableItem) else getattr(item, 'text', '')
        if not text.strip() or item.label in furniture or (
            item.content_layer == ContentLayer.FURNITURE and _pattern(text) in repeated):
            continue
        by_page = defaultdict(list)
        for prov in item.prov:
            size = document.pages[prov.page_no].size
            box = prov.bbox.to_top_left_origin(page_height=size.height)
            rect = tuple(min(1., max(0., value)) for value in
                         (box.l / size.width, box.t / size.height, box.r / size.width, box.b / size.height))
            if rect[0] < rect[2] and rect[1] < rect[3]:
                by_page[prov.page_no].append(rect)
        for page, rects in by_page.items():
            blocks.append(Block(f'p{page:04d}_b{len(blocks)+1:04d}', page, text.strip(), rects))
    if not blocks:
        raise ValueError('PDF에서 읽을 수 있는 본문을 찾지 못했습니다.')
    pages = len(document.pages)
    if not include_layout:
        return pages, blocks
    try:
        layout = read_layout(data)
    except (RuntimeError, ValueError):
        logging.getLogger(__name__).warning('Native PDF layout could not be read; using Docling blocks', exc_info=True)
        layout = {}
    return pages, blocks, layout


if __name__ == '__main__':
    from docling.datamodel.base_models import InputFormat
    from search import page_encoder
    converter().initialize_pipeline(InputFormat.PDF)
    page_encoder(local_files_only=False)
    print('PDF layout, Korean/English OCR and the BGE-M3 page model are ready.')
