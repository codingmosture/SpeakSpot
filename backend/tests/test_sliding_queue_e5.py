"""E5 changes individual word equality; queue and transition rules stay intact."""
import pytest

from app.schemas.models import Block
from app.search import SearchEngine, SearchSettings
import app.search.engine as engine_module


class WordModel:
    name = 'e5-small'
    kind = 'embedding'

    def __init__(self):
        self.calls = []

    def prepare(self, *args):
        pytest.fail('Sliding queue must not prepare paragraph vectors')

    def rank(self, *args):
        pytest.fail('Sliding queue must not rank paragraphs')

    def prepare_terms(self, document_id, terms):
        self.terms = tuple(terms)

    def match_terms(self, terms, threshold):
        self.calls.append((tuple(terms), threshold))
        pairs = {'검색': '탐색', '삭제': '제거'}
        return {term: frozenset([pairs[term]]) for term in terms if term in pairs and threshold <= .93}


def make_engine(monkeypatch, **settings):
    model = WordModel()
    monkeypatch.setattr(engine_module, 'load_model', lambda name: model if name == 'e5-small' else None)
    engine = SearchEngine()
    engine.register_document('doc', [
        Block(block_id=f'b{page}', page=page, text=text, block_type='paragraph',
              bbox=(.1, .1, .9, .2), rects=[(.1, .1, .9, .2)])
        for page, text in [(1, '소개'), (2, '탐색 제거'), (3, '정규화 종속성'), (9, '탐색 제거')]
    ])
    engine.create_session('s', 'doc', model='e5-small', settings=SearchSettings(**settings))
    return engine, model


def test_only_word_comparison_changes_and_original_queue_remains(monkeypatch):
    engine, model = make_engine(monkeypatch)
    engine.create_session('baseline', 'doc')
    assert engine.search('baseline', '검색 삭제').status == 'NONE'
    result = engine.search('s', '검색 삭제', 'u1')
    assert (result.page, result.model, result.strategy, result.stage_used) == (
        2, 'e5-small', 'sliding_queue', 'sliding_queue')
    assert '탐색' in model.terms and '제거' in model.terms
    assert engine.get_buffer_words('s') == ['검색', '삭제']
    assert model.calls == [(('검색', '삭제'), .90)]


def test_semantic_word_support_confirms_growing_interim(monkeypatch):
    engine, _ = make_engine(monkeypatch)
    first, _, page = engine.live_search('s', '검색 삭제', 'u1', 1, False)
    assert first.status == 'NONE' and page == 1
    repeated, _, page = engine.live_search('s', '검색 삭제', 'u1', 2, False)
    assert repeated.status == 'NONE' and page == 1
    growing, words, page = engine.live_search('s', '검색 삭제 작업', 'u1', 3, False)
    assert growing.page == page == 2
    assert words == ['검색', '삭제', '작업']
    engine.live_search('s', '검색 삭제 작업', 'u1', 4, True)
    engine.live_search('s', '검색 삭제 작업', 'u1', 5, True)
    assert engine.get_buffer_words('s') == ['검색', '삭제', '작업']
    assert len(engine.get_timeline('s')) == 1


def test_final_uses_same_word_support_rules(monkeypatch):
    engine, _ = make_engine(monkeypatch)
    result, _, page = engine.live_search('s', '검색 삭제', 'u1', 1, True)
    assert result.page == page == 2


def test_preview_restores_model_word_matches_and_state(monkeypatch):
    engine, _ = make_engine(monkeypatch)
    engine.search('s', '정규화 종속성')
    algorithm = engine._get_session('s').algorithm
    before = dict(algorithm._term_matches)
    result = engine.preview_search('s', '검색 삭제')
    assert result.page == 2 and result.model == 'e5-small'
    assert engine.get_current_page('s') == 3
    assert engine.get_buffer_words('s') == ['정규화', '종속성']
    assert algorithm._term_matches == before
    engine.reset_session('s')
    assert not algorithm._term_matches and not engine.get_buffer_words('s')


