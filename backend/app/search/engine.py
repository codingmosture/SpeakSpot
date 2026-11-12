"""문서/세션별 독립적인 검색 엔진 (단일 프로세스 인메모리 MVP)."""
from dataclasses import dataclass, field
from threading import RLock
from time import perf_counter
from typing import Sequence

from app.schemas.models import Block, SearchResult
from app.search.algorithms.base import SearchAlgorithm
from app.search.algorithms.sliding_queue import SlidingQueueSearch
from app.search.config import SearchSettings
from app.search.registry import create_algorithm, load_model, ALGORITHMS


@dataclass
class SearchSession:
    document_id: str
    algorithm: SearchAlgorithm
    results_by_segment: dict[str, SearchResult] = field(default_factory=dict)
    timeline: list[dict] = field(default_factory=list)
    started_at: float = field(default_factory=perf_counter)
    lock: RLock = field(default_factory=RLock)
    last_live_sequence: int = -1


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

    def search(self, session_id: str, text: str, segment_id: str | None = None,
               time_ms: int | None = None) -> SearchResult:
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
            session.timeline.append({
                'segment_id': segment_id,
                'time_ms': time_ms if time_ms is not None else int((perf_counter() - session.started_at) * 1000),
                'status': result.status, 'page': result.page, 'block_id': result.block_id,
            })
            return result

    def live_search(self, session_id: str, text: str, utterance_id: str,
                    sequence: int, is_final: bool) -> tuple[SearchResult, list[str], int]:
        """STT 결과마다 바로 위치 추적. final 여부와 무관하게 current_page 갱신.

        Web Speech API는 같은 발화를 여러 번 수정해 보낸다. sliding_queue는
        미확정 suffix를 교체하므로 단어 중복 없이 정확히 최근 N개만 유지한다.
        확정 이벤트만 타임라인에 적고, 클라이언트 순서가 뒤바뀐 이벤트는 버린다.
        """
        if not text.strip() or not utterance_id.strip():
            raise ValueError('발화와 utterance_id가 필요합니다.')
        session = self._get_session(session_id)
        with session.lock:
            # 같은 확정 이벤트가 다시 도착해도 큐에 중복 반영하지 않는다.
            if is_final and utterance_id in session.results_by_segment:
                result = session.results_by_segment[utterance_id]
                return result, list(getattr(session.algorithm, 'buffer_words', ())), session.algorithm.current_page
            if sequence <= session.last_live_sequence:
                raise ValueError('오래된 실시간 STT 이벤트입니다.')
            session.last_live_sequence = sequence

            started = perf_counter()
            if isinstance(session.algorithm, SlidingQueueSearch):
                decision = session.algorithm.stream_update(text, is_final)
            else:
                # 임베딩 모델 탐색은 누적 단어 큐가 없고, 입력 문장을 즉시 재탐색한다.
                decision = session.algorithm.search(text)
            elapsed = (perf_counter() - started) * 1000
            result = SearchResult(
                status=decision.status, document_id=session.document_id,
                session_id=session_id, segment_id=utterance_id,
                page=decision.page, block_id=decision.block_id, score=decision.score,
                strategy=session.algorithm.name, stage_used=decision.stage_used,
                model=session.algorithm.model.name if session.algorithm.model else 'none',
                processing_ms=round(elapsed, 3),
            )
            if is_final:
                session.results_by_segment[utterance_id] = result
                session.timeline.append({
                    'segment_id': utterance_id,
                    'time_ms': int((perf_counter() - session.started_at) * 1000),
                    'status': result.status, 'page': result.page, 'block_id': result.block_id,
                })
            return (result, list(getattr(session.algorithm, 'buffer_words', ())),
                    session.algorithm.current_page)

    def preview_search(self, session_id: str, text: str) -> SearchResult:
        """브라우저 중간 STT 결과: 잠정 하이라이트만 계산하고 큐/페이지/타임라인은 그대로 둔다."""
        if not text.strip():
            raise ValueError('발화 text는 비어 있을 수 없습니다.')
        session = self._get_session(session_id)
        with session.lock:
            if not isinstance(session.algorithm, SlidingQueueSearch):
                raise ValueError('실시간 미확정 발화 탐색은 sliding_queue에서만 지원합니다.')
            started = perf_counter()
            decision = session.algorithm.preview(text)
            elapsed = (perf_counter() - started) * 1000
            return SearchResult(
                status=decision.status, document_id=session.document_id,
                session_id=session_id, segment_id=None, page=decision.page,
                block_id=decision.block_id, score=decision.score,
                strategy=session.algorithm.name, stage_used=decision.stage_used,
                model='none', processing_ms=round(elapsed, 3),
            )

    def reset_session(self, session_id: str) -> None:
        session = self._get_session(session_id)
        with session.lock:
            session.algorithm.reset()
            session.results_by_segment.clear()
            session.last_live_sequence = -1
            session.timeline.clear()
            session.started_at = perf_counter()

    def get_timeline(self, session_id: str) -> list[dict]:
        session = self._get_session(session_id)
        with session.lock:
            return [dict(item) for item in session.timeline]

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
