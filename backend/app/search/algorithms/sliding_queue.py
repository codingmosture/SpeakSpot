"""Sliding Queue 검색 알고리즘.

- 최근 N개 단어만 점수에 반영 (시간에 따른 누적 점수 없음)
- 현재 페이지 ±R 범위의 PDF 블록만 탐색
- 한국어 단어 원형 보존, 접미사·조사 형태만 보수적으로 매칭
- 현재 페이지 유지 보너스 / 과거 페이지 감점 / 최소 점수로 오이동 억제
- 찾지 못했으면 NONE (현재 페이지는 그대로)
"""
from collections import Counter, deque
from math import log, sqrt
from typing import Sequence

from app.schemas.models import Block
from app.search.algorithms.base import AlgorithmResult, SearchAlgorithm
from app.search.config import SearchSettings
from app.search.tokenizer import matches, tokenize


class SlidingQueueSearch(SearchAlgorithm):
    name = 'sliding_queue'

    def __init__(
        self, blocks: Sequence[Block], settings: SearchSettings, model=None
    ) -> None:
        if model is not None:
            raise ValueError('sliding_queue는 AI 모델을 사용하지 않습니다. model=None으로 지정하세요.')
        super().__init__(blocks, settings, model)
        self._queue: deque[str] = deque(maxlen=settings.sliding_window_words)
        self._block_tokens: dict[str, tuple[str, ...]] = {}
        self._idf: dict[str, float] = {}
        self._build_index()

    @property
    def buffer_words(self) -> tuple[str, ...]:
        return tuple(self._queue)

    def _build_index(self) -> None:
        counts: Counter[str] = Counter()
        for block in self.blocks:
            if block.block_id in self._block_tokens:
                raise ValueError(f'중복 block_id: {block.block_id}')
            words = tuple(tokenize(block.text) + [term for keyword in block.keywords for term in tokenize(keyword)])
            self._block_tokens[block.block_id] = words
            counts.update(set(words))
        total = len(self.blocks)
        self._idf = {word: log((total + 1) / (count + 1)) + 1.0 for word, count in counts.items()}

    def _term_idf(self, word: str) -> float:
        # 일치하는 문서 토큰의 최대 IDF를 사용한다.
        return max((weight for term, weight in self._idf.items() if matches(word, term)), default=1.0)

    def _similarity(self, query_words: Counter[str], query_idf: dict[str, float], block: Block) -> float:
        words = self._block_tokens[block.block_id]
        if not words:
            return 0.0
        unique_doc = set(words)
        total = 0.0
        matched = 0.0
        hit_count = 0
        prefix_limit = max(1, len(words) // 3)
        prefix_hits = 0

        for query_word, times in query_words.items():
            # 같은 키워드가 여러 번 등장해도 가중치를 제한한다.
            weight = query_idf[query_word] * (1.0 + 0.15 * min(times - 1, 2))
            total += weight
            if any(matches(query_word, doc_word) for doc_word in unique_doc):
                matched += weight
                hit_count += 1
                if any(matches(query_word, w) for w in words[:prefix_limit]):
                    prefix_hits += 1
        if total == 0 or hit_count == 0:
            return 0.0

        coverage = matched / total
        # 짧은 PDF 블록이 한 단어만 일치했다고 무조건 1위가 되는 것을 완화
        specificity = min(1.0, hit_count / max(1.0, sqrt(len(unique_doc))))
        base = 0.86 * coverage + 0.14 * specificity
        prefix_ratio = prefix_hits / hit_count
        return min(1.0, base + self.settings.prefix_bonus * prefix_ratio)

    def _candidate_score(self, similarity: float, page: int) -> float:
        result = similarity
        if page == self.current_page:
            result += self.settings.stay_bonus
        elif page < self.current_page:
            distance = self.current_page - page
            result -= self.settings.backward_penalty * min(distance / 5.0, 1.0)
        return max(0.0, min(1.0, result))

    def search(self, speech_text: str) -> AlgorithmResult:
        self._queue.extend(tokenize(speech_text))
        if not self._queue:
            return AlgorithmResult(status='NONE')

        query_words = Counter(self._queue)
        query_idf = {word: self._term_idf(word) for word in query_words}
        radius = self.settings.local_radius_pages
        candidates: list[tuple[float, Block]] = []
        current_page_candidates: list[tuple[float, Block]] = []
        for block in self.blocks:
            if abs(block.page - self.current_page) > radius:
                continue
            raw = self._similarity(query_words, query_idf, block)
            if raw == 0:
                continue
            weighted = self._candidate_score(raw, block.page)
            candidates.append((weighted, block))
            if block.page == self.current_page:
                current_page_candidates.append((weighted, block))

        if not candidates:
            return AlgorithmResult(status='NONE')
        # 안정적인 tie-break: 같으면 현재 페이지, 다음으로 가까운 페이지, 첫 블록
        candidates.sort(key=lambda pair: (
            -pair[0],
            pair[1].page != self.current_page,
            abs(pair[1].page - self.current_page),
            pair[1].page,
            pair[1].block_id,
        ))
        best_score, best_block = candidates[0]
        if best_score < self.settings.min_match_score:
            return AlgorithmResult(status='NONE')

        # 옆 페이지가 조금 더 높다는 이유만으로 현재 페이지를 버리지 않는다.
        if best_block.page != self.current_page and current_page_candidates:
            stay_score, stay_block = max(current_page_candidates, key=lambda pair: pair[0])
            if (stay_score >= self.settings.min_match_score and
                    best_score < stay_score + self.settings.switch_margin):
                best_score, best_block = stay_score, stay_block

        self.current_page = best_block.page
        return AlgorithmResult(
            status='MATCH', page=best_block.page, block_id=best_block.block_id,
            score=round(best_score, 5), stage_used='sliding_queue',
        )

    def preview(self, speech_text: str) -> AlgorithmResult:
        """현재 15단어 큐에 미확정 발화를 잠시 반영해 탐색하되 저장하지 않는다.

        브라우저 STT는 같은 문장을 계속 수정해서 전송한다.
        미확정 전체 문장을 그대로 누적하면 단어가 중복되므로 큐/현재 페이지를 복사해
        임시 점수만 계산한 뒤 원래 상태로 복원한다. (SearchSession.lock 안에서 호출)
        """
        committed_queue = self._queue
        committed_page = self.current_page
        self._queue = deque(committed_queue, maxlen=committed_queue.maxlen)
        try:
            return self.search(speech_text)
        finally:
            self._queue = committed_queue
            self.current_page = committed_page

    def reset(self) -> None:
        self._queue.clear()
        self.current_page = 1
