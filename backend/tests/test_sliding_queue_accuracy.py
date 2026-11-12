import pytest

from app.schemas.models import Block
from app.search import SearchEngine, SearchSettings
from app.search.lexical import lexical_terms


def block(page, text, number=1, keywords=None):
    return Block(block_id=f'p{page:04}_b{number:04}', page=page, text=text,
                 block_type='paragraph', bbox=(0.1, 0.1, 0.9, 0.3),
                 rects=[(0.1, 0.1, 0.9, 0.3)], keywords=keywords or [])


def engine_for(blocks, **settings):
    engine = SearchEngine()
    engine.register_document('doc', blocks)
    engine.create_session('s', 'doc', settings=SearchSettings(**settings))
    return engine


@pytest.mark.parametrize('spoken,written', [
    ('크리에이트 테이블', 'CREATE TABLE'),
    ('얼터 테이블 애드로', 'ALTER TABLE ADD'),
    ('드롭 컬럼을', 'DROP COLUMN'),
    ('셀렉트 디스팅트', 'SELECT DISTINCT'),
    ('프라이머리키는 유니크로', 'PRIMARY KEY UNIQUE'),
    ('낫널은 디폴트로', 'NOT NULL DEFAULT'),
    ('오더 바이 에이에스씨', 'ORDER BY ASC'),
    ('그룹 바이', 'GROUP BY'),
    ('레프트 아우터 조인으로', 'LEFT OUTER JOIN'),
])
def test_spoken_sql_and_written_sql_use_same_terms(spoken, written):
    assert set(lexical_terms(written)) <= set(lexical_terms(spoken))
    engine = engine_for([block(1, '강의 소개'), block(2, written)])
    assert engine.search('s', spoken).page == 2


def test_aliases_do_not_replace_parts_of_other_words():
    terms = lexical_terms('컨트롤러 미들웨어 롤러코스터')
    assert not {'role', 'where'} & set(terms)


def test_spacing_particles_and_conjugations_match_ocr_text():
    engine = engine_for([
        block(1, '공급업체 주소와 전화번호'),
        block(2, '상품이름과상품가격을검색한다'),
    ])
    assert engine.search('s', '상품 이름과 상품 가격을 검색합니다').page == 2


def test_page_context_disambiguates_explanations_in_separate_blocks():
    engine = engine_for([
        block(1, '설정 소개'),
        block(2, '기본 값은 속성의 값을 지정한다'),
        block(3, 'NOT NULL DEFAULT'),
        block(4, 'NOT NULL'),
        block(4, '속성이 널 값을 허용하지 않음을 의미하는 키워드', 2),
        block(4, 'DEFAULT', 3),
        block(4, '속성의 기본 값을 지정하는 키워드', 4),
    ])
    result = engine.search('s', '낫 널은 널 값을 허용하지 않고 디폴트는 속성의 기본 값을 지정합니다')
    assert result.page == 4
    assert result.block_id in {'p0004_b0001', 'p0004_b0002', 'p0004_b0003', 'p0004_b0004'}


def test_short_new_topic_overcomes_long_previous_utterance_in_live_queue():
    engine = engine_for([
        block(1, '강의 소개'),
        block(2, '데이터베이스 관계형 모델의 기본 개념 구조 원리 특성 이해'),
        block(3, '인덱스 탐색'),
    ])
    engine.live_search('s', '데이터베이스 관계형 모델의 기본 개념 구조 원리 특성 이해', 'u1', 1, True)
    result, words, page = engine.live_search('s', '인덱스 탐색', 'u2', 2, False)
    assert result.page == page == 3
    assert '데이터베이스' in words and words[-2:] == ['인덱스', '탐색']
    # Revisions replace the hypothesis; the final is committed once.
    engine.live_search('s', '인덱스 탐색', 'u2', 3, True)
    engine.live_search('s', '인덱스 탐색', 'u2', 4, True)
    assert engine.get_buffer_words('s').count('인덱스') == 1


@pytest.mark.parametrize('unrelated', [
    '오늘 점심 메뉴는 비빔밥입니다', '마이크 소리 잘 들리시나요',
    '잠깐 쉬었다가 계속할게요', '그리고 이제 그거',
])
def test_old_queue_cannot_create_a_match_for_unrelated_speech(unrelated):
    engine = engine_for([block(1, '소개'), block(2, '인덱스 탐색 실행 계획')])
    engine.search('s', '인덱스 탐색 실행 계획')
    result, words, page = engine.live_search('s', unrelated, 'u2', 1, True)
    assert result.status == 'NONE' and result.page is None
    assert page == 2
    assert '인덱스' in words


def test_history_still_disambiguates_a_shared_new_word():
    engine = engine_for([
        block(1, '정규화 설계'), block(2, '인덱스 설계'),
    ])
    engine.search('s', '인덱스')
    assert engine.search('s', '설계').page == 2


def test_explicit_block_keywords_are_preserved():
    engine = engine_for([
        block(1, '소개'), block(2, '데이터 제약조건', keywords=['무결성']),
    ])
    assert engine.search('s', '무결성을').page == 2


def test_word_window_and_local_radius_still_bound_all_evidence():
    engine = engine_for([
        block(1, '시작'), block(2, '인덱스 탐색'), block(8, '원격 강의'),
    ], sliding_window_words=2)
    assert engine.search('s', '인덱스 탐색 무관한단어 없는단어').status == 'NONE'
    assert engine.search('s', '원격 강의').status == 'NONE'
    assert engine.get_current_page('s') == 1
    assert engine.get_buffer_words('s') == ['원격', '강의']
