"""페이지와 블록을 검색할 때 사용할 용어 단서를 구축하고 점수를 계산한다."""
from collections import Counter, defaultdict
from functools import cached_property, lru_cache
from math import ceil, log
import re
from threading import Lock

import numpy as np
from kiwipiepy import Kiwi, Match

from pdf import block_box


# 형태소 분석과 페이지 단위 인덱스
_KIWI_LOCK = Lock()
_HELPERS = frozenset({'하','되','있','없','보','이','것','수','등','다음','그다음','이번','그때','이거','그거','거','얘','여기','저기'})
_IDENTIFIER = re.compile(r'[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*')


# 한국어 형태소 분석기를 한 번 초기화해 재사용한다.
@lru_cache(maxsize=1)
def kiwi():
    return Kiwi(num_workers=1)


# 검색에 사용할 한국어 명사와 영문 식별자를 등장 순서대로 뽑는다.
@lru_cache(maxsize=8192)
def lexical_terms(text):
    text = text.casefold()
    with _KIWI_LOCK:
        tokens = kiwi().tokenize(text, match_options=Match.ALL | Match.JOIN_NOUN_PREFIX | Match.JOIN_NOUN_SUFFIX)
    terms = {t.start: t.form.casefold() for t in tokens if t.tag in {'NNG', 'NNP'} and t.form not in _HELPERS}
    starts = {t.start for t in tokens if t.tag == 'SL'}
    terms.update((m.start(), m.group()) for m in _IDENTIFIER.finditer(text) if m.start() in starts)
    return tuple(word for _, word in sorted(terms.items()))


# 점수의 중앙값과 분산 범위를 사용해 비교 가능한 척도로 바꾼다.
def normalize(scores, floor, ceiling=2.5):
    median = np.median(scores, axis=-1, keepdims=True)
    spread = np.maximum(np.quantile(scores, .90, axis=-1, keepdims=True) - median, floor)
    return np.clip((scores - median) / spread, -ceiling, ceiling)


class PageIndex:
    # 페이지별 본문을 정리하고 문서 전체에 반복되는 텍스트를 걸러낸다.
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

    # 페이지 본문을 일부 겹치는 검색 단위로 나누고 시작 위치를 기록한다.
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

    # 용어가 나타난 페이지와 용어의 희소성 가중치를 기록한다.
    @cached_property
    def _coverage_index(self):
        postings = defaultdict(list)
        for page, text in enumerate(self.texts):
            for term in set(lexical_terms(text)):
                postings[term].append(page)
        return {term: (positions, 1 + log((1 + len(self.pages)) / (1 + len(positions))))
                for term, positions in postings.items()}

    # 발화에 나온 검색 용어를 각 페이지가 얼마나 포함하는지 계산한다.
    def coverage(self, text):
        scores = np.zeros(len(self.pages))
        total = 0.
        for term in set(lexical_terms(text)):
            positions, weight = self._coverage_index.get(term, ([], 1.))
            total += weight
            scores[positions] += weight
        return scores / max(1., total)


# 문서에 병기된 용어와 약어
_PAIR = re.compile(r'(?:^|[·•:;|])\s*([^()\[\]·•:;|]{2,65})\s*[\(\[]([^()\[\]]{2,70})[\)\]]')
_LATIN = re.compile(r'[A-Za-z]')
_KOREAN = re.compile(r'[가-힣]')
_ACRONYM = re.compile(r'\b[A-Z][A-Z0-9]{1,7}\b')
_NOUN = re.compile(r'[가-힣]{2,}|[a-z][a-z0-9-]{2,}')


# 텍스트가 한글 또는 영문 한 종류로 되어 있는지 판별한다.
def _script(text):
    latin = bool(_LATIN.search(text))
    korean = bool(_KOREAN.search(text))
    return 'ko' if korean and not latin else 'en' if latin and not korean else None


# PDF에 직접 쓰인 검색용 단일 명사나 식별자를 뽑는다.
@lru_cache(maxsize=8192)
def literal_terms(text):
    return frozenset(term for term in lexical_terms(text) if _NOUN.fullmatch(term))


# 텍스트에 직접 표기된 대문자 약어를 검색용으로 정규화한다.
def acronyms(text):
    return {match.group().casefold() for match in _ACRONYM.finditer(text)}


# 괄호로 병기된 한글·영문 용어나 약어와 원어를 추출한다.
def _equivalents(text):
    for match in _PAIR.finditer(text):
        outer, parenthesis = (part.strip(' .–-•·') for part in match.groups())
        if len(outer.split()) > 8:
            continue
        opposite = {'ko': 'en', 'en': 'ko'}.get(_script(outer))
        forms = [part for part in map(str.strip, parenthesis.split(',')) if opposite and _script(part) == opposite]
        if forms:
            yield [outer, *forms]
        elif _ACRONYM.fullmatch(outer) and len(parenthesis.split()) >= 2 and _script(parenthesis) == 'en':
            initials = ''.join(word[0].upper() for word in re.findall(r'[A-Za-z]+', parenthesis))
            if initials == outer:
                yield [outer, parenthesis]


class DocumentTerms:
    # 병기 표현의 표기 변형을 문서 안에서 같은 용어 단서로 묶는다.
    def __init__(self, texts):
        self._forms = defaultdict(set)
        for text in texts:
            for forms in _equivalents(text):
                tokens = sorted({tuple(lexical_terms(form)) for form in forms} - {()})
                if len(tokens) < 2:
                    continue
                key = ('document-term', *tokens)
                for variant in tokens:
                    self._forms[variant].add(key)
        self._lengths = {len(variant) for variant in self._forms}

    # 발화나 PDF 텍스트에 나타난 문서별 용어 단서를 찾는다.
    def matches(self, text):
        result = set()
        words = lexical_terms(text) if self._lengths else ()
        for n in self._lengths:
            for index in range(len(words) - n + 1):
                result.update(self._forms.get(words[index:index + n], ()))
        return result


