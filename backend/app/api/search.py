"""실제 검색 로직용 API. 기존 /api/search Mock API는 유지한다.

블록 등록은 PDF 파서 담당 모듈과 연결하기 전까지 사용하는 임시 어댑터다.
운영 환경에서는 PDF 파서가 SearchEngine.register_document를 직접 호출한다.
"""
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.schemas.models import Block, SearchResult, SessionConfig
from app.search import SearchEngine, SearchSettings
from app.search.registry import ALGORITHMS, MODELS
from app.search.models.base import ModelRequestError, ModelUnavailableError

router = APIRouter(prefix='/api/v1', tags=['Search'])
engine = SearchEngine()


class RegisterDocumentRequest(BaseModel):
    document_id: str = Field(min_length=1)
    blocks: list[Block] = Field(min_length=1)


class CreateSessionRequest(BaseModel):
    document_id: str = Field(min_length=1)
    session_id: str | None = None
    config: SessionConfig = Field(default_factory=SessionConfig)


class SearchRequest(BaseModel):
    session_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    segment_id: str | None = None


@router.get('/search/options')
def list_search_options():
    return {
        'algorithms': [
            {'name': name, 'implemented': True, 'requires_model': name != 'sliding_queue'}
            for name in ALGORITHMS
        ],
        'models': [
            {'name': name, 'implemented': True, 'requires_setup': True}
            for name in MODELS
        ],
        'default_algorithm': 'sliding_queue',
        'default_model': None,
    }


@router.post('/search/documents/register', status_code=201)
def register_document(request: RegisterDocumentRequest):
    try:
        engine.register_document(request.document_id, request.blocks)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {'document_id': request.document_id, 'block_count': len(request.blocks)}


@router.post('/sessions', status_code=201)
def create_session(request: CreateSessionRequest):
    session_id = request.session_id or f'sess_{uuid4().hex[:12]}'
    config = request.config
    settings = SearchSettings(
        sliding_window_words=config.sliding_window_words,
        local_radius_pages=config.local_radius_pages,
        min_match_score=config.min_match_score,
        backward_penalty=config.backward_penalty,
        stay_bonus=config.stay_bonus,
        switch_margin=config.switch_margin,
        prefix_bonus=config.prefix_bonus,
        min_margin=config.min_margin,
        global_top_k_pages=config.global_top_k_pages,
        jev_min_confidence=config.jev_min_confidence,
        max_jev_candidates=config.max_jev_candidates,
    )
    try:
        engine.create_session(
            session_id, request.document_id, config.search_strategy,
            config.search_model, settings,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ModelUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ModelRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {'session_id': session_id, 'document_id': request.document_id, 'status': 'created'}


@router.post('/search', response_model=SearchResult)
def search(request: SearchRequest):
    try:
        return engine.search(request.session_id, request.text, request.segment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ModelUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ModelRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get('/sessions/{session_id}/search-state')
def get_search_state(session_id: str):
    try:
        return {
            'session_id': session_id,
            'current_page': engine.get_current_page(session_id),
            'buffer_words': engine.get_buffer_words(session_id),
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post('/sessions/{session_id}/reset-search')
def reset_search(session_id: str):
    try:
        engine.reset_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {'session_id': session_id, 'current_page': 1, 'status': 'reset'}
