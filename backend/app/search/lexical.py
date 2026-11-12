"""Sliding queue terms: Korean morphology and spoken SQL keyword spellings.

The queue still contains the original words. Only the comparison index is
normalized, identically for PDF text and speech hypotheses.
"""
from functools import lru_cache
import re
from threading import Lock

from kiwipiepy import Kiwi


# Long phrases precede their components (e.g. NOT NULL before NULL).
_SPOKEN_SQL = {
    '프라이머리 키': 'primary key', '포린 키': 'foreign key',
    '낫 널': 'not null',
    '에스 큐 엘': 'sql', '디디엘': 'ddl', '디엠엘': 'dml', '디씨엘': 'dcl',
    '크리에이트': 'create', '얼터': 'alter', '드롭': 'drop',
    '셀렉트': 'select', '셀렉': 'select', '디스팅트': 'distinct',
    '인서트': 'insert', '인투': 'into', '밸류즈': 'values',
    '업데이트': 'update', '딜리트': 'delete', '테이블': 'table',
    '디폴트': 'default', '유니크': 'unique', '레퍼런시스': 'references',
    '캐스케이드': 'cascade', '컨스트레인트': 'constraint',
    '컬럼': 'column', '체크': 'check', '애드': 'add',
    '에이에스씨': 'asc', '디이에스씨': 'desc', '에이에스': 'as',
    '웨어': 'where', '라이크': 'like', '오더 바이': 'order by',
    '그룹 바이': 'group by', '이즈': 'is', '널': 'null',
    '레프트': 'left', '라이트': 'right', '아우터': 'outer', '조인': 'join',
    '그랜트': 'grant', '리보크': 'revoke', '롤': 'role',
    '커서': 'cursor', '오픈': 'open',
}
_ALIASES = {
    re.sub(r'\s+', '', word): replacement for word, replacement in _SPOKEN_SQL.items()
}
_ALIAS_PATTERN = re.compile(r'(?<![가-힣a-z0-9_])(?:' + '|'.join(
    r'\s*'.join(map(re.escape, word))
    for word in sorted(_ALIASES, key=len, reverse=True)
) + r')(?![가-힣a-z0-9_])')
_lock = Lock()
_kiwi: Kiwi | None = None
_HELPERS = frozenset({'하', '되', '있', '없', '보', '이', '것', '수', '등'})


@lru_cache(maxsize=8192)
def lexical_terms(text: str) -> tuple[str, ...]:
    """Match particles, conjugations and OCR spacing without fuzzy substrings."""
    global _kiwi
    with _lock:
        if _kiwi is None:
            _kiwi = Kiwi(num_workers=1)
            for word in _ALIASES:
                _kiwi.add_user_word(word, 'NNP')
        tokens = _kiwi.tokenize(text.casefold())
    content = ' '.join(t.form.casefold() for t in tokens
                       if (t.tag.startswith('N') or t.tag in {'SL', 'SN', 'VV', 'VA', 'XR'})
                       and t.form not in _HELPERS)
    normalized = _ALIAS_PATTERN.sub(
        lambda m: _ALIASES[re.sub(r'\s+', '', m.group())], content,
    )
    return tuple(normalized.split())
