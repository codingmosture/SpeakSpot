"""Lexical evidence shared by section tracking and within-page highlighting."""
from collections import Counter, defaultdict
from functools import lru_cache
import re

import numpy as np

from document_terms import acronyms, literal_terms
from lecture_pdf import block_box
from page_ranking import lexical_terms

_NUMBERS = re.compile(r'(?<![\w.-])\d{2,6}(?![\d.])')
_NUMERIC_TALK = re.compile(r'주소|위치|번호|시작|끝|크기|계산|적재|거리|배치|간격|차이|범위|이동|오프셋')


@lru_cache(maxsize=8192)
def phrases(text):
    words = lexical_terms(text)
    return frozenset(words[i:i + size] for size in (2, 3, 4) for i in range(len(words) - size + 1))


@lru_cache(maxsize=8192)
def anchor_keys(text):
    return frozenset(words for words in phrases(text) if len(set(words)) >= 2)


def _stacked_labels(lines):
    for line in lines:
        x, y, right, bottom = line['box']
        if len(line['text'].split()) > 2 or right - x > .17:
            continue
        for below in lines:
            l, t, r, b = below['box']
            if -.002 <= t - bottom <= .020 and abs((x + right - l - r) / 2) <= .014 and len(below['text'].split()) <= 2:
                yield line['text'] + ' ' + below['text'], (min(x, l), y, max(right, r), b)


def _overlap(inner, block):
    left, top, right, bottom = inner
    x, y, r, b = block
    hit = max(0., min(right, r) - max(left, x)) * max(0., min(bottom, b) - max(top, y))
    return hit / max(1e-6, (right-left) * (bottom-top))


def _diagram_numbers(lines_by_page):
    pages = {}
    for page, lines in lines_by_page.items():
        repeated = defaultdict(set)
        for line in lines:
            left, top, right, bottom = line['box']
            if .20 <= top < .88 and bottom - top < .08 and abs(line['rotation']) <= .05 and len(line['text'].split()) <= 3:
                for hit in _NUMBERS.findall(line['text']):
                    repeated[int(hit)].add(round((left + right) / 2, 2))
        pages[page] = {number for number, xs in repeated.items() if max(xs) - min(xs) > .10}
    frequency = Counter(number for values in pages.values() for number in values)
    return {page: frozenset(number for number in values if frequency[number] == 1) for page, values in pages.items()}


