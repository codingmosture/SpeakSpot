"""Sliding Queue 검색 알고리즘.

- 최근 N개 단어만 점수에 반영 (시간에 따른 누적 점수 없음)
- 현재 페이지 ±R 범위의 PDF 블록만 탐색
- 큐의 원문 단어는 보존, 비교 시 한국어 형태소·SQL 발음 표기를 정규화
- 최신 발화를 우선하고 페이지 문맥과 블록 일치를 함께 비교
- 현재 페이지 유지 보너스 / 과거 페이지 감점 / 최소 점수로 오이동 억제
- 찾지 못했으면 NONE (현재 페이지는 그대로)
"""
from collections import Counter, deque
from math import log, sqrt
import re
from typing import Sequence

from app.schemas.models import Block
from app.search.algorithms.base import AlgorithmResult, SearchAlgorithm
from app.search.config import SearchSettings
from app.search.lexical import lexical_terms
from app.search.tokenizer import matches, tokenize


_REFERENCE = re.compile(r'후반부|나중에|이따가|앞에서|앞서|지난\s*시간|다음\s*시간')
_GENERIC_TRANSITIONS = frozenset({'값', '표현', '여부'})


class SlidingQueueSearch(SearchAlgorithm):
    name = 'sliding_queue'

    def __init__(
        self, blocks: Sequence[Block], settings: SearchSettings, model=None
    ) -> None:
        if model is not None:
            raise ValueError('sliding_queue는 AI 모델을 사용하지 않습니다. model=None으로 지정하세요.')
        super().__init__(blocks, settings, model)
        self._queue: deque[str] = deque(maxlen=settings.sliding_window_words)
        # 브라우저 STT의 현재 미확정 단어는 매 갱신마다 교체한다.
        self._live_words: tuple[str, ...] = ()
        self._stream_anchor: int | None = None
        self._block_tokens: dict[str, tuple[str, ...]] = {}
        self._page_tokens: dict[int, tuple[str, ...]] = {}
        self._page_pairs: dict[int, set[tuple[str, str]]] = {}
        self._idf: dict[str, float] = {}
        self._build_index()

    @property
    def buffer_words(self) -> tuple[str, ...]:
        # 실제 탐색에 사용 중인 '확정 + 미확정' 최근 N단어를 그대로 표시한다.
        return tuple(deque((*self._queue, *self._live_words), maxlen=self._queue.maxlen))

    def _build_index(self) -> None:
        counts: Counter[str] = Counter()
        pages: dict[int, list[str]] = {}
        for block in self.blocks:
            if block.block_id in self._block_tokens:
                raise ValueError(f'중복 block_id: {block.block_id}')
            text_words = lexical_terms(block.text)
            words = text_words + tuple(
                term for keyword in block.keywords for term in lexical_terms(keyword)
            )
            self._block_tokens[block.block_id] = words
            pages.setdefault(block.page, []).extend(words)
            self._page_pairs.setdefault(block.page, set()).update(zip(text_words, text_words[1:]))
            counts.update(set(words))
        total = len(self.blocks)
        self._idf = {word: log((total + 1) / (count + 1)) + 1.0 for word, count in counts.items()}
        self._page_tokens = {page: tuple(words) for page, words in pages.items()}

    def _term_idf(self, word: str) -> float:
        # 일치하는 문서 토큰의 최대 IDF를 사용한다.
        if word in self._idf:
            return self._idf[word]
        return max((weight for term, weight in self._idf.items() if matches(word, term)), default=1.0)

    def _similarity(self, query_words: Counter[str], query_idf: dict[str, float], words: tuple[str, ...]) -> float:
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

    def _candidate_score(self, similarity: float, page: int, anchor: int) -> float:
        # Adjacent slides are normal lecture progress; a more distant jump
        # needs stronger evidence than a passing reference to a shared keyword.
        result = similarity - min(0.24, max(0, abs(page - anchor) - 1) * 0.06)
        if page == self.current_page:
            result += self.settings.stay_bonus
        elif page < self.current_page:
            distance = self.current_page - page
            result -= self.settings.backward_penalty * min(distance / 5.0, 1.0)
        return max(0.0, min(1.0, result))

    def search(self, speech_text: str) -> AlgorithmResult:
        # 수동 입력/확정 발화는 중간 가설을 덮어쓰고 한 번만 큐에 넣는다.
        self._live_words = ()
        self._stream_anchor = None
        words = tokenize(speech_text)
        self._queue.extend(words)
        return self._evaluate(words)

    def stream_update(self, speech_text: str, is_final: bool) -> AlgorithmResult:
        """매 STT 갱신에 큐를 재계산한다. 미확정 단어는 누적하지 않고 교체한다.

        '데이터' → '데이터베이스' → '데이터베이스 정규화'와 같은
        중간 인식 수정은 과거 가설이 아닌 최신 가설만 사용한다.
        미확정 상태에서도 current_page와 하이라이트 위치는 갱신한다.
        """
        words = tokenize(speech_text)
        if self._stream_anchor is None:
            self._stream_anchor = self.current_page
        anchor = self._stream_anchor
        if is_final:
            self._live_words = ()
            self._queue.extend(words)
        else:
            self._live_words = tuple(words)
        result = self._evaluate(words, anchor)
        if result.page is not None and abs(result.page - anchor) == 1:
            self._stream_anchor = result.page
        if is_final:
            self._stream_anchor = None
        return result

    def _evaluate(self, latest_words: Sequence[str], anchor: int | None = None) -> AlgorithmResult:
        anchor = self.current_page if anchor is None else anchor
        current_words = self.buffer_words
        if not current_words:
            return AlgorithmResult(status='NONE')

        query_words = Counter(lexical_terms(' '.join(current_words)))
        latest_terms = lexical_terms(' '.join(latest_words[-self.settings.sliding_window_words:]))
        if not latest_terms:
            return AlgorithmResult(status='NONE')
        query_idf = {word: self._term_idf(word) for word in query_words}
        fresh_query = Counter(latest_terms)
        fresh_idf = {word: self._term_idf(word) for word in fresh_query}
        radius = self.settings.local_radius_pages
        candidates: list[tuple[float, Block]] = []

        def similarity(words: tuple[str, ...]) -> float:
            # Give the current utterance priority while retaining the bounded
            # history for context. Otherwise a short topic change can never
            # outweigh the previous, longer sentence in the same 15-word queue.
            history = self._similarity(query_words, query_idf, words)
            fresh = self._similarity(fresh_query, fresh_idf, words)
            return (history + 2 * fresh) / 3

        page_scores = {page: similarity(words)
                       for page, words in self._page_tokens.items()
                       if abs(page - anchor) <= radius}
        anchor_words = self._page_tokens.get(anchor, ())
        novel = {term for term in latest_terms
                 if term not in _GENERIC_TRANSITIONS
                 and not any(matches(term, word) for word in anchor_words)}
        query_pairs = set(zip(latest_terms, latest_terms[1:]))
        reference = _REFERENCE.search(' '.join(latest_words[-self.settings.sliding_window_words:]))
        eligible_pages = {anchor}
        for page in page_scores:
            if page == anchor or reference:
                continue
            page_words = self._page_tokens[page]
            new_hits = {term for term in novel if any(matches(term, word) for word in page_words)}
            new_pairs = (query_pairs & self._page_pairs[page]) - self._page_pairs.get(anchor, set())
            focused = len(set(latest_terms)) == 1 and sum(
                any(matches(latest_terms[0], word) for word in self._page_tokens[other])
                for other in page_scores
            ) == 1
            if len(new_hits) >= 2 or (new_hits and new_pairs) or focused:
                eligible_pages.add(page)
        for block in self.blocks:
            if block.page not in eligible_pages:
                continue
            # History can disambiguate a new utterance, but cannot supply all
            # evidence for unrelated speech or an empty/filler hypothesis.
            if not any(matches(term, word) for term in latest_terms
                       for word in self._block_tokens[block.block_id]):
                continue
            raw = similarity(self._block_tokens[block.block_id])
            if raw == 0:
                continue
            # Speech can span a heading, explanation and figure on the same slide.
            # Page context helps these beat a coincidental single-block overlap.
            weighted = self._candidate_score(0.25 * raw + 0.75 * page_scores[block.page], block.page, anchor)
            candidates.append((weighted, block))

        # A distinctive distant match may be weaker in a long OCR figure, but
        # must still clearly beat every alternative page before moving there.
        page_best: dict[int, float] = {}
        for score, block in candidates:
            page_best[block.page] = max(page_best.get(block.page, 0), score)
        candidates = [(score, block) for score, block in candidates
                      if abs(block.page - anchor) <= 1 or score >= 0.65
                      or score >= max((value for page, value in page_best.items()
                                      if page != block.page), default=0) + max(0.12, self.settings.switch_margin)]
        if not candidates:
            return AlgorithmResult(status='NONE')
        current_page_candidates = [(score, block) for score, block in candidates
                                   if block.page == self.current_page]
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
        committed_live = self._live_words
        committed_anchor = self._stream_anchor
        self._queue = deque(committed_queue, maxlen=committed_queue.maxlen)
        try:
            return self.search(speech_text)
        finally:
            self._queue = committed_queue
            self.current_page = committed_page
            self._live_words = committed_live
            self._stream_anchor = committed_anchor

    def reset(self) -> None:
        self._queue.clear()
        self._live_words = ()
        self._stream_anchor = None
        self.current_page = 1
