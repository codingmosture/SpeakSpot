"""Page tracking and block highlights from streaming speech."""
from collections import Counter, OrderedDict, defaultdict, deque
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from time import monotonic_ns

import numpy as np

from lecture_matching import LectureMatching, PageTracker
from pdf import Block
from text_index import PageIndex, lexical_terms, normalize

_MODEL = 'BAAI/bge-m3'
_ENCODER_LOCK = Lock()
_SHORT_WORDS = 11
_CONTEXT_WORDS = 28
_SAMPLE_MS = 1500
_BLOCK_SWITCH_LEAD = .05
_BLOCK_RUNNER_UP_MARGIN = .015
_BLOCK_RETURN_MS = 6000
_BLOCK_EVIDENCE_GAP_MS = 7000
_BLOCK_TERM_BONUS = .09
_BLOCK_TERM_BONUS_FLOOR = .42


@dataclass(frozen=True, slots=True)
class SearchResult:
    page: int | None = None
    block_id: str | None = None


class BlockHighlight:
    # 블록 강조에 필요한 후보와 전환 상태를 초기화한다.
    def __init__(self):
        self.reset()

    # 현재 강조와 대기 후보를 초기화한다.
    def reset(self):
        self._active = None
        self._previous = None
        self._changed_at = None
        self._last_at = None
        self.clear_pending()

    # 아직 확인되지 않은 강조 후보만 지운다.
    def clear_pending(self):
        self._pending = None

    # 점수 차이와 후보 반복을 확인해 새로 강조할 블록을 고른다.
    def observe(self, scores, minimum, at):
        if self._last_at is not None and at - self._last_at > _BLOCK_EVIDENCE_GAP_MS:
            self.clear_pending()
        self._last_at = at
        best = int(np.argmax(scores))
        if scores[best] < minimum or (self._active is not None and scores[best] <= scores[self._active]):
            self.clear_pending()
            return None

        if self._active is not None and self._pending != best:
            returning = best == self._previous and at - self._changed_at <= _BLOCK_RETURN_MS
            close = len(scores) > 1 and scores[best] - np.partition(scores, -2)[-2] < _BLOCK_RUNNER_UP_MARGIN
            if returning or close or scores[best] - scores[self._active] < _BLOCK_SWITCH_LEAD:
                self._pending = best
                return None

        self._previous, self._active = self._active, best
        self._changed_at = at
        self.clear_pending()
        return best


# 로컬 BGE-M3 임베딩 모델을 불러와 재사용한다.
@lru_cache(maxsize=1)
def page_encoder(*, local_files_only=True):
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(min(torch.get_num_threads(), 4))
    return SentenceTransformer(_MODEL, device='cpu', local_files_only=local_files_only)


# 텍스트를 BGE-M3 벡터로 변환하고 모델 호출을 직렬화한다.
def _embed(texts):
    with _ENCODER_LOCK:
        return page_encoder().encode(
            texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False,
        )


# 검색 주기와 전환 시간을 잴 단조 시계 값을 밀리초로 읽는다.
def _clock_ms():
    return monotonic_ns() // 1_000_000


