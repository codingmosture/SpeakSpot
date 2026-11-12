"""탐색 알고리즘 계약. 모든 알고리즘이 같은 검색 결과를 반환한다."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal, Sequence

from app.schemas.models import Block
from app.search.config import SearchSettings
from app.search.models.base import SearchModel


@dataclass(frozen=True, slots=True)
class AlgorithmResult:
    status: Literal['MATCH', 'NONE']
    page: int | None = None
    block_id: str | None = None
    score: float | None = None
    stage_used: str = 'none'


class SearchAlgorithm(ABC):
    name: str

    def __init__(
        self,
        blocks: Sequence[Block],
        settings: SearchSettings,
        model: SearchModel | None = None,
    ) -> None:
        self.blocks = tuple(blocks)
        self.settings = settings
        self.model = model
        self.current_page = 1

    @abstractmethod
    def search(self, speech_text: str) -> AlgorithmResult:
        """증분 발화를 처리하고 검색 결과를 반환한다."""

    @abstractmethod
    def reset(self) -> None:
        """세션 상태(페이지/버퍼 등)를 처음 상태로 되돌린다."""
