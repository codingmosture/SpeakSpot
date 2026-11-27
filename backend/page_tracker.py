"""Confirm nearby page moves from consecutive search results."""
import numpy as np


class PageTracker:
    def __init__(self, pages):
        self._pages = pages
        self._positions = {page: index for index, page in enumerate(pages)}
        self._last_at = None
        self._last_move = None
        self.clear_pending()

    def clear_pending(self):
        self._pending = None
        self._count = 0

    def correct(self, at):
        self.clear_pending()
        self._last_move = at

    def observe(self, current, scores, at, *, allowed=None):
        if self._last_at is not None and at - self._last_at > 7000:
            self.clear_pending()
        self._last_at = at
        if self._last_move is not None and at - self._last_move < 1500:
            return None

        changes = self._pages - current
        costs = np.where(changes >= 0, .135, .36) + .405 * np.maximum(0, np.abs(changes) - 1)
        position = self._positions.get(current)
        stay = scores[position] if position is not None else 0.
        eligible = (changes != 0) & (np.abs(changes) <= 3)
        leads = np.where(eligible, scores - costs - stay, -np.inf)
        best = int(np.argmax(leads))

        if leads[best] < .12 or (allowed is not None and not allowed[best]):
            self.clear_pending()
            return None
        self._count = self._count + 1 if best == self._pending else 1
        self._pending = best
        if self._count < (3 if changes[best] == 1 else 4):
            return None
        self.correct(at)
        return int(self._pages[best])
