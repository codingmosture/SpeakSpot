"""Fixed eight-noun sliding queue with E5 word matching and incremental scores."""
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field
from functools import lru_cache
from math import log, sqrt
import re
from threading import Lock, RLock
from typing import Sequence
import numpy as np
from kiwipiepy import Kiwi, Match

_lock = Lock()
_HELPERS = frozenset({'하','되','있','없','보','이','것','수','등','다음','그다음','이번','그때','이거','그거','거','얘','여기','저기'})
_IDENTIFIER = re.compile(r'[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*')
_REFERENCE = re.compile(r'후반부|나중에|이따가|앞에서|앞서|지난\s*시간|다음\s*시간')
_GENERIC_TRANSITIONS = frozenset({'값','표현','여부'})

@lru_cache(maxsize=1)
def kiwi():
    return Kiwi(num_workers=1)

@lru_cache(maxsize=8192)
def lexical_terms(text):
    text = text.casefold()
    with _lock:
        tokens = kiwi().tokenize(text, match_options=Match.ALL | Match.JOIN_NOUN_PREFIX | Match.JOIN_NOUN_SUFFIX)
    terms = {t.start:t.form.casefold() for t in tokens if t.tag in {'NNG','NNP'} and t.form not in _HELPERS}
    starts = {t.start for t in tokens if t.tag == 'SL'}
    terms.update((m.start(),m.group()) for m in _IDENTIFIER.finditer(text) if m.start() in starts)
    return tuple(word for _,word in sorted(terms.items()))

@lru_cache(maxsize=1)
def encoder():
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError
    from sentence_transformers import SentenceTransformer
    model = 'intfloat/multilingual-e5-small'
    try:
        model = snapshot_download(model, local_files_only=True)
    except LocalEntryNotFoundError:
        pass
    return SentenceTransformer(model)

def encode(words):
    with _lock:
        return encoder().encode([f'query: {word}' for word in words], batch_size=32,
            normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True)

@dataclass
class Block:
    block_id: str
    page: int
    text: str
    rects: list
    keywords: list = field(default_factory=list)

@dataclass(frozen=True, slots=True)
class AlgorithmResult:
    status: str = 'NONE'
    page: int | None = None
    block_id: str | None = None
    score: float | None = None

_TOKEN_PATTERN = re.compile('[가-힣]+|[a-zA-Z0-9]+(?:[-_][a-zA-Z0-9]+)*')

_STOPWORDS = frozenset({'그', '이', '저', '그런데', '그리고', '그러면', '이제', '여기', '저기', '제가', '우리가', '하는', '것', '거', '이거', '그거', '이렇게', '저렇게', '정말', '좀', '한번', '다음', '같은', '경우', '입니다', '있습니다'})

_SUFFIXES = frozenset({'은', '는', '이', '가', '을', '를', '에', '의', '와', '과', '도', '만', '로', '으로', '에서', '에게', '부터', '까지', '에는', '에서는', '하고', '이고', '인데', '이다', '입니다', '이라고', '라는', '같은', '처럼', '이라면', '한다', '합니다', '하는', '했다', '했습니다', '하지', '하면', '해서', '된다', '됩니다', '되는', '되면', '됐습니다', '됩니다만'})

def tokenize(text: str) -> list[str]:
    """대소문자 통일, 한국어 원형 보존. 형태소를 임의로 절단하지 않는다."""
    tokens = _TOKEN_PATTERN.findall(text.casefold())
    return [word for word in tokens if word not in _STOPWORDS]

def matches(query_word: str, document_word: str) -> bool:
    """동일 단어 또는 제한된 한국어 접미사 차이를 확인한다."""
    if query_word == document_word:
        return True
    if len(query_word) < 2 or len(document_word) < 2:
        return False
    longer, shorter = (query_word, document_word) if len(query_word) >= len(document_word) else (document_word, query_word)
    return longer.startswith(shorter) and longer[len(shorter):] in _SUFFIXES

@dataclass(frozen=True, slots=True)
class _TermMatch:
    words: frozenset[str]
    idf: float
    hits: np.ndarray
    prefixes: np.ndarray

@dataclass(slots=True)
class _QueryScores:
    counts: Counter[str]
    totals: np.ndarray
    weight: float = 0.0

