"""검색 알고리즘 공통 설정. 각 세션에 개별 적용."""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchSettings:
    sliding_window_words: int = 40
    local_radius_pages: int = 5
    min_match_score: float = 0.34
    backward_penalty: float = 0.08
    stay_bonus: float = 0.025
    switch_margin: float = 0.05
    prefix_bonus: float = 0.035
    min_margin: float = 0.0
    global_top_k_pages: int = 3
    jev_min_confidence: float = 0.0
    max_jev_candidates: int = 120

    def __post_init__(self) -> None:
        if not 1 <= self.sliding_window_words <= 500:
            raise ValueError('sliding_window_words는 1~500이어야 합니다.')
        if not 0 <= self.local_radius_pages <= 100:
            raise ValueError('local_radius_pages는 0~100이어야 합니다.')
        if not 1 <= self.global_top_k_pages <= 100:
            raise ValueError('global_top_k_pages는 1~100이어야 합니다.')
        if not 1 <= self.max_jev_candidates <= 240:
            raise ValueError('max_jev_candidates는 1~240이어야 합니다.')
        for field in ('min_match_score', 'backward_penalty', 'stay_bonus', 'switch_margin',
                      'prefix_bonus', 'min_margin', 'jev_min_confidence'):
            if not 0 <= getattr(self, field) <= 1:
                raise ValueError(f'{field}는 0~1이어야 합니다.')
