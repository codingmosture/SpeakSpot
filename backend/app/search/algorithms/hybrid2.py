from app.search.algorithms.semantic_base import SemanticSearch


class Hybrid2Search(SemanticSearch):
    """Local 후보 + Global Top-K 페이지의 모든 블록을 합쳐 한 번에 재판단."""
    name = 'hybrid2'

    def search(self, speech_text):
        local = self.local_blocks()
        global_candidates = self._global_top_pages(speech_text)
        combined = list({b.block_id: b for b in local + global_candidates}.values())
        result = self._pick(speech_text, combined, 'global')
        if result.status == 'MATCH' and result.page in {b.page for b in local}:
            from app.search.algorithms.base import AlgorithmResult
            result = AlgorithmResult(status='MATCH', page=result.page,
                                     block_id=result.block_id, score=result.score, stage_used='local')
        return self._commit(result)
