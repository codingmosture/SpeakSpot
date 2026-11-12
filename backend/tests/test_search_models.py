"""외부 모델 서버를 실제로 호출하지 않고 요청 스키마와 임베딩 접두어 테스트."""
import numpy as np
import pytest
import httpx
from app.schemas.models import Block
from app.search.models.jev import JevModel
from app.search.models.base import ModelRequestError, ModelUnavailableError
from app.search.models.embeddings import BGEM3Model, E5SmallModel
import app.search.models.embeddings as embeddings_module


def block(page=1):
    return Block(block_id='b1', page=page, text='데이터베이스 정규화',
                 block_type='paragraph', bbox=(0, 0, 1, 1), rects=[(0, 0, 1, 1)])


class Encoder:
    def __init__(self):
        self.seen = []
    def encode(self, texts, **kwargs):
        self.seen.append(texts)
        if isinstance(texts, str):
            return np.array([1., 0.], dtype=float)
        return np.array([[1., 0.] for _ in texts], dtype=float)


@pytest.mark.parametrize('class_,passage,query', [
    (E5SmallModel, 'passage: 데이터베이스 정규화', 'query: 정규화'),
    (BGEM3Model, '데이터베이스 정규화', '정규화'),
])
def test_embedding_model_real_encoder_interface_with_stub(monkeypatch, class_, passage, query):
    encoder = Encoder()
    monkeypatch.setattr(embeddings_module, '_load_encoder', lambda *_: encoder)
    embeddings_module._DOCUMENT_CACHE.clear()
    model = class_()
    model.prepare('doc_test', [block()])
    results = model.rank('정규화', [block()])
    assert results[0].block_id == 'b1'
    assert results[0].score == 1
    assert encoder.seen[0] == [passage]
    assert encoder.seen[1] == query
    model.rank('정규화', [block()])
    assert len(encoder.seen) == 2  # query cache


def test_jev_requires_key(monkeypatch):
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    with pytest.raises(ModelUnavailableError):
        JevModel()


def test_jev_choice_payload_and_none(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'mock-key')
    model = JevModel()
    captured = []
    def fake_post(url, json, headers, timeout):
        captured.append((url, json, headers, timeout))
        choice = 'b0' if len(captured) == 1 else 'OUT_OF_LOCAL'
        request = httpx.Request('POST', url)
        return httpx.Response(200, json={'answers': {'block': {
            'type': 'choice', 'choice': choice,
            'probabilities': {choice: .85}, 'confidence': .81,
        }}}, request=request)
    monkeypatch.setattr(httpx, 'post', fake_post)
    choice = model.choose('정규화', [block()], allow_out_of_local=True)
    assert choice.block_id == 'b1' and choice.confidence == .85
    assert captured[0][1]['questions']['block']['criteria']['b0'].startswith('페이지 1')
    assert captured[0][2]['Authorization'] == 'Bearer mock-key'
    other = model.choose('무관함', [block()], allow_out_of_local=True)
    assert other.block_id is None and other.reason == 'out_of_local'


def test_jev_invalid_choice_fails_loudly(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'mock-key')
    model = JevModel()
    def fake_post(url, json, headers, timeout):
        return httpx.Response(200, json={'answers': {'block': {'choice': 'not-a-candidate'}}},
                              request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx, 'post', fake_post)
    with pytest.raises(ModelRequestError):
        model.choose('정규화', [block()])
