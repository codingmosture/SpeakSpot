"""Store the reusable part of PDF search in the same browser as the PDF."""
from dataclasses import asdict, dataclass
from functools import cached_property
from hashlib import sha256
from io import BytesIO
import json
from zipfile import ZipFile

import numpy as np

from pdf import Block
import search

VERSION = 1
MAX_SNAPSHOT_BYTES = 512 * 1024 * 1024


@dataclass
class PdfAnalysis:
    key: str
    pages: int
    blocks: list
    layout: dict
    batches: dict
    index: search.PassageSearch

    # 뷰어의 강조 레이어에서 사용할 블록 좌표를 반환한다.
    @cached_property
    def geometry(self):
        return [{'id': block.block_id, 'page': block.page, 'rects': block.rects} for block in self.blocks]


# 색인을 만들 때 임베딩한 텍스트 묶음의 식별값을 만든다.
def batch_key(texts):
    return sha256(json.dumps(texts, ensure_ascii=False).encode()).hexdigest()


# OCR로 읽은 PDF를 검색 가능한 색인으로 만들고 계산한 벡터를 모은다.
def build_analysis(key, pages, blocks, layout):
    batches = {}

    # 검색용 벡터를 계산해 이후 저장과 복원에 사용할 수 있게 한다.
    def embed(texts):
        vectors = search._embed(texts)
        batches[batch_key(texts)] = vectors
        return vectors

    index = search.PassageSearch(blocks, page_count=pages, native_layout=layout, index_embed=embed)
    return PdfAnalysis(key, pages, blocks, layout, batches, index)


# 실행 중인 발화 상태를 제외한 분석 데이터만 브라우저에 보낼 바이너리로 만든다.
def pack_analysis(analysis):
    metadata = json.dumps({
        'version': VERSION, 'key': analysis.key, 'pages': analysis.pages,
        'blocks': [asdict(block) for block in analysis.blocks], 'layout': analysis.layout,
    }, ensure_ascii=False).encode()
    buffer = BytesIO()
    np.savez(buffer, metadata=np.frombuffer(metadata, dtype=np.uint8), **analysis.batches)
    return buffer.getvalue()


# 저장된 텍스트와 벡터를 읽어 OCR이나 임베딩 없이 검색 색인을 복원한다.
def unpack_analysis(key, data):
    buffer = BytesIO(data)
    with ZipFile(buffer) as archive:
        entries = archive.infolist()
        if not 2 <= len(entries) <= 4 or sum(entry.file_size for entry in entries) > len(data):
            raise ValueError('Invalid analysis archive')
    buffer.seek(0)
    with np.load(buffer, allow_pickle=False) as archive:
        metadata = json.loads(archive['metadata'].tobytes())
        if metadata['version'] != VERSION or metadata['key'] != key:
            raise ValueError('Analysis version or file mismatch')
        pages = metadata['pages']
        blocks = [Block(**block) for block in metadata['blocks']]
        layout = {int(page): lines for page, lines in metadata['layout'].items()}
        if type(pages) is not int or pages < 1 or not blocks or any(not 1 <= block.page <= pages for block in blocks):
            raise ValueError('Invalid analysis page or block data')
        batches = {name: archive[name] for name in archive.files if name != 'metadata'}
    dimensions = {array.shape[1] for array in batches.values() if array.ndim == 2}
    if len(dimensions) != 1 or any(
        array.ndim != 2 or array.dtype.kind != 'f' or not np.isfinite(array).all() for array in batches.values()
    ):
        raise ValueError('Invalid analysis vectors')

    # 저장 당시와 같은 텍스트 묶음에만 기존 벡터를 사용한다.
    def embed(texts):
        vectors = batches[batch_key(texts)]
        if len(vectors) != len(texts):
            raise ValueError('Invalid analysis batch length')
        return vectors

    index = search.PassageSearch(blocks, page_count=pages, native_layout=layout, index_embed=embed)
    return PdfAnalysis(key, pages, blocks, layout, batches, index)