class LectureAnchors:
    def __init__(self, pages, blocks, profiles, layout, dictionary, single_weight):
        self.pages = pages
        self.numbers = _diagram_numbers(layout)
        self._dictionary = dictionary
        self._single_weight = single_weight
        page_keys, block_keys = {}, {}
        for page in pages:
            lines = [line for line in layout.get(page, ())
                     if .02 <= line['box'][1] <= .92 and abs(line['rotation']) <= .05]
            stacked = [(self._keys(text), box) for text, box in _stacked_labels(lines)]
            keys = set().union(*(self._keys(line['text']) for line in lines), *(tags for tags, _ in stacked))
            block_keys[page] = []
            for block in blocks[page]:
                terms = set()
                if len(block.text.split()) <= 46:
                    box = block_box(block)
                    terms.update(self._keys(block.text))
                    terms.update(key for tags, rect in stacked if _overlap(rect, box) >= .35 for key in tags)
                keys.update(terms)
                block_keys[page].append(terms)
            page_keys[page] = keys
        vocabulary = sorted(set().union(*page_keys.values()), key=repr)
        self.columns = {key: i for i, key in enumerate(vocabulary)}
        self._paired = np.array([isinstance(key, tuple) and key[0] == 'document-term' for key in vocabulary], dtype=bool)
        self.ownership = np.array([[key in page_keys[page] for page in pages]
                                   for key in vocabulary], dtype=bool).reshape(len(vocabulary), len(pages))
        frequency = self.ownership.sum(axis=1)
        rarity = np.log1p((len(pages) - frequency + .5) / (frequency + .5))
        self.weights = rarity * [1. if isinstance(key, tuple) else single_weight for key in vocabulary]
        self.heading = [[self.columns[key] for key in self._keys(profiles[page].header) if key in self.columns]
                        for page in pages]
        self.block_indexes = {}
        for page, groups in block_keys.items():
            postings = defaultdict(list)
            for column, keys in enumerate(groups):
                for key in keys:
                    postings[self.columns[key]].append(column)
            self.block_indexes[page] = {
                key: (np.asarray(columns), self.weights[key] * np.sqrt(1 - len(columns) / len(groups)))
                for key, columns in postings.items()
            }

    def _keys(self, text):
        keys = anchor_keys(text)
        if self._dictionary is not None:
            keys |= self._dictionary.matches(text)
        if self._single_weight:
            keys |= literal_terms(text)
        return keys

    def observe(self, short, long, *, literal_heading=False):
        weights = np.zeros(len(self.columns))
        weights[[self.columns[key] for key in self._keys(long) if key in self.columns]] = .48
        weights[[self.columns[key] for key in self._keys(short) if key in self.columns]] = 1.
        heading = self._heading_scores(weights, literal_heading)
        numbers = {int(number) for number in _NUMBERS.findall(short)} if _NUMERIC_TALK.search(long) else set()
        numeric = np.asarray([bool(numbers & self.numbers.get(page, ())) if page in self.numbers else False
                              for page in self.pages])
        return weights, heading, numeric

    def _heading_scores(self, weights, literal):
        scores = weights * self.weights
        if literal:
            scores = scores * ~self._paired
        return np.asarray([min(1.5, scores[keys].sum() / 2) for keys in self.heading])

    def literal_titles(self, text):
        weights = np.zeros(len(self.columns))
        keys = [self.columns[key] for key in anchor_keys(text) if key in self.columns]
        weights[keys] = 1.
        return self._heading_scores(weights, True)

    def incoming(self, weight, current, *, include_pairs=True):
        owner = self.ownership[:, current] if current is not None else np.zeros(len(weight), dtype=bool)
        eligible = ~owner if include_pairs else ~owner & ~self._paired
        return (weight * self.weights * eligible) @ self.ownership

    def block_support(self, page, weights, count):
        evidence = np.zeros(count)
        for key, (positions, weight) in self.block_indexes[page].items():
            if weights[key]:
                evidence[positions] += weight * weights[key]
        return np.minimum(1.4, evidence / 2.1)


class BlockPhrases:
    def __init__(self, blocks, dictionary):
        self._dictionary = dictionary
        self.pages = {}
        for page, rows in blocks.items():
            postings = {}
            for i, block in enumerate(rows):
                if len(block.text.split()) > 45:
                    continue
                for key in phrases(block.text):
                    self._store(postings, key, i, 'words', .64 if len(key) == 2 else .96)
                if dictionary is not None:
                    for key in dictionary.matches(block.text):
                        self._store(postings, key, i, 'alias', 1.30)
                    for code in acronyms(block.text):
                        self._store(postings, code, i, 'code', 1.55)
            self.pages[page] = {
                key: (kind, weight / len(owners) ** .65, np.asarray(sorted(owners)))
                for key, (kind, weight, owners) in postings.items() if len(owners) < len(rows)
            }

    @staticmethod
    def _store(postings, key, position, kind, weight):
        _, old_weight, owners = postings.get(key, (kind, 0., set()))
        owners.add(position)
        postings[key] = kind, max(weight, old_weight), owners

    def scores(self, short, long, blocks):
        recent, context = self._query(short), self._query(long)
        for page, count in blocks.items():
            evidence = np.zeros(count)
            for key, (kind, weight, indexes) in self.pages[page].items():
                if key in recent[kind]:
                    evidence[indexes] += weight
                elif key in context[kind]:
                    evidence[indexes] += .35 * weight
            yield page, np.tanh(evidence / 1.8)

    def _query(self, text):
        return {'words': phrases(text), 'code': set(lexical_terms(text)),
                'alias': self._dictionary.matches(text) if self._dictionary is not None else ()}