def test_history_cannot_match_unrelated_latest_words(monkeypatch):
    engine, _ = make_engine(monkeypatch)
    engine.search('s', '검색 삭제')
    result, _, page = engine.live_search('s', '점심 메뉴', 'u2', 1, True)
    assert result.status == 'NONE' and page == 2


def test_window_radius_and_word_threshold_are_still_applied(monkeypatch):
    engine, model = make_engine(monkeypatch, sliding_window_words=2, local_radius_pages=0)
    assert engine.search('s', '검색 삭제 정규화 종속성').status == 'NONE'
    assert model.calls == [(('정규화', '종속성'), .90)]
    assert engine.get_current_page('s') == 1
    engine, _ = make_engine(monkeypatch, word_match_threshold=.99)
    assert engine.search('s', '검색 삭제').status == 'NONE'
    # Identical text still matches even when model similarity is below the threshold.
    assert engine.search('s', '탐색 제거').page == 2


def test_unsupported_models_rejected_before_loading(monkeypatch):
    engine, _ = make_engine(monkeypatch)
    monkeypatch.setattr(engine_module, 'load_model', lambda _: pytest.fail('reject before model loading'))
    with pytest.raises(ValueError, match='e5-small'):
        engine.create_session('bad', 'doc', model='bge-m3')


@pytest.mark.parametrize('threshold', [-.01, 1.01])
def test_word_threshold_range(threshold):
    with pytest.raises(ValueError):
        SearchSettings(word_match_threshold=threshold)


def test_retained_and_negative_terms_are_compared_once(monkeypatch):
    engine, model = make_engine(monkeypatch)
    for sequence, (text, final) in enumerate([
        ('검색 삭제', False), ('검색 삭제', False), ('검색 삭제 작업', False),
        ('검색 삭제 작업', True),
    ], 1):
        engine.live_search('s', text, 'u1', sequence, final)
    assert [terms for terms, _ in model.calls] == [('검색', '삭제'), ('작업',)]
    assert engine.get_buffer_words('s') == ['검색', '삭제', '작업']
    engine.search('s', '작업 작업')
    assert len(model.calls) == 2  # Empty/negative model results are cached too.


def test_hypothesis_correction_removes_the_old_word_contribution(monkeypatch):
    engine, model = make_engine(monkeypatch)
    engine.live_search('s', '검색 삭제', 'u1', 1, False)
    result, words, page = engine.live_search('s', '검색 점심', 'u1', 2, True)
    assert result.status == 'NONE' and page == 1
    assert words == ['검색', '점심']
    assert [terms for terms, _ in model.calls] == [('검색', '삭제'), ('점심',)]
    algorithm = engine._get_session('s').algorithm
    assert '삭제' not in algorithm._history_scores.counts
    assert algorithm._history_scores.totals[algorithm._target_index['b2'], 1] == 1


def test_sliding_expiration_and_duplicate_counts_update_without_new_comparison(monkeypatch):
    engine, model = make_engine(monkeypatch, sliding_window_words=3)
    engine.search('s', '검색 검색 삭제')
    engine.search('s', '삭제')
    algorithm = engine._get_session('s').algorithm
    assert engine.get_buffer_words('s') == ['검색', '삭제', '삭제']
    assert algorithm._history_scores.counts == {'검색': 1, '삭제': 2}
    assert len(model.calls) == 1
    engine.search('s', '점심 메뉴 식사')
    assert algorithm._history_scores.totals[:, 0].sum() == 0
    assert engine.search('s', '점심 메뉴 식사').status == 'NONE'
    assert [terms for terms, _ in model.calls] == [('검색', '삭제'), ('점심', '메뉴', '식사')]


