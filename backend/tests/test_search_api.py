import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.search import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def test_api_roundtrip():
    block = {
        'block_id': 'p0001_b0001', 'page': 1,
        'text': '데이터베이스 정규화의 목적', 'block_type': 'paragraph',
        'bbox': [0.1, 0.1, 0.8, 0.2], 'rects': [[0.1, 0.1, 0.8, 0.2]],
        'keywords': ['정규화'],
    }
    assert client.post('/api/v1/search/documents/register', json={
        'document_id': 'test_api_doc', 'blocks': [block],
    }).status_code == 201
    session = client.post('/api/v1/sessions', json={
        'document_id': 'test_api_doc', 'session_id': 'test_api_sess',
        'config': {'search_strategy': 'sliding_queue', 'search_model': None},
    })
    assert session.status_code == 201, session.text
    result = client.post('/api/v1/search', json={
        'session_id': 'test_api_sess', 'segment_id': 'seg_1',
        'text': '정규화 목적',
    })
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'MATCH'
    assert result.json()['page'] == 1
    state = client.get('/api/v1/sessions/test_api_sess/search-state').json()
    assert state['current_page'] == 1
    assert state['buffer_words'] == ['정규화', '목적']
    assert client.post('/api/v1/sessions/test_api_sess/reset-search').status_code == 200
    assert client.get('/api/v1/sessions/test_api_sess/search-state').json()['buffer_words'] == []


def test_sliding_queue_e5_words_can_be_enabled_and_disabled(monkeypatch):
    from app.search.models.embeddings import E5SmallModel
    prepared, thresholds = [], []
    def prepare(self, document_id, terms):
        prepared.append((document_id, tuple(terms)))
    def match(self, terms, threshold):
        thresholds.append(threshold)
        pairs = {'검색': frozenset(['탐색']), '삭제': frozenset(['제거'])}
        return {term: pairs[term] for term in terms if term in pairs}
    monkeypatch.setattr(E5SmallModel, 'prepare_terms', prepare)
    monkeypatch.setattr(E5SmallModel, 'match_terms', match)
    monkeypatch.setattr(E5SmallModel, 'rank', lambda *args: pytest.fail('paragraph ranking is not used'))
    doc_id = 'e5_word_api_doc'
    assert client.post('/api/v1/search/documents/register', json={
        'document_id': doc_id, 'blocks': [{
            'block_id': f'e5word-{page}', 'page': page, 'text': text,
            'block_type': 'paragraph', 'bbox': [0, 0, 1, 1], 'rects': [],
        } for page, text in [(1, '소개'), (2, '탐색 제거')]],
    }).status_code == 201
    options = client.get('/api/v1/search/options').json()
    assert options['default_algorithm'] == 'sliding_queue' and options['default_model'] is None
    assert next(x for x in options['algorithms'] if x['name'] == 'sliding_queue')['supported_models'] == [None, 'e5-small']
    for model in ('e5-small', None):
        created = client.post('/api/v1/sessions', json={'document_id': doc_id,
            'config': {'search_strategy': 'sliding_queue', 'search_model': model, 'word_match_threshold': .92}})
        assert created.status_code == 201, created.text
        sid = created.json()['session_id']
        preview = client.post('/api/v1/search/preview', json={'session_id': sid, 'text': '검색 삭제'}).json()
        assert preview['model'] == (model or 'none')
        assert client.get(f'/api/v1/sessions/{sid}/search-state').json()['current_page'] == 1
        result = client.post('/api/v1/search/live', json={
            'session_id': sid, 'utterance_id': 'u1', 'sequence': 1, 'is_final': True, 'text': '검색 삭제'})
        assert result.status_code == 200, result.text
        assert result.json()['page'] == (2 if model else None)
        assert result.json()['buffer_words'] == ['검색', '삭제']
    assert prepared and thresholds and set(thresholds) == {.92}


def test_api_missing_session_and_missing_model():
    client.post('/api/v1/search/documents/register', json={
        'document_id': 'test_api_doc_for_future',
        'blocks': [{
            'block_id': 'p0001_b0001', 'page': 1, 'text': '테스트 문서',
            'block_type': 'paragraph', 'bbox': [0.1, 0.1, 0.8, 0.2],
            'rects': [[0.1, 0.1, 0.8, 0.2]],
        }],
    })
    response = client.post('/api/v1/search', json={'session_id': 'missing', 'text': '검색'})
    assert response.status_code == 404
    response = client.post('/api/v1/sessions', json={
        'document_id': 'test_api_doc_for_future', 'config': {'search_strategy': 'global', 'search_model': None},
    })
    assert response.status_code == 400


