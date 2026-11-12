"""진짜 모델을 다운로드하지 않고도 전략 4종과 세션 처리 검증."""
from collections import Counter
from app.schemas.models import Block
from app.search import SearchEngine, SearchSettings
import app.search.engine as search_engine_module
from app.search.models.base import ModelScore, ModelChoice
from app.search.tokenizer import tokenize


def block(page: int, text: str) -> Block:
    return Block(block_id=f'b{page}', page=page, text=text, block_type='paragraph',
                 bbox=(0.1, 0.1, 0.9, 0.2), rects=[(0.1, 0.1, 0.9, 0.2)])


class FakeEmbedding:
    name = 'fake-embedding'
    kind = 'embedding'
    def prepare(self, document_id, blocks):
        self.prepared = True
        self.blocks = list(blocks)
    def rank(self, query, candidates):
        assert self.prepared
        q = set(tokenize(query))
        return sorted([
            ModelScore(b.block_id, 0.93 if set(tokenize(b.text)) & q else 0.01)
            for b in candidates
        ], key=lambda s: -s.score)


class FakeChoice:
    name = 'jev'
    kind = 'choice'
    max_candidates = 240
    def prepare(self, document_id, blocks):
        self.prepared = True
    def choose(self, query, candidates, *, allow_out_of_local=False):
        for b in candidates:
            if set(tokenize(query)) & set(tokenize(b.text)):
                return ModelChoice(b.block_id, .90)
        return ModelChoice(None, .9, 'out_of_local' if allow_out_of_local else 'none')


def make_engine(monkeypatch, algorithm, model, radius=1):
    monkeypatch.setattr(search_engine_module, 'load_model', lambda _: model)
    engine = SearchEngine()
    engine.register_document('doc', [block(1, '시작 소개'), block(2, 'DISTINCT 중복 제거'),
                                     block(3, '정규화 함수 종속성'), block(8, '운영체제 메모리 가상 페이지')])
    engine.create_session('s', 'doc', algorithm=algorithm, model=model.name,
                          settings=SearchSettings(local_radius_pages=radius, min_match_score=.4,
                                                  min_margin=0, stay_bonus=0, backward_penalty=0,
                                                  switch_margin=0, global_top_k_pages=2))
    return engine


def test_local_cannot_jump_outside_radius(monkeypatch):
    e = make_engine(monkeypatch, 'local', FakeEmbedding())
    r = e.search('s', '운영체제 메모리')
    assert r.status == 'NONE'
    assert e.get_current_page('s') == 1
    assert e.search('s', 'DISTINCT').page == 2


def test_global_searches_all_blocks_not_top_five(monkeypatch):
    e = make_engine(monkeypatch, 'global', FakeEmbedding())
    r = e.search('s', '운영체제 메모리')
    assert (r.status, r.page, r.stage_used) == ('MATCH', 8, 'global')


def test_hybrid1_fallback_after_local_none(monkeypatch):
    e = make_engine(monkeypatch, 'hybrid1', FakeEmbedding())
    assert e.search('s', '운영체제').stage_used == 'global'
    e.reset_session('s')
    r = e.search('s', 'DISTINCT')
    assert (r.page, r.stage_used) == (2, 'local')


def test_hybrid2_merges_local_and_global_candidates(monkeypatch):
    e = make_engine(monkeypatch, 'hybrid2', FakeEmbedding())
    r = e.search('s', '운영체제')
    assert (r.page, r.stage_used) == (8, 'global')
    e.reset_session('s')
    assert e.search('s', 'DISTINCT').stage_used == 'local'


def test_choice_model_global_jump_and_local_none(monkeypatch):
    e = make_engine(monkeypatch, 'hybrid1', FakeChoice())
    r = e.search('s', '운영체제')
    assert r.status == 'MATCH' and r.page == 8
    assert r.model == 'jev'
    e.reset_session('s')
    e2 = make_engine(monkeypatch, 'local', FakeChoice())
    assert e2.search('s', '운영체제').status == 'NONE'


def test_semantic_none_does_not_move_and_segment_replay_is_safe(monkeypatch):
    e = make_engine(monkeypatch, 'global', FakeEmbedding())
    r = e.search('s', 'DISTINCT', 'seg1')
    assert r.page == 2
    again = e.search('s', '메모리', 'seg1')
    assert again == r
    assert e.get_current_page('s') == 2
    no = e.search('s', '문서에 없는 엉뚱한 단어')
    assert no.status == 'NONE' and e.get_current_page('s') == 2
    e.reset_session('s')
    assert e.get_current_page('s') == 1


def test_all_strategies_accept_jev(monkeypatch):
    for strategy in ('local', 'global', 'hybrid1', 'hybrid2'):
        e = make_engine(monkeypatch, strategy, FakeChoice())
        r = e.search('s', 'DISTINCT')
        assert r.status == 'MATCH', strategy
        assert r.page == 2, strategy
