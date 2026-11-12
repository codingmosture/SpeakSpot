"""발화 및 PDF 토큰 추출.

원문 단어를 임의로 끝에서 자르지 않는다. 한국어 조사·활용형은 검색
매칭 시에만 제한적으로 확인하여 '제어같은 → 제어같' 같은 오류를 피한다.
Kiwi 형태소 분석을 나중에 이 모듈 내부로 교체할 수 있다.
"""
import re

_TOKEN_PATTERN = re.compile(r'[가-힣]+|[a-zA-Z0-9]+(?:[-_][a-zA-Z0-9]+)*')
_STOPWORDS = frozenset({
    '그', '이', '저', '그런데', '그리고', '그러면', '이제', '여기', '저기',
    '제가', '우리가', '하는', '것', '거', '이거', '그거', '이렇게', '저렇게',
    '정말', '좀', '한번', '다음', '같은', '경우', '입니다', '있습니다',
})
# 접미사만 떼어 비교하며 새로운 토큰을 생성·저장하지 않는다.
_SUFFIXES = frozenset({
    '은', '는', '이', '가', '을', '를', '에', '의', '와', '과', '도', '만', '로', '으로',
    '에서', '에게', '부터', '까지', '에는', '에서는', '하고', '이고', '인데',
    '이다', '입니다', '이라고', '라는', '같은', '처럼', '이라면',
    '한다', '합니다', '하는', '했다', '했습니다', '하지', '하면', '해서',
    '된다', '됩니다', '되는', '되면', '됐습니다', '됩니다만',
})


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