class Search:
    def __init__(self, blocks):
        self.blocks = tuple(blocks)
        self.current_page = 1
        self.lock = RLock()
        self.sequence = -1
        self.finalized = OrderedDict()
        self._queue = deque(maxlen=8)
        self._live_words = ()
        self._stream_anchor = self._stream_utterance = None
        self._clear_pending()
        self._block_tokens, self._page_tokens, self._page_pairs, self._idf = {}, {}, {}, {}
        self._term_matches = OrderedDict()
        self._build_index()
        targets = {**self._block_tokens, **self._page_tokens}
        self._target_index = {key:i for i,key in enumerate(targets)}
        self._target_terms = [(set(words),set(words[:max(1,len(words)//3)])) for words in targets.values()]
        self._target_sizes = [max(1.,sqrt(len(words))) for words,_ in self._target_terms]
        self._history_scores = _QueryScores(Counter(),np.zeros((len(targets),3)))
        self._latest_scores = _QueryScores(Counter(),np.zeros((len(targets),3)))
        self._vocabulary = tuple(self._idf)
        if not self._vocabulary:
            raise ValueError('문서에서 비교할 명사를 찾지 못했습니다.')
        self._word_index = {word:i for i,word in enumerate(self._vocabulary)}
        self._vectors = encode(self._vocabulary)

    def _semantic(self, terms):
        missing = [term for term in terms if term not in self._word_index]
        fresh = dict(zip(missing,encode(missing))) if missing else {}
        result = {}
        for term in terms:
            vector = self._vectors[self._word_index[term]] if term in self._word_index else fresh[term]
            scores = self._vectors @ vector
            result[term] = frozenset(word for i,word in enumerate(self._vocabulary) if scores[i] >= .90)
        return result

    def update(self, text, is_final, utterance_id, sequence):
        with self.lock:
            if sequence <= self.sequence or utterance_id in self.finalized:
                return AlgorithmResult()
            self.sequence = sequence
            result = self._stream_update(text,is_final,utterance_id)
            if is_final:
                self.finalized[utterance_id] = True
                if len(self.finalized) > 256:
                    self.finalized.popitem(last=False)
            return result


    def _matches(self, query: str, word: str) -> bool:
        cached = self._term_matches.get(query)
        return word in cached.words if cached is not None else matches(query, word)

    @property
    def buffer_words(self) -> tuple[str, ...]:
        return tuple(deque((*self._queue, *self._live_words), maxlen=self._queue.maxlen))

    def _build_index(self) -> None:
        counts: Counter[str] = Counter()
        pages: dict[int, list[str]] = {}
        for block in self.blocks:
            if block.block_id in self._block_tokens:
                raise ValueError(f'중복 block_id: {block.block_id}')
            text_words = lexical_terms(block.text)
            words = text_words + tuple((term for keyword in block.keywords for term in lexical_terms(keyword)))
            self._block_tokens[block.block_id] = words
            pages.setdefault(block.page, []).extend(words)
            self._page_pairs.setdefault(block.page, set()).update(zip(text_words, text_words[1:]))
            counts.update(set(words))
        total = len(self.blocks)
        self._idf = {word: log((total + 1) / (count + 1)) + 1.0 for word, count in counts.items()}
        self._page_tokens = {page: tuple(words) for page, words in pages.items()}

    def _prepare_matches(self, terms: Sequence[str]) -> None:
        missing = [term for term in terms if term not in self._term_matches]
        semantic = self._semantic(missing) if missing else {}
        for term in missing:
            words = frozenset((word for word in self._idf if matches(term, word) or word in semantic.get(term, ())))
            idf = self._idf.get(term, max((self._idf[word] for word in words), default=1.0))
            hits, prefixes = ([], [])
            for index, (all_words, prefix_words) in enumerate(self._target_terms):
                if words & all_words:
                    hits.append(index)
                    if words & prefix_words:
                        prefixes.append(index)
            self._term_matches[term] = _TermMatch(words, idf, np.array(hits, dtype=np.intp), np.array(prefixes, dtype=np.intp))
        for term in terms:
            self._term_matches.move_to_end(term)

    def _update_scores(self, state: _QueryScores, counts: Counter[str]) -> None:
        for term in dict.fromkeys((*state.counts, *counts)):
            previous, current = (state.counts[term], counts[term])
            if previous == current:
                continue
            evidence = self._term_matches[term]

            def weight(count):
                return evidence.idf * (1 + 0.15 * min(count - 1, 2)) if count else 0.0
            delta = weight(current) - weight(previous)
            present = int(current > 0) - int(previous > 0)
            state.weight += delta
            state.totals[evidence.hits, 0] += delta
            state.totals[evidence.hits, 1] += present
            state.totals[evidence.prefixes, 2] += present
        state.counts = counts
        state.totals[state.totals[:, 1] == 0] = 0
        if not counts:
            state.weight = 0.0

    def _sync_scores(self, history: Counter[str], latest: Counter[str]) -> None:
        active = tuple(dict.fromkeys((*history, *latest)))
        self._prepare_matches(active)
        self._update_scores(self._history_scores, history)
        self._update_scores(self._latest_scores, latest)
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
        return min(1.0, 0.86 * coverage + 0.14 * specificity + 0.035 * prefix_hits / hits)

    def _candidate_score(self, similarity: float, page: int, anchor: int) -> float:
        result = similarity - min(0.24, max(0, abs(page - anchor) - 1) * 0.06)
        if page == self.current_page:
            result += 0.025
        elif page < self.current_page:
            distance = self.current_page - page
            result -= 0.08 * min(distance / 5.0, 1.0)
        return max(0.0, min(1.0, result))

    def _stream_update(self, speech_text: str, is_final: bool, utterance_id: str) -> AlgorithmResult:
        words = lexical_terms(speech_text)
        if utterance_id != self._stream_utterance:
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
        result = self._confirm_transition(self._evaluate(words, anchor, speech_text), words, is_final)
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

    def _confirm_transition(self, result: AlgorithmResult, words: Sequence[str], is_final: bool) -> AlgorithmResult:
        if result.page is None or result.page == self.current_page:
            self._clear_pending()
            return result
        recent = words[-8:]
        terms = tuple(recent)
        support = frozenset((term for term in terms if any((self._matches(term, word) for word in self._page_tokens[result.page]))))
        novel_support = {term for term in support if term not in _GENERIC_TRANSITIONS and (not any((self._matches(term, word) for word in self._page_tokens.get(self.current_page, ()))))}
        content_words = recent
        focused = len(content_words) == 1 and len(content_words[0]) >= 3 and ((result.score or 0) >= 0.8) and bool(support) and (sum((all((any((self._matches(term, word) for word in page_words)) for term in terms)) for page_words in self._page_tokens.values())) == 1)
        continued = any((self._pending_words[-size:] == tuple(recent[:size]) for size in range(min(2, len(self._pending_words)), min(len(self._pending_words), len(recent)) + 1) if size > 0))
        growing = self._pending_page == result.page and continued and (len(content_words) >= 2) and (len(support) >= 2) and bool(support & self._pending_support) and (terms != self._pending_terms) and (len(novel_support) >= 2 or max(self._pending_score, result.score or 0) >= 0.65)
        confirmed = is_final and len(content_words) >= 2 and (len(support) >= 2)
        if focused or growing or confirmed:
            self._clear_pending()
            return result
        previous_score = self._pending_score if self._pending_page == result.page else 0
        self._pending_page, self._pending_support = (result.page, support)
        self._pending_score = max(previous_score, result.score or 0)
        self._pending_terms = terms
        self._pending_words = tuple(recent)
        return AlgorithmResult(status='NONE')

    def _evaluate(self, latest_words: Sequence[str], anchor: int, source_text: str) -> AlgorithmResult:
        current_words = self.buffer_words
        query_words = Counter(current_words)
        latest_terms = tuple(latest_words[-8:])
        self._sync_scores(query_words, Counter(latest_terms))
        if not current_words or not latest_terms:
            return AlgorithmResult(status='NONE')
        radius = 5
        candidates: list[tuple[float, Block]] = []

        def similarity(target: str | int) -> float:
            index = self._target_index[target]
            history = self._similarity(self._history_scores, index)
            fresh = self._similarity(self._latest_scores, index)
            return (history + 2 * fresh) / 3
        page_scores = {page: similarity(page) for page, words in self._page_tokens.items() if abs(page - anchor) <= radius}
        anchor_words = self._page_tokens.get(anchor, ())
        novel = {term for term in latest_terms if term not in _GENERIC_TRANSITIONS and (not any((self._matches(term, word) for word in anchor_words)))}
        query_pairs = set(zip(latest_terms, latest_terms[1:]))
        reference = _REFERENCE.search(' '.join(tokenize(source_text)[-8:]))
        eligible_pages = {anchor}
        for page in page_scores:
            if page == anchor or reference:
                continue
            page_words = self._page_tokens[page]
            new_hits = {term for term in novel if any((self._matches(term, word) for word in page_words))}
            new_pairs = (query_pairs & self._page_pairs[page]) - self._page_pairs.get(anchor, set())
            focused = len(set(latest_terms)) == 1 and sum((any((self._matches(latest_terms[0], word) for word in self._page_tokens[other])) for other in page_scores)) == 1
            if len(new_hits) >= 2 or (new_hits and new_pairs) or focused:
                eligible_pages.add(page)
        for block in self.blocks:
            if block.page not in eligible_pages:
                continue
            if not any((self._matches(term, word) for term in latest_terms for word in self._block_tokens[block.block_id])):
                continue
            raw = similarity(block.block_id)
            if raw == 0:
                continue
            weighted = self._candidate_score(0.25 * raw + 0.75 * page_scores[block.page], block.page, anchor)
            candidates.append((weighted, block))
        page_best: dict[int, float] = {}
        for score, block in candidates:
            page_best[block.page] = max(page_best.get(block.page, 0), score)
        candidates = [(score, block) for score, block in candidates if abs(block.page - anchor) <= 1 or score >= 0.65 or score >= max((value for page, value in page_best.items() if page != block.page), default=0) + max(0.12, 0.05)]
        if not candidates:
            return AlgorithmResult(status='NONE')
        current_page_candidates = [(score, block) for score, block in candidates if block.page == self.current_page]
        candidates.sort(key=lambda pair: (-pair[0], pair[1].page != self.current_page, abs(pair[1].page - self.current_page), pair[1].page, pair[1].block_id))
        best_score, best_block = candidates[0]
        if best_score < 0.34:
            return AlgorithmResult(status='NONE')
        if best_block.page != self.current_page and current_page_candidates:
            stay_score, stay_block = max(current_page_candidates, key=lambda pair: pair[0])
            if stay_score >= 0.34 and best_score < stay_score + 0.05:
                best_score, best_block = (stay_score, stay_block)
        return AlgorithmResult(status='MATCH', page=best_block.page, block_id=best_block.block_id, score=round(best_score, 5))
