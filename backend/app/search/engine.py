"""문서/세션별 독립적인 검색 엔진 (단일 프로세스 인메모리 MVP)."""
from dataclasses import dataclass, field
from threading import RLock
from time import perf_counter
from typing import Sequence

from app.schemas.models import Block, SearchResult
from app.search.algorithms.base import SearchAlgorithm
from app.search.config import SearchSettings
from app.search.registry import create_algorithm, load_model, ALGORITHMS


@dataclass
class SearchSession:
    document_id: str
    algorithm: SearchAlgorithm
    results_by_segment: dict[str, SearchResult] = field(default_factory=dict)
    lock: RLock = field(default_factory=RLock)


class SearchEngine:
    def __init__(self) -> None:
        self._documents: dict[str, tuple[Block, ...]] = {}
        self._sessions: dict[str, SearchSession] = {}
        self._lock = RLock()

    def register_document(self, document_id: str, blocks: Sequence[Block]) -> None:
        if not document_id.strip() or not blocks:
            raise ValueError('document_id와 하나 이상의 블록이 필요합니다.')
        ids = [block.block_id for block in blocks]
        if len(ids) != len(set(ids)):
            raise ValueError('block_id는 문서 내에서 유일해야 합니다.')
        with self._lock:
            if any(s.document_id == document_id for s in self._sessions.values()):
                raise ValueError('사용 중인 문서는 세션을 종료하기 전까지 교체할 수 없습니다.')
            self._documents[document_id] = tuple(blocks)

    def create_session(
        self, session_id: str, document_id: str, algorithm: str = 'sliding_queue',
        model: str | None = None, settings: SearchSettings | None = None,
    ) -> None:
        with self._lock:
            if not session_id.strip():
                raise ValueError('session_id는 비어 있을 수 없습니다.')
            if session_id in self._sessions:
                raise ValueError('이미 존재하는 session_id입니다.')
            if document_id not in self._documents:
                raise KeyError(f'등록되지 않은 document_id: {document_id}')
            if algorithm == 'hybrid':  # 이전 실험 API와의 호환 별칭
                algorithm = 'hybrid1'
            if algorithm not in ALGORITHMS:
                raise ValueError(f'알 수 없는 알고리즘: {algorithm}')
            if algorithm == 'sliding_queue' and model not in (None, 'none'):
                raise ValueError('sliding_queue는 모델 없이 실행합니다.')
            if algorithm != 'sliding_queue' and model in (None, 'none'):
                raise ValueError(f'{algorithm} 알고리즘은 검색 모델을 지정해야 합니다.')
            loaded_model = load_model(model)
            selected = create_algorithm(
                algorithm, self._documents[document_id], settings or SearchSettings(), loaded_model,
            )
            if loaded_model is not None:
                loaded_model.prepare(document_id, self._documents[document_id])
            self._sessions[session_id] = SearchSession(document_id, selected)

    def search(self, session_id: str, text: str, segment_id: str | None = None) -> SearchResult:
        if not text.strip():
            raise ValueError('발화 text는 비어 있을 수 없습니다.')
        session = self._get_session(session_id)
        with session.lock:
            if segment_id is not None and segment_id in session.results_by_segment:
                return session.results_by_segment[segment_id]
            start = perf_counter()
            decision = session.algorithm.search(text)
            elapsed = (perf_counter() - start) * 1000
            result = SearchResult(
                status=decision.status,
                document_id=session.document_id,
                session_id=session_id,
                segment_id=segment_id,
                page=decision.page,
                block_id=decision.block_id,
                score=decision.score,
                strategy=session.algorithm.name,
                stage_used=decision.stage_used,
                model=session.algorithm.model.name if session.algorithm.model is not None else 'none',
                processing_ms=round(elapsed, 3),
            )
            if segment_id is not None:
                session.results_by_segment[segment_id] = result
            return result

    def reset_session(self, session_id: str) -> None:
        session = self._get_session(session_id)
        with session.lock:
            session.algorithm.reset()
            session.results_by_segment.clear()

    def get_current_page(self, session_id: str) -> int:
        session = self._get_session(session_id)
        with session.lock:
            return session.algorithm.current_page

    def get_buffer_words(self, session_id: str) -> list[str]:
        session = self._get_session(session_id)
        with session.lock:
            return list(getattr(session.algorithm, 'buffer_words', ()))

    def _get_session(self, session_id: str) -> SearchSession:
        with self._lock:
            if session_id not in self._sessions:
                raise KeyError(f'등록되지 않은 session_id: {session_id}')
            return self._sessions[session_id]
