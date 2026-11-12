import pytest

from app.schemas.models import Block
from app.search import SearchEngine
from app.search.lexical import lexical_terms


def make_engine():
    engine = SearchEngine()
    texts = ['강의 소개', '인덱스 탐색 속도 병렬 성능', '정규화 종속성 분해',
             'REFERENCES 키워드 다음에 제시']
    engine.register_document('doc', [
        Block(block_id=f'p{page}_b1', page=page, text=text, block_type='paragraph',
              bbox=(0.1, 0.1, 0.8, 0.3), rects=[(0.1, 0.1, 0.8, 0.3)])
        for page, text in enumerate(texts, 1)
    ])
    engine.create_session('s', 'doc')
    return engine


@pytest.mark.parametrize('text', ['그다음에', '그 다음에', '다음은', '그래서', '음', '어'])
def test_discourse_only_speech_has_no_search_evidence(text):
    assert lexical_terms(text) == ()
    engine = make_engine()
    result, _, page = engine.live_search('s', text, 'u', 1, False)
    assert result.status == 'NONE' and page == 1


def test_repeated_identical_interims_never_confirm_a_page_change():
    engine = make_engine()
    for sequence in range(1, 8):
        result, _, page = engine.live_search('s', '인덱스 탐색', 'u', sequence, False)
        assert result.status == 'NONE' and page == 1
    result, _, page = engine.live_search('s', '인덱스 탐색', 'u', 8, True)
    assert result.page == page == 2


def test_growing_coherent_speech_confirms_before_final():
    engine = make_engine()
    engine.live_search('s', '인덱스 탐색', 'u', 1, False)
    result, _, page = engine.live_search('s', '인덱스 탐색 속도', 'u', 2, False)
    assert result.page == page == 2
    assert engine.get_timeline('s') == []


def test_rewritten_hypothesis_for_same_page_is_not_confirmation():
    engine = make_engine()
    engine.live_search('s', '인덱스 탐색', 'u', 1, False)
    result, _, page = engine.live_search('s', '병렬 인덱스 성능', 'u', 2, False)
    assert result.status == 'NONE' and page == 1


def test_new_utterance_cannot_confirm_an_abandoned_interim():
    engine = make_engine()
    engine.live_search('s', '인덱스 탐색', 'abandoned', 1, False)
    result, _, page = engine.live_search('s', '인덱스 탐색 속도', 'new', 2, False)
    assert result.status == 'NONE' and page == 1
    result, _, page = engine.live_search('s', '인덱스 탐색 속도', 'new', 3, True)
    assert result.page == page == 2


def test_finalized_utterance_cannot_replay_an_old_page_as_an_interim():
    engine = make_engine()
    engine.live_search('s', '인덱스 탐색 속도', 'old', 1, True)
    engine.live_search('s', '정규화 종속성 분해', 'new', 2, True)
    words = engine.get_buffer_words('s')
    result, after, page = engine.live_search('s', '인덱스 탐색', 'old', 3, False)
    assert result.status == 'NONE' and page == 3
    assert after == words
    assert len(engine.get_timeline('s')) == 2


def test_preview_does_not_discard_a_pending_transition():
    engine = make_engine()
    engine.live_search('s', '인덱스 탐색', 'u', 1, False)
    engine.preview_search('s', '정규화 종속성 분해')
    result, _, page = engine.live_search('s', '인덱스 탐색 속도', 'u', 2, False)
    assert result.page == page == 2
