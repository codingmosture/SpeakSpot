"""Extract text blocks and their coordinates from PDF layout and Korean/English OCR."""
from collections import defaultdict
from functools import lru_cache
from io import BytesIO
import logging
import re
import warnings
from docling.datamodel.base_models import ConversionStatus, DocumentStream, InputFormat
from docling.datamodel.pipeline_options import EasyOcrOptions, PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import ContentLayer, DocItemLabel, TableItem
from search import Block


@lru_cache(maxsize=1)
def converter():
    # EasyOCR requests pinned memory on CPU too; PyTorch already disables it.
    warnings.filterwarnings('ignore',
        message=r"'pin_memory' argument is set as true but no accelerator is found.*",
        category=UserWarning, module=r'torch\.utils\.data\.dataloader$')
    options = PdfPipelineOptions(ocr_options=EasyOcrOptions(lang=['ko', 'en']))
    return DocumentConverter(allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})


def _pattern(text):
    return ' '.join(re.sub(r'\d+', '#', text.casefold()).split())


def parse_pdf(data):
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
    return len(document.pages), blocks


if __name__ == '__main__':
    from search import page_encoder
    converter().initialize_pipeline(InputFormat.PDF)
    page_encoder(local_files_only=False)
    print('PDF layout, Korean/English OCR and the BGE-M3 page model are ready.')