# 제목, 도식 숫자, 블록별 발화 단서
_NUMBERS = re.compile(r'(?<![\w.-])\d{2,6}(?![\d.])')
_NUMERIC_TALK = re.compile(r'주소|위치|번호|시작|끝|크기|계산|적재|거리|배치|간격|차이|범위|이동|오프셋')


# 검색 용어를 연속된 두 단어에서 네 단어 묶음으로 만든다.
@lru_cache(maxsize=8192)
def phrases(text):
    words = lexical_terms(text)
    return frozenset(words[i:i + size] for size in (2, 3, 4) for i in range(len(words) - size + 1))


# 동일 단어만 반복된 묶음을 제외하고 구별에 쓸 표현을 얻는다.
@lru_cache(maxsize=8192)
def anchor_keys(text):
    return frozenset(words for words in phrases(text) if len(set(words)) >= 2)


# 도식에서 같은 열에 세로로 이어진 짧은 레이블을 붙인다.
def _stacked_labels(lines):
    for line in lines:
        x, y, right, bottom = line['box']
        if len(line['text'].split()) > 2 or right - x > .17:
            continue
        for below in lines:
            l, t, r, b = below['box']
            if -.002 <= t - bottom <= .020 and abs((x + right - l - r) / 2) <= .014 and len(below['text'].split()) <= 2:
                yield line['text'] + ' ' + below['text'], (min(x, l), y, max(right, r), b)


# PDF 텍스트 줄이 강조 블록 안에 포함된 면적 비율을 계산한다.
def _overlap(inner, block):
    left, top, right, bottom = inner
    x, y, r, b = block
    hit = max(0., min(right, r) - max(left, x)) * max(0., min(bottom, b) - max(top, y))
    return hit / max(1e-6, (right-left) * (bottom-top))


# 한 페이지의 도식 여러 열에 반복되는 문서 고유 숫자를 찾는다.
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
    # 페이지와 블록의 고유 표현, 제목 단서, 도식 숫자를 색인한다.
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

    # 텍스트에서 직접 표기와 문서별 병기 단서를 검색 키로 모은다.
    def _keys(self, text):
        keys = anchor_keys(text)
        if self._dictionary is not None:
            keys |= self._dictionary.matches(text)
        if self._single_weight:
            keys |= literal_terms(text)
        return keys

    # 최근 발화로 용어, 제목, 도식 숫자의 일치 증거를 계산한다.
    def observe(self, short, long, *, literal_heading=False):
        weights = np.zeros(len(self.columns))
        weights[[self.columns[key] for key in self._keys(long) if key in self.columns]] = .48
        weights[[self.columns[key] for key in self._keys(short) if key in self.columns]] = 1.
        heading = self._heading_scores(weights, literal_heading)
        numbers = {int(number) for number in _NUMBERS.findall(short)} if _NUMERIC_TALK.search(long) else set()
        numeric = np.asarray([not numbers.isdisjoint(self.numbers.get(page, ())) for page in self.pages])
        return weights, heading, numeric

    # 현재 일치한 용어가 각 페이지 제목을 얼마나 뒷받침하는지 계산한다.
    def _heading_scores(self, weights, literal):
        scores = weights * self.weights
        if literal:
            scores = scores * ~self._paired
        return np.asarray([min(1.5, scores[keys].sum() / 2) for keys in self.heading])

    # 주제 전환 뒤 문장에 제목 표현이 직접 나왔는지 확인한다.
    def literal_titles(self, text):
        weights = np.zeros(len(self.columns))
        keys = [self.columns[key] for key in anchor_keys(text) if key in self.columns]
        weights[keys] = 1.
        return self._heading_scores(weights, True)

    # 현재 페이지와 겹치지 않는 단서로 다른 페이지의 근거를 계산한다.
    def incoming(self, weight, current, *, include_pairs=True):
        owner = self.ownership[:, current] if current is not None else np.zeros(len(weight), dtype=bool)
        eligible = ~owner if include_pairs else ~owner & ~self._paired
        return (weight * self.weights * eligible) @ self.ownership

    # 현재 페이지에서 각 블록에만 해당하는 단서 점수를 더한다.
    def block_support(self, page, weights, count):
        evidence = np.zeros(count)
        for key, (positions, weight) in self.block_indexes[page].items():
            if weights[key]:
                evidence[positions] += weight * weights[key]
        return np.minimum(1.4, evidence / 2.1)


class BlockPhrases:
    # 블록별 문구, 병기 표현, 약어를 모아 같은 페이지 안에서 구별할 수 있게 한다.
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

    # 검색 키의 해당 블록과 가장 높은 가중치를 저장한다.
    @staticmethod
    def _store(postings, key, position, kind, weight):
        _, old_weight, owners = postings.get(key, (kind, 0., set()))
        owners.add(position)
        postings[key] = kind, max(weight, old_weight), owners

    # 최근 발화와 앞 문맥에서 찾은 표현을 블록별 점수로 환산한다.
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

    # 발화를 문구, 약어, 병기 표현별 검색 키로 준비한다.
    def _query(self, text):
        return {'words': phrases(text), 'code': set(lexical_terms(text)),
                'alias': self._dictionary.matches(text) if self._dictionary is not None else ()}
