"""Index equivalent terms explicitly printed together in a PDF."""
from collections import defaultdict
from functools import lru_cache
import re

from page_ranking import lexical_terms

_PAIR = re.compile(r'(?:^|[·•:;|])\s*([^()\[\]·•:;|]{2,65})\s*[\(\[]([^()\[\]]{2,70})[\)\]]')
_LATIN = re.compile(r'[A-Za-z]')
_KOREAN = re.compile(r'[가-힣]')
_ACRONYM = re.compile(r'\b[A-Z][A-Z0-9]{1,7}\b')
_NOUN = re.compile(r'[가-힣]{2,}|[a-z][a-z0-9-]{2,}')


def _script(text):
    latin = bool(_LATIN.search(text))
    korean = bool(_KOREAN.search(text))
    return 'ko' if korean and not latin else 'en' if latin and not korean else None


@lru_cache(maxsize=8192)
def literal_terms(text):
    return frozenset(term for term in lexical_terms(text) if _NOUN.fullmatch(term))


def acronyms(text):
    return {match.group().casefold() for match in _ACRONYM.finditer(text)}


def _equivalents(text):
    for match in _PAIR.finditer(text):
        outer, parenthesis = (part.strip(' .–-•·') for part in match.groups())
        if len(outer.split()) > 8:
            continue
        inside = [part.strip() for part in parenthesis.split(',')]
        script = _script(outer)
        opposite = {'ko': 'en', 'en': 'ko'}.get(script)
        if opposite and any(_script(part) == opposite for part in inside):
            yield [outer, *(part for part in inside if _script(part) == opposite)]
        elif _ACRONYM.fullmatch(outer) and len(parenthesis.split()) >= 2 and _script(parenthesis) == 'en':
            initials = ''.join(word[0].upper() for word in re.findall(r'[A-Za-z]+', parenthesis))
            if initials == outer:
                yield [outer, parenthesis]


class DocumentTerms:
    def __init__(self, texts):
        self._forms = defaultdict(set)
        self._lengths = set()
        for text in texts:
            for forms in _equivalents(text):
                tokens = sorted({tuple(lexical_terms(form)) for form in forms} - {()})
                if len(tokens) < 2:
                    continue
                key = ('document-term', *tokens)
                for variant in tokens:
                    self._forms[variant].add(key)
                    self._lengths.add(len(variant))

    def matches(self, text):
        result = set()
        if self._forms:
            words = lexical_terms(text)
            for n in self._lengths:
                for index in range(len(words) - n + 1):
                    result.update(self._forms.get(words[index:index + n], ()))
        return result
