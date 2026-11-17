"""Sliding Queue 검색 알고리즘.

- 최근 N개 단어만 점수에 반영; 추가/삭제/반복 횟수 변화만 집계 갱신
- 현재 페이지 ±R 범위의 PDF 블록만 탐색
- 큐와 비교에는 일반명사·고유명사·영문 용어만 사용
- E5 선택 시 개별 단어 유사도로 일치 판정을 보완 (문장 랭킹은 사용하지 않음)
- 최신 발화를 우선하고 페이지 문맥과 블록 일치를 함께 비교
- 현재 페이지 유지 보너스 / 과거 페이지 감점 / 최소 점수로 오이동 억제
- 찾지 못했으면 NONE (현재 페이지는 그대로)
"""
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass
from math import log, sqrt
import re
from typing import Sequence

import numpy as np

from app.schemas.models import Block
from app.search.algorithms.base import AlgorithmResult, SearchAlgorithm
from app.search.config import SearchSettings
from app.search.lexical import lexical_terms
from app.search.tokenizer import matches, tokenize


_REFERENCE = re.compile(r'후반부|나중에|이따가|앞에서|앞서|지난\s*시간|다음\s*시간')
_GENERIC_TRANSITIONS = frozenset({'값', '표현', '여부'})


@dataclass(frozen=True, slots=True)
class _TermMatch:
    words: frozenset[str]
    idf: float
    hits: np.ndarray
    prefixes: np.ndarray


@dataclass(slots=True)
class _QueryScores:
    counts: Counter[str]
    totals: np.ndarray  # Per target: matched weight, distinct hits, prefix hits.
    weight: float = 0.0

    def copy(self):
        return _QueryScores(self.counts.copy(), self.totals.copy(), self.weight)


