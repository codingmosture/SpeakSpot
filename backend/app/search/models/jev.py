"""TypeSafe Jev Choice API 어댑터. 모델 결과를 재작성하거나 임의로 대체하지 않는다."""
import os
from typing import Sequence
import httpx

from app.schemas.models import Block
from app.search.models.base import ModelChoice, ModelRequestError, ModelUnavailableError


class JevModel:
    name = 'jev'
    kind = 'choice'

    def __init__(self):
        self.api_key = os.getenv('TYPESAFE_API_KEY', '').strip()
        if not self.api_key:
            raise ModelUnavailableError('Jev에는 TYPESAFE_API_KEY가 필요합니다.')
        self.model_id = os.getenv('TYPESAFE_JEV_MODEL', 'jev-latest')
        self.url = os.getenv('TYPESAFE_JEV_URL', 'https://api.typesafe.ai/v1/systemone')
        self.timeout = float(os.getenv('TYPESAFE_JEV_TIMEOUT', '15'))
        self.max_candidates = 240  # NONE, OUT_OF_LOCAL을 포함해 Choice 255개 제한 이하

    def prepare(self, document_id: str, blocks: Sequence[Block]) -> None:
        # Choice 모델은 임베딩 인덱스를 생성하지 않는다.
        pass

    def choose(self, query: str, candidates: Sequence[Block], *, allow_out_of_local: bool = False) -> ModelChoice:
        if not candidates:
            return ModelChoice(None, reason='none')
        if len(candidates) > self.max_candidates:
            raise ValueError(f'Jev 후보는 {self.max_candidates}개 이하로 보내야 합니다.')
        lookup = {f'b{i}': block.block_id for i, block in enumerate(candidates)}
        # 120개 후보를 보낼 때 긴 원문을 모두 붙이면 Jev 문맥 한도를 넘기기 쉬움.
        char_limit = min(650, max(120, 24000 // len(candidates)))
        criteria = {
            f'b{i}': f'페이지 {block.page}, 블록 {block.block_id}: {block.text[:char_limit]}'
            for i, block in enumerate(candidates)
        }
        criteria['NONE'] = '주어진 블록 중 발화 내용과 직접적으로 일치하는 내용이 없음'
        if allow_out_of_local:
            criteria['OUT_OF_LOCAL'] = '현재 페이지 주변이 아닌 다른 페이지에 설명 대상이 있을 것으로 판단됨'
        body = {
            'model': self.model_id,
            'state': {'speech': query},
            'questions': {
                'block': {
                    'type': 'choice',
                    'instructions': (
                        '강사가 현재 설명하는 PDF 블록을 하나 선택하세요. 문구가 완전히 같지 않아도 의미를 비교하고, '
                        '문맥이 부합하지 않으면 NONE 또는 OUT_OF_LOCAL을 선택하세요.'
                    ),
                    'criteria': criteria,
                }
            },
        }
        try:
            response = httpx.post(
                self.url, json=body,
                headers={'Authorization': f'Bearer {self.api_key}'},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            answer = payload['answers']['block']
            selected = answer['choice']
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ModelRequestError(f'Jev API 호출/응답 처리 실패: {type(exc).__name__}') from exc
        if not isinstance(selected, str) or selected not in criteria:
            raise ModelRequestError('Jev가 알 수 없는 후보 ID를 반환했습니다.')
        probs = answer.get('probabilities') or {}
        confidence = probs.get(selected, answer.get('confidence'))
        if confidence is not None:
            try:
                confidence = float(confidence)
            except (ValueError, TypeError) as exc:
                raise ModelRequestError('Jev confidence 값이 잘못되었습니다.') from exc
            if not 0 <= confidence <= 1:
                raise ModelRequestError('Jev confidence 범위를 벗어났습니다.')
        if selected in ('NONE', 'OUT_OF_LOCAL'):
            return ModelChoice(None, confidence, selected.lower())
        return ModelChoice(lookup[selected], confidence, 'selected')
