"""Page tracking and block highlights from streaming speech."""
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock
from time import monotonic_ns

import numpy as np
from huggingface_hub import snapshot_download
from huggingface_hub.errors import LocalEntryNotFoundError

from page_ranking import PageIndex, character_ngrams, normalize
from page_tracker import PageTracker

_MODEL = 'BAAI/bge-m3'
_ENCODER_LOCK = Lock()
_SHORT_WORDS = 11
_CONTEXT_WORDS = 28
_SAMPLE_MS = 1500


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


def create_search(blocks, page_count):
    try:
        snapshot = Path(snapshot_download(_MODEL, local_files_only=True))
        semantic = any((snapshot / name).is_file() for name in ('model.safetensors', 'pytorch_model.bin'))
    except LocalEntryNotFoundError:
        semantic = False
    return PassageSearch(blocks, page_count, semantic=semantic)


class PassageSearch:
    def __init__(self, blocks, page_count, *, semantic):
        self.page_count = page_count
        self.current_page = 1
        self._lock = Lock()
        self._sequence = -1
        self._finalized = OrderedDict()
        self._recognition_cycle = None
        self._committed = deque(maxlen=_CONTEXT_WORDS)
        self._last_query = ()
        self._next_sample = 0
        self._semantic = semantic

        page_text = defaultdict(list)
        self._block_indices = defaultdict(list)
        self._block_ids = []
        for index, block in enumerate(blocks):
            page_text[block.page].append(block.text)
            self._block_indices[block.page].append(index)
            self._block_ids.append(block.block_id)
        self._index = PageIndex(page_text)
        self._tracker = PageTracker(self._index.pages)

        if semantic:
            if page_count > 1:
                passages, self._passage_starts = self._index.passages()
                self._page_vectors = _embed(passages)
            self._block_vectors = _embed([' '.join(block.text.split()[:72]) for block in blocks])
        else:
            self._block_grams = [set(character_ngrams(block.text)) for block in blocks]

    def correct_page(self, page):
        with self._lock:
            if not 1 <= page <= self.page_count:
                raise ValueError('문서 범위 안의 페이지를 선택해주세요.')
            self.current_page = page
            self._tracker.correct(_clock_ms())

    def update(self, text, is_final, utterance_id, sequence, recognition_cycle=None):
        with self._lock:
            if sequence <= self._sequence or utterance_id in self._finalized:
                return SearchResult()
            self._sequence = sequence
            if recognition_cycle is not None and recognition_cycle != self._recognition_cycle:
                self._recognition_cycle = recognition_cycle
                self._committed.clear()
                self._last_query = ()

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
        vector = _embed([short])[0] if self._semantic else None

        page = None
        if self.page_count > 1:
            page = self._tracker.observe(self.current_page, self._page_scores(short, long, vector), at)
            if page is not None:
                self.current_page = page
        return SearchResult(page, self._match_block(short, vector))

    def _page_scores(self, short, long, vector):
        if vector is not None:
            body = np.maximum.reduceat(self._page_vectors @ vector, self._passage_starts)
            coverage = .65 * self._index.coverage(short) + .35 * self._index.coverage(long)
            return normalize(body.astype(float) + .10 * coverage, floor=.034)
        return normalize(.65 * self._index.character(short) + .35 * self._index.character(long), floor=.08)

    def _match_block(self, short, vector):
        indices = self._block_indices.get(self.current_page)
        if not indices:
            return None
        if vector is not None:
            scores = self._block_vectors[indices] @ vector
            minimum = .45
        else:
            grams = set(character_ngrams(short))
            scores = [len(grams & self._block_grams[index]) for index in indices]
            minimum = max(2, .12 * len(grams))
        best = int(np.argmax(scores))
        return self._block_ids[indices[best]] if scores[best] >= minimum else None