def test_api_semantic_route_with_stub_model(monkeypatch):
    import app.search.engine as engine_module
    from app.search.models.base import ModelScore
    from app.search.tokenizer import tokenize

    class StubModel:
        name = 'e5-small'
        kind = 'embedding'
        def prepare(self, document_id, blocks):
            pass
        def rank(self, text, blocks):
            tokens = set(tokenize(text))
            return [ModelScore(b.block_id, 0.95 if tokens & set(tokenize(b.text)) else 0.01)
                    for b in blocks]

    monkeypatch.setattr(engine_module, 'load_model', lambda _: StubModel())
    assert client.post('/api/v1/search/documents/register', json={
        'document_id': 'test_semantic_doc', 'blocks': [{
            'block_id': 'b1', 'page': 1, 'text': '데이터베이스 정규화',
            'block_type': 'paragraph', 'bbox': [0.1, 0.1, .8, .2], 'rects': [],
        }, {
            'block_id': 'b12', 'page': 12, 'text': '멀리 떨어진 메모리 관리',
            'block_type': 'paragraph', 'bbox': [0.1, 0.1, .8, .2], 'rects': [],
        }],
    }).status_code == 201
    creation = client.post('/api/v1/sessions', json={
        'document_id': 'test_semantic_doc', 'session_id': 'test_semantic_session',
        'config': {'search_strategy': 'hybrid1', 'search_model': 'e5-small',
                   'local_radius_pages': 2, 'min_match_score': .3},
    })
    assert creation.status_code == 201, creation.text
    found = client.post('/api/v1/search', json={
        'session_id': 'test_semantic_session', 'segment_id': 's-1', 'text': '메모리 관리',
    })
    assert found.status_code == 200, found.text
    assert found.json()['page'] == 12
    assert found.json()['stage_used'] == 'global'
    assert found.json()['model'] == 'e5-small'


def test_api_jev_without_key_returns_unavailable(monkeypatch):
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    client.post('/api/v1/search/documents/register', json={
        'document_id': 'test_jev_doc', 'blocks': [{
            'block_id': 'b1', 'page': 1, 'text': '임베딩 모델',
            'block_type': 'paragraph', 'bbox': [0, 0, 1, 1], 'rects': [],
        }],
    })
    response = client.post('/api/v1/sessions', json={
        'document_id': 'test_jev_doc',
        'config': {'search_strategy': 'global', 'search_model': 'jev'},
    })
    assert response.status_code == 503
    assert 'TYPESAFE_API_KEY' in response.json()['detail']


def test_api_preview_is_provisional_only():
    client.post('/api/v1/search/documents/register', json={
        'document_id': 'preview_api_doc',
        'blocks': [{
            'block_id': 'b1', 'page': 1, 'text': '운영체제 프로세스 관리',
            'block_type': 'paragraph', 'bbox': [0.1, 0.1, 0.8, 0.2],
            'rects': [[0.1, 0.1, 0.8, 0.2]], 'keywords': [],
        }],
    })
    session = client.post('/api/v1/sessions', json={
        'document_id': 'preview_api_doc',
        'config': {'search_strategy': 'sliding_queue', 'sliding_window_words': 15},
    })
    assert session.status_code == 201, session.text
    session_id = session.json()['session_id']
    for partial in ('운영체제', '운영체제 프로세스', '운영체제 프로세스 관리'):
        preview = client.post('/api/v1/search/preview', json={
            'session_id': session_id, 'text': partial, 'segment_id': None,
        })
        assert preview.status_code == 200, preview.text
    state = client.get(f'/api/v1/sessions/{session_id}/search-state').json()
    assert state['buffer_words'] == []
    assert client.get(f'/api/v1/sessions/{session_id}/timeline').json()['items'] == []
    final = client.post('/api/v1/search', json={
        'session_id': session_id, 'text': '운영체제 프로세스 관리',
        'segment_id': None,
    })
    assert final.status_code == 200
    assert client.get(f'/api/v1/sessions/{session_id}/search-state').json()['buffer_words'] == [
        '운영', '체제', '프로세스', '관리',
    ]



def test_live_api_updates_actual_search_queue_on_interim():
    doc_id = 'live_api_doc'
    assert client.post('/api/v1/search/documents/register', json={
        'document_id': doc_id, 'blocks': [{
            'block_id': 'live-b1', 'page': 1, 'text': '운영체제 프로세스',
            'block_type': 'paragraph', 'bbox': [0, 0, 1, 1], 'rects': [],
        }],
    }).status_code == 201
    created = client.post('/api/v1/sessions', json={'document_id': doc_id,
        'config': {'search_strategy': 'sliding_queue', 'sliding_window_words': 15}})
    sid = created.json()['session_id']
    for seq, text in enumerate(['운영체제는', '운영체제는 프로세스입니다'], start=1):
        result = client.post('/api/v1/search/live', json={
            'session_id': sid, 'utterance_id': 'utt1',
            'text': text, 'sequence': seq, 'is_final': False,
        })
        assert result.status_code == 200
    result = result.json()
    assert result['buffer_words'] == ['운영', '체제', '프로세스']
    state = client.get(f'/api/v1/sessions/{sid}/search-state').json()
    assert state['buffer_words'] == ['운영', '체제', '프로세스']
    assert state['current_page'] == 1
    assert client.get(f'/api/v1/sessions/{sid}/timeline').json()['items'] == []

    final = client.post('/api/v1/search/live', json={
        'session_id': sid, 'utterance_id': 'utt1',
        'text': '운영체제는 프로세스입니다', 'sequence': 3, 'is_final': True,
    })
    assert final.status_code == 200
    assert final.json()['buffer_words'] == ['운영', '체제', '프로세스']
    assert len(client.get(f'/api/v1/sessions/{sid}/timeline').json()['items']) == 1
