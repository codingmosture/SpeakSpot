"""모델 계약. 임베딩 랭커와 Jev 같은 선택 모델은 서로 다른 기능을 제공한다."""
from dataclasses import dataclass
from typing import Literal, Protocol, Sequence
from app.schemas.models import Block


@dataclass(frozen=True, slots=True)
class ModelScore:
    block_id: str
    score: float  # cosine similarity clipped to [0, 1] (probability 아님)


@dataclass(frozen=True, slots=True)
class ModelChoice:
    block_id: str | None  # None = NONE / OUT_OF_LOCAL
    confidence: float | None = None
    reason: str = 'selected'


class SearchModel(Protocol):
    name: str
    kind: Literal['embedding', 'choice']
    def prepare(self, document_id: str, blocks: Sequence[Block]) -> None: ...


class EmbeddingModel(SearchModel, Protocol):
    kind: Literal['embedding']
    def rank(self, query: str, candidates: Sequence[Block]) -> list[ModelScore]: ...


class ChoiceModel(SearchModel, Protocol):
    kind: Literal['choice']
    def choose(self, query: str, candidates: Sequence[Block], *, allow_out_of_local: bool = False) -> ModelChoice: ...


class ModelUnavailableError(RuntimeError):
    """필수 의존성/인증 설정이 없어서 모델을 사용할 수 없음."""


class ModelRequestError(RuntimeError):
    """실제 모델 추론 요청 실패. 조용히 다른 모델로 대체하지 않는다."""
