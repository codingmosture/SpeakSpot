"""Rank pages from their text, independently of block highlights."""

from collections import Counter, defaultdict
from functools import cached_property, lru_cache
from math import ceil, log
import re
from threading import Lock

import numpy as np
from kiwipiepy import Kiwi, Match


_KIWI_LOCK = Lock()
_HELPERS = frozenset({'하','되','있','없','보','이','것','수','등','다음','그다음','이번','그때','이거','그거','거','얘','여기','저기'})
_IDENTIFIER = re.compile(r'[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*')


@lru_cache(maxsize=1)
def kiwi():
    return Kiwi(num_workers=1)


@lru_cache(maxsize=8192)
def lexical_terms(text):
    text = text.casefold()
    with _KIWI_LOCK:
        tokens = kiwi().tokenize(text, match_options=Match.ALL | Match.JOIN_NOUN_PREFIX | Match.JOIN_NOUN_SUFFIX)
    terms = {t.start: t.form.casefold() for t in tokens if t.tag in {'NNG', 'NNP'} and t.form not in _HELPERS}
    starts = {t.start for t in tokens if t.tag == 'SL'}
    terms.update((m.start(), m.group()) for m in _IDENTIFIER.finditer(text) if m.start() in starts)
    return tuple(word for _, word in sorted(terms.items()))


def normalize(scores, floor, ceiling=2.5):
    median = np.median(scores, axis=-1, keepdims=True)
    spread = np.maximum(np.quantile(scores, .90, axis=-1, keepdims=True) - median, floor)
    return np.clip((scores - median) / spread, -ceiling, ceiling)


class PageIndex:
    def __init__(self, by_page):
        by_page = {page: [clean for text in texts if (clean := ' '.join(text.split()))]
                   for page, texts in by_page.items()}
        by_page = {page: texts for page, texts in by_page.items() if texts}
        if not by_page:
            raise ValueError('PDF에서 읽을 수 있는 본문을 찾지 못했습니다.')
        self.pages = np.array(sorted(by_page))
        counts = Counter()
        for texts in by_page.values():
            counts.update({text.casefold() for text in texts})
        cutoff = max(3, ceil(len(by_page) / 4))
        self.texts = []
        for page in self.pages:
            texts = list(dict.fromkeys(by_page[page]))
            body = [text for text in texts if counts[text.casefold()] < cutoff]
            self.texts.append(' '.join(body or texts))

    def passages(self, size=72, stride=52):
        passages, starts = [], []
        for text in self.texts:
            starts.append(len(passages))
            words = text.split()
            for start in range(0, len(words), stride):
                passages.append(' '.join(words[start:start + size]))
                if start + size >= len(words):
                    break
        return passages, starts

    @cached_property
    def _coverage_index(self):
        postings = defaultdict(list)
        for page, text in enumerate(self.texts):
            for term in set(lexical_terms(text)):
                postings[term].append(page)
        return {term: (positions, 1 + log((1 + len(self.pages)) / (1 + len(positions))))
                for term, positions in postings.items()}

    def coverage(self, text):
        scores = np.zeros(len(self.pages))
        total = 0.
        for term in set(lexical_terms(text)):
            positions, weight = self._coverage_index.get(term, ([], 1.))
            total += weight
            scores[positions] += weight
        return scores / max(1., total)
