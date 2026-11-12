"""알고리즘과 모델의 독립적인 생성. 지원하지 않는 조합은 즉시 오류."""
from typing import Sequence
from app.schemas.models import Block
from app.search.algorithms import (SlidingQueueSearch, LocalSearch, GlobalSearch,
                                   Hybrid1Search, Hybrid2Search)
from app.search.algorithms.base import SearchAlgorithm
from app.search.config import SearchSettings
from app.search.models import BGEM3Model, E5SmallModel, JevModel
from app.search.models.base import SearchModel

ALGORITHM_TYPES = {
    'sliding_queue': SlidingQueueSearch,
    'local': LocalSearch,
    'global': GlobalSearch,
    'hybrid1': Hybrid1Search,
    'hybrid2': Hybrid2Search,
}
MODEL_TYPES = {
    'bge-m3': BGEM3Model,
    'e5-small': E5SmallModel,
    'jev': JevModel,
}
ALGORITHMS = tuple(ALGORITHM_TYPES)
MODELS = tuple(MODEL_TYPES)


def create_algorithm(name: str, blocks: Sequence[Block], settings: SearchSettings,
                     model: SearchModel | None = None) -> SearchAlgorithm:
    if name not in ALGORITHM_TYPES:
        raise ValueError(f'알 수 없는 알고리즘: {name}; 지원 목록: {ALGORITHMS}')
    return ALGORITHM_TYPES[name](blocks, settings, model)


def load_model(name: str | None) -> SearchModel | None:
    if name is None or name == 'none':
        return None
    if name not in MODEL_TYPES:
        raise ValueError(f'알 수 없는 모델: {name}; 지원 목록: {MODELS}')
    return MODEL_TYPES[name]()