def test_preview_restores_incremental_counts_and_weights(monkeypatch):
    import numpy as np
    engine, _ = make_engine(monkeypatch)
    engine.search('s', '검색 삭제')
    algorithm = engine._get_session('s').algorithm
    before = algorithm._history_scores.copy()
    engine.preview_search('s', '정규화 종속성')
    assert algorithm._history_scores.counts == before.counts
    assert algorithm._history_scores.weight == before.weight
    np.testing.assert_array_equal(algorithm._history_scores.totals, before.totals)
    engine.reset_session('s')
    assert not algorithm._history_scores.counts
    assert algorithm._history_scores.weight == 0
    assert algorithm._history_scores.totals.sum() == 0


def test_match_cache_is_bounded_and_active_terms_are_pinned(monkeypatch):
    engine, model = make_engine(monkeypatch, sliding_window_words=2)
    engine.search('s', '검색 삭제')
    for index in range(520):
        engine.search('s', f'unrelated{index}')
    algorithm = engine._get_session('s').algorithm
    assert len(algorithm._term_matches) <= 512
    active = algorithm._history_scores.counts.keys() | algorithm._latest_scores.counts.keys()
    assert active <= algorithm._term_matches.keys()
    assert '검색' not in algorithm._term_matches
    engine.search('s', '검색 삭제')
    assert model.calls[-1][0] == ('검색', '삭제')


@pytest.mark.parametrize('text,expected', [
    ('상품의 가격을 빠르게 검색합니다.', ('상품', '가격', '검색')),
    ('이것은 제가 그것을 하는 것이고 할 수 있습니다.', ()),
    ('먹고 마시고 예쁘고 빠르게 갑니다.', ()),
    ('함수적 종속성을 이용해 중복을 제거합니다.', ('함수적', '종속성', '이용', '중복', '제거')),
    ('SQL2003 employee_id word19 123 456', ('sql2003', 'employee_id', 'word19')),
])
def test_only_content_nouns_and_technical_identifiers_are_compared(text, expected):
    from app.search.lexical import lexical_terms
    assert lexical_terms(text) == expected


def test_queue_window_counts_nouns_instead_of_particles_or_verbs(monkeypatch):
    engine, model = make_engine(monkeypatch, sliding_window_words=3)
    engine.live_search('s', '상품의 가격을 빠르게 검색합니다.', 'u1', 1, True)
    assert engine.get_buffer_words('s') == ['상품', '가격', '검색']
    assert model.calls == [(('상품', '가격', '검색'), .90)]
    result, words, page = engine.live_search('s', '먹고 마시고 예쁘고 빠르게 갑니다.', 'u2', 2, True)
    assert result.status == 'NONE' and page == 1
    assert words == ['상품', '가격', '검색']
    assert len(model.calls) == 1  # No filler is encoded or evicts useful nouns.
    engine.live_search('s', '삭제합니다.', 'u3', 3, True)
    assert engine.get_buffer_words('s') == ['가격', '검색', '삭제']
    assert model.calls[-1] == (('삭제',), .90)


def test_filtered_hypothesis_correction_removes_abandoned_noun(monkeypatch):
    engine, model = make_engine(monkeypatch)
    engine.live_search('s', '빠르게 검색하고 삭제합니다.', 'u1', 1, False)
    result, words, page = engine.live_search('s', '빠르게 검색합니다.', 'u1', 2, True)
    assert result.status == 'NONE' and page == 1
    assert words == ['검색']
    assert model.calls == [(('검색', '삭제'), .90)]
    algorithm = engine._get_session('s').algorithm
    assert algorithm._history_scores.counts == {'검색': 1}
    assert algorithm._history_scores.totals[algorithm._target_index['b2'], 1] == 1


def test_reference_particles_are_checked_in_original_speech(monkeypatch):
    engine, _ = make_engine(monkeypatch)
    for sequence, text in enumerate(('앞에서 검색하고 삭제했습니다.', '나중에 검색하고 삭제합니다.'), 1):
        result, words, page = engine.live_search('s', text, f'u{sequence}', sequence, True)
        assert result.status == 'NONE' and page == 1
        assert not {'앞에서', '나중에', '하', '되'} & set(words)
    result, _, page = engine.live_search('s', '검색하고 삭제합니다.', 'now', 3, True)
    assert result.page == page == 2