class SlidingQueueSearch(SearchAlgorithm):
    name = 'sliding_queue'

    def __init__(
        self, blocks: Sequence[Block], settings: SearchSettings, model=None
    ) -> None:
        if model is not None and (model.name != 'e5-small' or model.kind != 'embedding'):
            raise ValueError('sliding_queue는 e5-small 단어 비교만 지원합니다.')
        super().__init__(blocks, settings, model)
        self._queue: deque[str] = deque(maxlen=settings.sliding_window_words)
        # 브라우저 STT의 현재 미확정 단어는 매 갱신마다 교체한다.
        self._live_words: tuple[str, ...] = ()
        self._stream_anchor: int | None = None
        self._stream_utterance: str | None = None
        self._pending_page: int | None = None
        self._pending_support: frozenset[str] = frozenset()
        self._pending_terms: tuple[str, ...] = ()
        self._pending_words: tuple[str, ...] = ()
        self._pending_score = 0.0
        self._block_tokens: dict[str, tuple[str, ...]] = {}
        self._page_tokens: dict[int, tuple[str, ...]] = {}
        self._page_pairs: dict[int, set[tuple[str, str]]] = {}
        self._idf: dict[str, float] = {}
        self._term_matches: OrderedDict[str, _TermMatch] = OrderedDict()
        self._build_index()
        targets = {**self._block_tokens, **self._page_tokens}
        self._target_index = {key: i for i, key in enumerate(targets)}
        self._target_terms = [(set(words), set(words[:max(1, len(words) // 3)]))
                              for words in targets.values()]
        self._target_sizes = [max(1.0, sqrt(len(words))) for words, _ in self._target_terms]
        self._history_scores = _QueryScores(Counter(), np.zeros((len(targets), 3)))
        self._latest_scores = self._history_scores.copy()

    @property
    def document_terms(self) -> tuple[str, ...]:
        return tuple(self._idf)

    def _matches(self, query: str, word: str) -> bool:
        cached = self._term_matches.get(query)
        return word in cached.words if cached is not None else matches(query, word)

    @property
    def buffer_words(self) -> tuple[str, ...]:
        # 실제 탐색에 사용 중인 '확정 + 미확정' 최근 N개 명사를 표시한다.
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

    def _prepare_matches(self, terms: Sequence[str]) -> None:
        missing = [term for term in terms if term not in self._term_matches]
        semantic = (self.model.match_terms(missing, self.settings.word_match_threshold)
                    if missing and self.model is not None else {})
        for term in missing:
            words = frozenset(word for word in self._idf
                              if matches(term, word) or word in semantic.get(term, ()))
            idf = self._idf.get(term, max((self._idf[word] for word in words), default=1.0))
            hits, prefixes = [], []
            for index, (all_words, prefix_words) in enumerate(self._target_terms):
                if words & all_words:
                    hits.append(index)
                    if words & prefix_words:
                        prefixes.append(index)
            self._term_matches[term] = _TermMatch(words, idf, np.array(hits, dtype=np.intp),
                                                 np.array(prefixes, dtype=np.intp))
        for term in terms:
            self._term_matches.move_to_end(term)

    def _update_scores(self, state: _QueryScores, counts: Counter[str]) -> None:
        for term in dict.fromkeys((*state.counts, *counts)):
            previous, current = state.counts[term], counts[term]
            if previous == current:
                continue
            evidence = self._term_matches[term]
            def weight(count):
                return evidence.idf * (1 + .15 * min(count - 1, 2)) if count else 0.0
            delta = weight(current) - weight(previous)
            present = int(current > 0) - int(previous > 0)
            state.weight += delta
            state.totals[evidence.hits, 0] += delta
            state.totals[evidence.hits, 1] += present
            state.totals[evidence.prefixes, 2] += present
        state.counts = counts
        # Clear round-off after removals; no expired contribution survives.
        state.totals[state.totals[:, 1] == 0] = 0
        if not counts:
            state.weight = 0.0

    def _sync_scores(self, history: Counter[str], latest: Counter[str]) -> None:
        active = tuple(dict.fromkeys((*history, *latest)))
        self._prepare_matches(active)
        self._update_scores(self._history_scores, history)
        self._update_scores(self._latest_scores, latest)
        # Bound memoization but never evict evidence still contributing to a queue.
        limit = max(512, len(active))
        active = set(active)
        for term in tuple(self._term_matches):
            if len(self._term_matches) <= limit:
                break
            if term not in active:
                del self._term_matches[term]

    def _similarity(self, state: _QueryScores, index: int) -> float:
        matched, hits, prefix_hits = state.totals[index]
        if state.weight <= 0 or hits == 0:
            return 0.0
        coverage = matched / state.weight
        specificity = min(1.0, hits / self._target_sizes[index])
        return min(1.0, 0.86 * coverage + 0.14 * specificity
                   + self.settings.prefix_bonus * prefix_hits / hits)

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
        self._stream_utterance = None
        self._clear_pending()
        words = lexical_terms(speech_text)
        self._queue.extend(words)
        return self._evaluate(words, source_text=speech_text)

    def stream_update(self, speech_text: str, is_final: bool,
                      utterance_id: str | None = None) -> AlgorithmResult:
        """매 STT 갱신에 큐를 재계산한다. 미확정 단어는 누적하지 않고 교체한다.

        '데이터' → '데이터베이스' → '데이터베이스 정규화'와 같은
        중간 인식 수정은 과거 가설이 아닌 최신 가설만 사용한다.
        미확정 상태에서도 current_page와 하이라이트 위치는 갱신한다.
        """
        words = lexical_terms(speech_text)
        if utterance_id is not None and utterance_id != self._stream_utterance:
            self._stream_anchor = None
            self._clear_pending()
            self._stream_utterance = utterance_id
        if self._stream_anchor is None:
            self._stream_anchor = self.current_page
        anchor = self._stream_anchor
        if is_final:
            self._live_words = ()
            self._queue.extend(words)
        else:
            self._live_words = tuple(words)
        result = self._confirm_transition(
            self._evaluate(words, anchor, commit=False, source_text=speech_text), words, is_final)
        if result.page is not None:
            self.current_page = result.page
        if result.page is not None and abs(result.page - anchor) == 1:
            self._stream_anchor = result.page
        if is_final:
            self._stream_anchor = None
            self._stream_utterance = None
            self._clear_pending()
        return result

    def _clear_pending(self) -> None:
        self._pending_page = None
        self._pending_support = frozenset()
        self._pending_terms = ()
        self._pending_words = ()
        self._pending_score = 0.0

    def _confirm_transition(self, result: AlgorithmResult, words: Sequence[str],
                            is_final: bool) -> AlgorithmResult:
        if result.page is None or result.page == self.current_page:
            self._clear_pending()
            return result
        recent = words[-self.settings.sliding_window_words:]
        terms = tuple(recent)
        support = frozenset(term for term in terms if any(
            self._matches(term, word) for word in self._page_tokens[result.page]
        ))
        novel_support = {term for term in support if term not in _GENERIC_TRANSITIONS and not any(
            self._matches(term, word) for word in self._page_tokens.get(self.current_page, ())
        )}
        content_words = recent
        # A genuinely unique keyword can still follow immediately (e.g. DISTINCT).
        # Common short nouns and discourse words cannot move the viewer alone.
        focused = (len(content_words) == 1 and len(content_words[0]) >= 3
                   and (result.score or 0) >= 0.8 and bool(support)
                   and sum(all(any(self._matches(term, word) for word in page_words)
                               for term in terms) for page_words in self._page_tokens.values()) == 1)
        # The rolling word window may drop its prefix as speech grows. Require
        # overlapping consecutive speech, not repeated or unrelated hypotheses.
        continued = any(self._pending_words[-size:] == tuple(recent[:size])
                        for size in range(min(2, len(self._pending_words)),
                                          min(len(self._pending_words), len(recent)) + 1)
                        if size > 0)
        growing = (self._pending_page == result.page and continued
                   and len(content_words) >= 2 and len(support) >= 2
                   and bool(support & self._pending_support) and terms != self._pending_terms
                   and (len(novel_support) >= 2 or max(self._pending_score, result.score or 0) >= 0.65))
        confirmed = is_final and len(content_words) >= 2 and len(support) >= 2
        if focused or growing or confirmed:
            self._clear_pending()
            return result
        previous_score = self._pending_score if self._pending_page == result.page else 0
        self._pending_page, self._pending_support = result.page, support
        self._pending_score = max(previous_score, result.score or 0)
        self._pending_terms = terms
        self._pending_words = tuple(recent)
        return AlgorithmResult(status='NONE')

    def _evaluate(self, latest_words: Sequence[str], anchor: int | None = None,
                  *, commit: bool = True, source_text: str = '') -> AlgorithmResult:
        anchor = self.current_page if anchor is None else anchor
        current_words = self.buffer_words
        query_words = Counter(current_words)
        latest_terms = tuple(latest_words[-self.settings.sliding_window_words:])
        self._sync_scores(query_words, Counter(latest_terms))
        if not current_words or not latest_terms:
            return AlgorithmResult(status='NONE')
        radius = self.settings.local_radius_pages
        candidates: list[tuple[float, Block]] = []

        def similarity(target: str | int) -> float:
            # Give the current utterance priority while retaining the bounded
            # history for context. Otherwise a short topic change can never
            # outweigh the previous, longer sentence in the same 15-word queue.
            index = self._target_index[target]
            history = self._similarity(self._history_scores, index)
            fresh = self._similarity(self._latest_scores, index)
            return (history + 2 * fresh) / 3

        page_scores = {page: similarity(page)
                       for page, words in self._page_tokens.items()
                       if abs(page - anchor) <= radius}
        anchor_words = self._page_tokens.get(anchor, ())
        novel = {term for term in latest_terms
                 if term not in _GENERIC_TRANSITIONS
                 and not any(self._matches(term, word) for word in anchor_words)}
        query_pairs = set(zip(latest_terms, latest_terms[1:]))
        # Inspect original speech for references such as 앞에서/나중에, whose
        # particles are intentionally absent from the noun queue.
        reference = _REFERENCE.search(' '.join(tokenize(source_text)[-self.settings.sliding_window_words:]))
        eligible_pages = {anchor}
        for page in page_scores:
            if page == anchor or reference:
                continue
            page_words = self._page_tokens[page]
            new_hits = {term for term in novel if any(self._matches(term, word) for word in page_words)}
            new_pairs = (query_pairs & self._page_pairs[page]) - self._page_pairs.get(anchor, set())
            focused = len(set(latest_terms)) == 1 and sum(
                any(self._matches(latest_terms[0], word) for word in self._page_tokens[other])
                for other in page_scores
            ) == 1
            if len(new_hits) >= 2 or (new_hits and new_pairs) or focused:
                eligible_pages.add(page)
        for block in self.blocks:
            if block.page not in eligible_pages:
                continue
            # History can disambiguate a new utterance, but cannot supply all
            # evidence for unrelated speech or an empty/filler hypothesis.
            if not any(self._matches(term, word) for term in latest_terms
                       for word in self._block_tokens[block.block_id]):
                continue
            raw = similarity(block.block_id)
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

        if commit:
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
        committed_matches = self._term_matches.copy()
        committed_history = self._history_scores.copy()
        committed_latest = self._latest_scores.copy()
        committed_queue = self._queue
        committed_page = self.current_page
        committed_live = self._live_words
        committed_anchor = self._stream_anchor
        committed_utterance = self._stream_utterance
        committed_pending = (self._pending_page, self._pending_support, self._pending_terms,
                             self._pending_words, self._pending_score)
        self._queue = deque(committed_queue, maxlen=committed_queue.maxlen)
        try:
            return self.search(speech_text)
        finally:
            self._term_matches = committed_matches
            self._history_scores = committed_history
            self._latest_scores = committed_latest
            self._queue = committed_queue
            self.current_page = committed_page
            self._live_words = committed_live
            self._stream_anchor = committed_anchor
            self._stream_utterance = committed_utterance
            (self._pending_page, self._pending_support, self._pending_terms,
             self._pending_words, self._pending_score) = committed_pending

    def reset(self) -> None:
        self._term_matches.clear()
        self._history_scores = _QueryScores(Counter(), np.zeros((len(self._target_index), 3)))
        self._latest_scores = self._history_scores.copy()
        self._queue.clear()
        self._live_words = ()
        self._stream_anchor = None
        self._stream_utterance = None
        self._clear_pending()
        self.current_page = 1
