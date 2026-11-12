"""Local/Global/Hybrid 공통 판단 로직. Sliding Queue와 독립적이다."""
from typing import Sequence

from app.schemas.models import Block
from app.search.algorithms.base import AlgorithmResult, SearchAlgorithm
from app.search.models.base import SearchModel
from app.search.tokenizer import matches, tokenize


class SemanticSearch(SearchAlgorithm):
    def __init__(self, blocks, settings, model: SearchModel | None = None):
        if model is None:
            raise ValueError(f'{self.name}은 BGE-M3, E5-small 또는 Jev 모델이 필요합니다.')
        if model.kind not in ('embedding', 'choice'):
            raise ValueError(f'지원하지 않는 모델 유형: {model.kind}')
        super().__init__(blocks, settings, model)
        self._block_by_id = {b.block_id: b for b in self.blocks}

    def local_blocks(self) -> list[Block]:
        return [b for b in self.blocks if abs(b.page - self.current_page) <= self.settings.local_radius_pages]

    def _lexical_score(self, query: str, block: Block) -> float:
        terms = set(tokenize(query))
        if not terms:
            return 0.0
        block_words = tokenize(block.text)
        keyword_words = [t for x in block.keywords for t in tokenize(x)]
        score = sum(1.0 for term in terms if any(matches(term, w) for w in block_words))
        score += sum(1.5 for term in terms if any(matches(term, w) for w in keyword_words))
        if block.block_type == 'title':
            score *= 1.5
        return score

    def global_blocks(self, query: str) -> list[Block]:
        """임베딩은 전체 블록 검색. Jev는 토큰 한도 때문에 제목/키워드로 페이지를 축소."""
        if self.model.kind == 'embedding':
            return list(self.blocks)
        max_candidates = min(self.settings.max_jev_candidates, self.model.max_candidates)
        if len(self.blocks) <= max_candidates:
            return list(self.blocks)  # Top-K 누락 방지: 전부 전송 가능하면 축소하지 않음
        groups: dict[int, list[Block]] = {}
        for block in self.blocks:
            groups.setdefault(block.page, []).append(block)
        page_scores = {
            p: max(self._lexical_score(query, block) for block in page_blocks)
            + 0.12 * sum(self._lexical_score(query, block) for block in page_blocks)
            for p, page_blocks in groups.items()
        }
        page_order = sorted(groups, key=lambda p: (-page_scores[p], abs(p - self.current_page), p))
        selected_pages = page_order[:self.settings.global_top_k_pages]
        candidates = [block for page in selected_pages for block in groups[page]]
        if len(candidates) > max_candidates:
            candidates = sorted(candidates, key=lambda b: (
                -self._lexical_score(query, b),
                abs(b.page - self.current_page), b.page, b.block_id,
            ))[:max_candidates]
        return candidates

    def _global_top_pages(self, query: str) -> list[Block]:
        """Hybrid2 전용: Global에서 의미상 상위 페이지 선정, 해당 페이지의 블록 풀 사용."""
        if self.model.kind == 'choice':
            return self.global_blocks(query)
        rankings = self.model.rank(query, self.blocks)
        scores = {s.block_id: s.score for s in rankings}
        page_scores: dict[int, float] = {}
        for block in self.blocks:
            page_scores[block.page] = max(page_scores.get(block.page, -1.0), scores.get(block.block_id, -1.0))
        pages = sorted(page_scores, key=lambda p: (-page_scores[p], abs(p - self.current_page), p))
        page_set = set(pages[:self.settings.global_top_k_pages])
        return [b for b in self.blocks if b.page in page_set]

    def _limit_jev(self, text: str, candidates: Sequence[Block]) -> list[Block]:
        if self.model.kind != 'choice':
            return list(candidates)
        limit = min(self.settings.max_jev_candidates, self.model.max_candidates)
        if len(candidates) <= limit:
            return list(candidates)
        # 규모가 너무 크면 관련도 기반 사전 축소. 숨겨진 Top-5 제한이 아님.
        return sorted(candidates, key=lambda b: (
            -self._lexical_score(text, b),
            abs(b.page - self.current_page), b.page, b.block_id,
        ))[:limit]

    def _pick(self, speech_text: str, candidates: Sequence[Block], stage: str) -> AlgorithmResult:
        if not candidates:
            return AlgorithmResult(status='NONE')
        if self.model.kind == 'choice':
            candidates = self._limit_jev(speech_text, candidates)
            choice = self.model.choose(speech_text, candidates, allow_out_of_local=(stage == 'local'))
            if choice.block_id is None or (choice.confidence is not None and
                                           choice.confidence < self.settings.jev_min_confidence):
                return AlgorithmResult(status='NONE')
            block = next((b for b in candidates if b.block_id == choice.block_id), None)
            if block is None:
                raise ValueError('선택 모델이 현재 후보 외의 블록을 반환했습니다.')
            return AlgorithmResult(status='MATCH', page=block.page, block_id=block.block_id,
                                   score=choice.confidence, stage_used=stage)

        ranks = self.model.rank(speech_text, candidates)
        by_id = {b.block_id: b for b in candidates}
        scored = []
        for r in ranks:
            block = by_id.get(r.block_id)
            if block is None:
                raise ValueError('랭킹 모델이 현재 후보 외의 블록을 반환했습니다.')
            score = float(r.score)
            if not 0 <= score <= 1:
                raise ValueError('임베딩 모델 점수는 0~1로 정규화되어야 합니다.')
            adjusted = score
            if block.page == self.current_page:
                adjusted += self.settings.stay_bonus
            elif block.page < self.current_page:
                adjusted -= self.settings.backward_penalty * min((self.current_page - block.page) / 5, 1.0)
            scored.append((max(0.0, min(1.0, adjusted)), score, block))
        if not scored:
            return AlgorithmResult(status='NONE')
        scored.sort(key=lambda s: (-s[0], abs(s[2].page - self.current_page), s[2].page, s[2].block_id))
        best_score, _, best = scored[0]
        if best_score < self.settings.min_match_score:
            return AlgorithmResult(status='NONE')
        # 현재 페이지 경쟁 후보가 근소하게 뒤지면 불필요한 페이지 이동을 막는다.
        if best.page != self.current_page:
            stays = [item for item in scored if item[2].page == self.current_page]
            if stays:
                stay = max(stays, key=lambda x: x[0])
                if stay[0] >= self.settings.min_match_score and best_score < stay[0] + self.settings.switch_margin:
                    best_score, _, best = stay
        # 다른 페이지의 2위 후보와 매우 비슷하면 애매한 발화로 보고 NONE.
        competitors = [s for s in scored if s[2].page != best.page]
        if competitors and best_score - competitors[0][0] < self.settings.min_margin:
            return AlgorithmResult(status='NONE')
        return AlgorithmResult(status='MATCH', page=best.page, block_id=best.block_id,
                               score=round(best_score, 5), stage_used=stage)

    def _commit(self, result: AlgorithmResult) -> AlgorithmResult:
        if result.status == 'MATCH':
            self.current_page = result.page
        return result

    def reset(self) -> None:
        self.current_page = 1
