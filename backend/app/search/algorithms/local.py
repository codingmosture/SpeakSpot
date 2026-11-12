from app.search.algorithms.semantic_base import SemanticSearch


class LocalSearch(SemanticSearch):
    name = 'local'

    def search(self, speech_text):
        return self._commit(self._pick(speech_text, self.local_blocks(), 'local'))