class PassageSearch:
    # 한 문서의 페이지 검색 인덱스, 블록 벡터, 발화 상태를 준비한다.
    def __init__(self, blocks, page_count, *, native_layout=None):
        self.page_count = page_count
        self.current_page = 1
        self._lock = Lock()
        self._sequence = -1
        self._finalized = OrderedDict()
        self._recognition_cycle = None
        self._committed = deque(maxlen=_CONTEXT_WORDS)
        self._last_query = ()
        self._next_sample = 0

        page_text = defaultdict(list)
        self._block_indices = defaultdict(list)
        self._block_ids = []
        for index, block in enumerate(blocks):
            page_text[block.page].append(block.text)
            self._block_indices[block.page].append(index)
            self._block_ids.append(block.block_id)
        self._index = PageIndex(page_text)
        self._highlight = BlockHighlight()

        if page_count > 1:
            passages, self._passage_starts = self._index.passages()
            self._page_vectors = _embed(passages)
        self._block_vectors = _embed([' '.join(block.text.split()[:72]) for block in blocks])
        self._block_terms = self._distinctive_block_terms(blocks)
        self._lecture = (LectureMatching(self._index.pages, blocks, native_layout, _embed, self._block_vectors)
                         if native_layout is not None else None)
        self._tracker = PageTracker(self._index.pages) if self._lecture is None else None

    # 유효한 수동 페이지를 검색 기준으로 적용하고 강조 상태를 초기화한다.
    def correct_page(self, page):
        with self._lock:
            if not 1 <= page <= self.page_count:
                raise ValueError('문서 범위 안의 페이지를 선택해주세요.')
            self.current_page = page
            at = _clock_ms()
            if self._lecture is not None:
                self._lecture.correct(page, at)
            else:
                self._tracker.correct(at)
            self._highlight.reset()

    # 순서가 맞는 새 발화만 받고 인식 주기와 확정 발화를 관리한다.
    def update(self, text, is_final, utterance_id, sequence, recognition_cycle=None):
        with self._lock:
            if sequence <= self._sequence or utterance_id in self._finalized:
                return SearchResult()
            self._sequence = sequence
            if recognition_cycle is not None and recognition_cycle != self._recognition_cycle:
                self._recognition_cycle = recognition_cycle
                self._committed.clear()
                self._last_query = ()
                self._highlight.clear_pending()
                if self._lecture is not None:
                    self._lecture.reset_query()

            result = self._stream_update(text, is_final)
            if is_final:
                self._finalized[utterance_id] = True
                if len(self._finalized) > 256:
                    self._finalized.popitem(last=False)
            return result

    # 발화 문맥을 누적하고 정해진 간격으로 페이지와 블록을 검색한다.
    def _stream_update(self, text, is_final):
        words = tuple(text.split()[-_CONTEXT_WORDS:])
        context = (*self._committed, *words)[-_CONTEXT_WORDS:]
        if is_final:
            self._committed.extend(words)
        at = _clock_ms()
        if len(words) < 3 or len(context) < 5 or context == self._last_query or at < self._next_sample:
            return SearchResult()
        self._last_query = context
        self._next_sample = at + _SAMPLE_MS
        short, long = ' '.join(context[-_SHORT_WORDS:]), ' '.join(context)
        multiple_pages = self.page_count > 1
        query = [short, long] if self._lecture is not None and multiple_pages and short != long else [short]
        vectors = _embed(query)
        vector = vectors[0]
        page, lecture = None, None
        if multiple_pages or self._lecture is not None:
            coverage = .65 * self._index.coverage(short) + .35 * self._index.coverage(long)
            base = self._page_scores(vector, coverage) if multiple_pages else None
        if self._lecture is not None:
            page, weights, memory = self._lecture.observe(
                self.current_page, short, long, vector, vectors[-1], base, coverage, at,
            )
            lecture = weights, memory
        elif multiple_pages:
            page = self._tracker.observe(self.current_page, base, at)
        if page is not None:
            self.current_page = page
            self._highlight.reset()
        return SearchResult(page, self._match_block(short, vector, at, lecture=lecture))

    # 페이지별 임베딩 유사도와 용어 포함 점수를 합친다.
    def _page_scores(self, vector, coverage):
        body = np.maximum.reduceat(self._page_vectors @ vector, self._passage_starts)
        return normalize(body.astype(float) + .10 * coverage, floor=.034)

    # 같은 페이지의 다른 블록과 겹치지 않는 명사를 찾는다.
    def _distinctive_block_terms(self, blocks):
        terms = [{term for term in lexical_terms(block.text) if len(term) >= 2} for block in blocks]
        for indices in self._block_indices.values():
            counts = Counter(term for index in indices for term in terms[index])
            for index in indices:
                terms[index] = {term for term in terms[index] if counts[term] == 1}
        return terms

    # 현재 페이지의 블록을 점수화하고 강조기가 고른 블록 ID를 반환한다.
    def _match_block(self, short, vector, at, *, lecture=None):
        indices = self._block_indices.get(self.current_page)
        if not indices:
            return None
        scores = self._block_vectors[indices] @ vector
        recent = set(lexical_terms(' '.join(short.split()[-4:])))
        exact = np.array([len(recent & self._block_terms[index]) for index in indices])
        scores = scores + _BLOCK_TERM_BONUS * np.minimum(exact, 2) * (scores >= _BLOCK_TERM_BONUS_FLOOR)
        minimum = .45
        if lecture is not None:
            weights, memory = lecture
            scores = self._lecture.block_scores(self.current_page, scores, weights, memory)
            minimum = -np.inf
        best = self._highlight.observe(scores, minimum, at)
        if best is not None and lecture is not None:
            self._lecture.highlighted(self.current_page, best)
        return self._block_ids[indices[best]] if best is not None else None
