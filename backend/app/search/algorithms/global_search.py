from app.search.algorithms.semantic_base import SemanticSearch


class GlobalSearch(SemanticSearch):
    name = 'global'

    def search(self, speech_text):
        return self._commit(self._pick(speech_text, self.global_blocks(speech_text), 'global'))
