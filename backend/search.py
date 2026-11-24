"""Page tracking and block highlights from streaming speech."""
from collections import Counter, OrderedDict, defaultdict, deque
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from time import monotonic_ns

import numpy as np

from page_ranking import PageIndex, lexical_terms, normalize
from page_tracker import PageTracker

_MODEL = 'BAAI/bge-m3'
_ENCODER_LOCK = Lock()
_SHORT_WORDS = 11
_CONTEXT_WORDS = 28
_SAMPLE_MS = 1500
_BLOCK_SWITCH_LEAD = .05
_BLOCK_RETURN_MS = 6000
_BLOCK_EVIDENCE_GAP_MS = 7000
_BLOCK_TERM_BONUS = .09
_BLOCK_TERM_BONUS_FLOOR = .42


@dataclass
class Block:
    block_id: str
    page: int
    text: str
    rects: list


@dataclass(frozen=True, slots=True)
class SearchResult:
    page: int | None = None
    block_id: str | None = None


class BlockHighlight:
    def __init__(self):
        self.reset()

    def reset(self):
        self._active = None
        self._previous = None
        self._changed_at = None
        self._last_at = None
        self.clear_pending()

    def clear_pending(self):
        self._pending = None

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
            if returning or scores[best] - scores[self._active] < _BLOCK_SWITCH_LEAD:
                self._pending = best
                return None

        self._previous, self._active = self._active, best
        self._changed_at = at
        self.clear_pending()
        return best


@lru_cache(maxsize=1)
def page_encoder(*, local_files_only=True):
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(min(torch.get_num_threads(), 4))
    return SentenceTransformer(_MODEL, device='cpu', local_files_only=local_files_only)


def _embed(texts):
    with _ENCODER_LOCK:
        return page_encoder().encode(
            texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False,
        )


def _clock_ms():
    return monotonic_ns() // 1_000_000


class PassageSearch:
    def __init__(self, blocks, page_count):
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
        self._tracker = PageTracker(self._index.pages)
        self._highlight = BlockHighlight()

        if page_count > 1:
            passages, self._passage_starts = self._index.passages()
            self._page_vectors = _embed(passages)
        self._block_vectors = _embed([' '.join(block.text.split()[:72]) for block in blocks])
        self._block_terms = self._distinctive_block_terms(blocks)

    def correct_page(self, page):
        with self._lock:
            if not 1 <= page <= self.page_count:
                raise ValueError('문서 범위 안의 페이지를 선택해주세요.')
            self.current_page = page
            self._tracker.correct(_clock_ms())
            self._highlight.reset()

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

            result = self._stream_update(text, is_final)
            if is_final:
                self._finalized[utterance_id] = True
                if len(self._finalized) > 256:
                    self._finalized.popitem(last=False)
            return result

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
        vector = _embed([short])[0]

        page = None
        if self.page_count > 1:
            page = self._tracker.observe(self.current_page, self._page_scores(short, long, vector), at)
            if page is not None:
                self.current_page = page
                self._highlight.reset()
        return SearchResult(page, self._match_block(short, vector, at))

    def _page_scores(self, short, long, vector):
        body = np.maximum.reduceat(self._page_vectors @ vector, self._passage_starts)
        coverage = .65 * self._index.coverage(short) + .35 * self._index.coverage(long)
        return normalize(body.astype(float) + .10 * coverage, floor=.034)

    def _distinctive_block_terms(self, blocks):
        terms = [{term for term in lexical_terms(block.text) if len(term) >= 2} for block in blocks]
        for indices in self._block_indices.values():
            counts = Counter(term for index in indices for term in terms[index])
            for index in indices:
                terms[index] = {term for term in terms[index] if counts[term] == 1}
        return terms

    def _match_block(self, short, vector, at):
        indices = self._block_indices.get(self.current_page)
        if not indices:
            return None
        scores = self._block_vectors[indices] @ vector
        recent = set(lexical_terms(' '.join(short.split()[-7:])))
        exact = np.array([len(recent & self._block_terms[index]) for index in indices])
        scores = scores + _BLOCK_TERM_BONUS * np.minimum(exact, 2) * (scores >= _BLOCK_TERM_BONUS_FLOOR)
        best = self._highlight.observe(scores, .45, at)
        return self._block_ids[indices[best]] if best is not None else None
