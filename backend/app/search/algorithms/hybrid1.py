from app.search.algorithms.semantic_base import SemanticSearch


class Hybrid1Search(SemanticSearch):
    """Local 우선, 실패(NONE / Jev OUT_OF_LOCAL) 시 Global 재탐색."""
    name = 'hybrid1'

    def search(self, speech_text):
        result = self._pick(speech_text, self.local_blocks(), 'local')
        if result.status == 'NONE':
            result = self._pick(speech_text, self.global_blocks(speech_text), 'global')
        return self._commit(result)
