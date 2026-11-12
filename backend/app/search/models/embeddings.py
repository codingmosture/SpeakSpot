"""BGE-M3, multilingual E5-small 실제 SentenceTransformer dense 임베딩 어댑터.

의존성은 해당 모델을 선택한 시점에만 로딩된다. 문서 임베딩은 fingerprint 기준
최대 6개까지 프로세스 내 공유하며 동일한 문서를 세션별 재인코딩하지 않는다.
"""
import hashlib
import os
from collections import OrderedDict
from functools import lru_cache
from threading import RLock
from typing import Sequence

from app.schemas.models import Block
from app.search.models.base import ModelScore, ModelUnavailableError, ModelRequestError

_CACHE_LOCK = RLock()
_DOCUMENT_CACHE: OrderedDict[tuple[str, str, str], object] = OrderedDict()


@lru_cache(maxsize=3)
def _load_encoder(model_id: str, device: str | None):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise ModelUnavailableError(
            '임베딩 모델에 sentence-transformers가 필요합니다. '
            'python -m pip install -r requirements-search.txt'
        ) from exc
    try:
        return SentenceTransformer(model_id, device=device)
    except Exception as exc:
        raise ModelUnavailableError(f'모델을 로딩하지 못했습니다: {model_id} ({type(exc).__name__})') from exc


class SentenceTransformerSearchModel:
    kind = 'embedding'

    def __init__(self, name: str, default_model_id: str, env_prefix: str, e5: bool = False) -> None:
        self.name = name
        self.model_id = os.getenv(f'{env_prefix}_MODEL_ID', default_model_id)
        self.device = os.getenv(f'{env_prefix}_DEVICE') or None
        self.batch_size = int(os.getenv(f'{env_prefix}_BATCH_SIZE', '8'))
        if self.batch_size < 1:
            raise ValueError('BATCH_SIZE는 1 이상이어야 합니다.')
        self._e5 = e5
        self._vectors = None
        self._index: dict[str, int] = {}
        self._query_cache: OrderedDict[str, object] = OrderedDict()

    def _passage(self, text: str) -> str:
        return f'passage: {text}' if self._e5 else text

    def _query(self, text: str) -> str:
        return f'query: {text}' if self._e5 else text

    def prepare(self, document_id: str, blocks: Sequence[Block]) -> None:
        if not blocks:
            raise ValueError('비어 있는 블록 목록입니다.')
        fingerprint = hashlib.sha256()
        for block in blocks:
            fingerprint.update(repr((block.block_id, block.page, block.text, block.keywords)).encode('utf-8'))
        key = (f'{self.model_id}|e5={self._e5}', document_id, fingerprint.hexdigest())
        with _CACHE_LOCK:
            cached = _DOCUMENT_CACHE.get(key)
            if cached is not None:
                _DOCUMENT_CACHE.move_to_end(key)
        if cached is None:
            encoder = _load_encoder(self.model_id, self.device)
            try:
                cached = encoder.encode(
                    [self._passage(b.text + (' ' + ' '.join(b.keywords) if b.keywords else '')) for b in blocks],
                    batch_size=self.batch_size, normalize_embeddings=True,
                    show_progress_bar=False, convert_to_numpy=True,
                )
            except Exception as exc:
                raise ModelRequestError(f'{self.name} 문서 임베딩 실패: {type(exc).__name__}') from exc
            with _CACHE_LOCK:
                _DOCUMENT_CACHE[key] = cached
                _DOCUMENT_CACHE.move_to_end(key)
                while len(_DOCUMENT_CACHE) > 6:
                    _DOCUMENT_CACHE.popitem(last=False)
        self._vectors = cached
        self._index = {block.block_id: i for i, block in enumerate(blocks)}
        self._query_cache.clear()

    def rank(self, query: str, candidates: Sequence[Block]) -> list[ModelScore]:
        if self._vectors is None:
            raise RuntimeError(f'{self.name}: prepare()를 먼저 호출하세요.')
        if not candidates or not query.strip():
            return []
        vector = self._query_cache.get(query)
        if vector is None:
            encoder = _load_encoder(self.model_id, self.device)
            try:
                vector = encoder.encode(
                    self._query(query), normalize_embeddings=True,
                    show_progress_bar=False, convert_to_numpy=True,
                )
            except Exception as exc:
                raise ModelRequestError(f'{self.name} 발화 임베딩 실패: {type(exc).__name__}') from exc
            self._query_cache[query] = vector
            if len(self._query_cache) > 32:
                self._query_cache.popitem(last=False)
        else:
            self._query_cache.move_to_end(query)
        scores = []
        for block in candidates:
            index = self._index.get(block.block_id)
            if index is None:
                raise ValueError(f'인덱스에 없는 블록: {block.block_id}')
            cosine = float(self._vectors[index] @ vector)
            scores.append(ModelScore(block.block_id, max(0.0, min(1.0, cosine))))
        return sorted(scores, key=lambda x: (-x.score, x.block_id))


# 별도 클래스를 두어 이름만 바꾸면 실 모델도 선택된다.
class BGEM3Model(SentenceTransformerSearchModel):
    def __init__(self):
        super().__init__('bge-m3', 'BAAI/bge-m3', 'SEARCH_BGE_M3')


class E5SmallModel(SentenceTransformerSearchModel):
    def __init__(self):
        super().__init__('e5-small', 'intfloat/multilingual-e5-small', 'SEARCH_E5_SMALL', e5=True)
