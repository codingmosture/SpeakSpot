"""Noun-only search terms; retain technical English without rewriting speech."""
from functools import lru_cache
import re
from threading import Lock

from kiwipiepy import Kiwi, Match


_lock = Lock()
_kiwi: Kiwi | None = None
_IDENTIFIER = re.compile(r'[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*')
_HELPERS = frozenset({
    '하', '되', '있', '없', '보', '이', '것', '수', '등',
    '다음', '그다음', '이번', '그때', '이거', '그거', '거', '얘', '여기', '저기',
})


@lru_cache(maxsize=8192)
def lexical_terms(text: str) -> tuple[str, ...]:
    """Keep common/proper nouns and English terms/identifiers, in source order."""
    global _kiwi
    text = text.casefold()
    with _lock:
        if _kiwi is None:
            _kiwi = Kiwi(num_workers=1)
        tokens = _kiwi.tokenize(text, match_options=Match.ALL | Match.JOIN_NOUN_PREFIX | Match.JOIN_NOUN_SUFFIX)
    terms = {t.start: t.form.casefold() for t in tokens
             if t.tag in {'NNG', 'NNP'} and t.form not in _HELPERS}
    # Keep SQL2003 / employee_id intact; standalone numbers are not nouns.
    foreign_starts = {t.start for t in tokens if t.tag == 'SL'}
    terms.update((m.start(), m.group()) for m in _IDENTIFIER.finditer(text) if m.start() in foreign_starts)
    return tuple(term for _, term in sorted(terms.items()))
