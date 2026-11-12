import pytest
from app.schemas.models import Block
from app.search import SearchEngine, SearchSettings
from app.search.registry import ALGORITHMS, MODELS
from app.search.tokenizer import tokenize, matches


def block(page: int, text: str, keywords=None) -> Block:
    return Block(
        block_id=f'p{page:04}_b0001', page=page, text=text,
        block_type='paragraph', bbox=(0.1, 0.1, 0.8, 0.25),
        rects=[(0.1, 0.1, 0.8, 0.25)], keywords=keywords or [],
    )


def sample_engine(window=3) -> SearchEngine:
    engine = SearchEngine()
    engine.register_document('doc', [
        block(1, '문서 소개 목차'),
        block(2, 'DISTINCT 는 중복된 행을 제거한다', ['DISTINCT', '중복', '제거']),
        block(3, '정규화 함수 종속성 테이블 분해', ['정규화', '종속성']),
        block(7, '제어 흐름과 상태 머신', ['제어']),
        block(12, '최적화 병렬 실행과 성능'),
    ])
    engine.create_session('session', 'doc', settings=SearchSettings(
        sliding_window_words=window, local_radius_pages=5,
        min_match_score=0.25, stay_bonus=0,
        switch_margin=0, backward_penalty=0,
    ))
    return engine


def test_default_starts_page_one_and_matches():
    engine = sample_engine()
    assert engine.get_current_page('session') == 1
    result = engine.search('session', 'DISTINCT 중복 제거', 's1')
    assert result.status == 'MATCH'
    assert result.page == 2
    assert result.block_id == 'p0002_b0001'
    assert result.strategy == 'sliding_queue'
    assert result.stage_used == 'sliding_queue'
    assert result.model == 'none'
    assert engine.get_current_page('session') == 2


def test_recent_words_replace_old_words_without_score_accumulation():
    engine = sample_engine(window=3)
    engine.search('session', 'DISTINCT 중복 제거')
    result = engine.search('session', '정규화 종속성 테이블')
    assert result.status == 'MATCH'
    assert result.page == 3
    assert engine.get_buffer_words('session') == ['정규화', '종속성', '테이블']


def test_forward_radius_based_on_current_page():
    engine = sample_engine(window=3)
    result = engine.search('session', '제어 흐름 상태')
    assert result.status == 'NONE'  # 7p: 초기 1p에서 범위 밖
    assert engine.get_current_page('session') == 1
    engine.search('session', '정규화 종속성 테이블')
    result = engine.search('session', '제어 흐름 상태')
    assert result.status == 'MATCH'
    assert result.page == 7


def test_none_does_not_move_page_or_return_location():
    engine = sample_engine(window=2)
    engine.search('session', '정규화 종속성')
    result = engine.search('session', '전혀관계없는발화 무의미한내용')
    assert result.status == 'NONE'
    assert result.page is None and result.block_id is None and result.score is None
    assert engine.get_current_page('session') == 3


def test_duplicate_segment_id_is_idempotent_and_reset_works():
    engine = sample_engine()
    first = engine.search('session', 'DISTINCT 중복', 'seg_1')
    again = engine.search('session', '정규화 종속성', 'seg_1')
    assert again == first
    assert engine.get_buffer_words('session') == ['distinct', '중복']
    engine.reset_session('session')
    assert engine.get_current_page('session') == 1
    assert engine.get_buffer_words('session') == []
    result = engine.search('session', '정규화 종속성', 'seg_1')
    assert result.page == 3


def test_separate_sessions_have_separate_queues():
    engine = sample_engine()
    engine.create_session('other', 'doc')
    engine.search('session', '정규화 종속성')
    assert engine.get_current_page('other') == 1
    assert engine.get_buffer_words('other') == []


def test_korean_word_not_truncated_and_suffix_matches_safe():
    assert tokenize('제어같은') == ['제어같은']
    assert matches('제어같은', '제어')
    assert tokenize('DISTINCT') == ['distinct']
    assert not matches('제어', '제로')


def test_model_requirements_and_invalid_combinations():
    assert {'sliding_queue', 'local', 'global', 'hybrid1', 'hybrid2'}.issubset(ALGORITHMS)
    assert {'bge-m3', 'e5-small', 'jev'}.issubset(MODELS)
    engine = sample_engine()
    with pytest.raises(ValueError):
        engine.create_session('not-yet', 'doc', algorithm='local', model=None)
    with pytest.raises(ValueError):
        engine.create_session('not-yet', 'doc', algorithm='sliding_queue', model='bge-m3')
    with pytest.raises(ValueError):
        engine.create_session('not-yet', 'doc', algorithm='sliding_queue', model='invalid')


def test_default_sliding_queue_keeps_only_latest_15_words():
    from app.schemas.models import SessionConfig
    assert SessionConfig().sliding_window_words == 15
    assert SearchSettings().sliding_window_words == 15

    engine = SearchEngine()
    engine.register_document('window15', [block(1, '최신 발화 검색')])
    engine.create_session('window15_sess', 'window15')
    engine.search('window15_sess', ' '.join(f'word{i}' for i in range(20)))
    assert engine.get_buffer_words('window15_sess') == [
        f'word{i}' for i in range(5, 20)
    ]


def test_interim_preview_does_not_duplicate_words_or_commit_page():
    engine = sample_engine(window=3)
    assert engine.get_current_page('session') == 1
    first = engine.preview_search('session', 'DISTINCT')
    second = engine.preview_search('session', 'DISTINCT 중복 제거')
    assert first.status == 'MATCH' and second.page == 2
    # 브라우저 STT가 같은 문장을 여러 번 고쳐도 확정 큐는 비어 있다.
    assert engine.get_current_page('session') == 1
    assert engine.get_buffer_words('session') == []
    assert engine.get_timeline('session') == []

    result = engine.search('session', 'DISTINCT 중복 제거', 'seg_1')
    assert result.page == 2
    assert engine.get_buffer_words('session') == ['distinct', '중복', '제거']
    assert len(engine.get_timeline('session')) == 1

    # 이어서 들어온 미확정 문장은 기존 3단어만 임시로 밀어내고 복원한다.
    engine.preview_search('session', '정규화 종속성 테이블')
    assert engine.get_buffer_words('session') == ['distinct', '중복', '제거']
    assert engine.get_current_page('session') == 2

