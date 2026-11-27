"""Causal page transitions and block tracking for a loaded lecture PDF."""
from collections import defaultdict
from dataclasses import dataclass
from itertools import chain
import re

import numpy as np

from document_terms import DocumentTerms
from lecture_anchors import BlockPhrases, LectureAnchors
from lecture_pdf import block_box, build_one, layout_groups, normalized
from page_ranking import normalize
from page_tracker import PageTracker

_INTRO = re.compile(r'다음\s*(?:으로|은)|(?:살펴|알아|보)\s*(?:보도록|보겠습니다)|어떻게\s*(?:생겼|구성되어)|이제')
_RETRO = re.compile(r'(?:했었|보았|얘기했|설명했|말했|앞에서)')
_OPENING = re.compile(r'(?<!\w)(?:다음\s*으로|다음은|이어서|이번에는|이제)(?!\w)')
_GAP_MS = 7_000


@dataclass(frozen=True)
class LecturePolicy:
    document_pairs: bool = True
    single_weight: float = 0.
    semantic_weight: float = .65
    semantic_margin: float = .012
    title_confirmation: float = .60
    semantic_scope: str = 'section-first'
    pair_scope: str = 'siblings'
    title_guard_ms: int = 18_000
    same_title_hold_ms: int = 65_000
    protect_confirmed: bool = True
    intro_clause: bool = True
    title_highlight: str = 'after_body'


class LectureMatching:
    def __init__(self, pages, blocks, layout, embed, block_vectors, *, policy=LecturePolicy()):
        self.pages = pages
        self._policy = policy
        self._positions = {page: i for i, page in enumerate(pages)}
        by_page = defaultdict(list)
        page_block_vectors = defaultdict(list)
        for block, vector in zip(blocks, block_vectors, strict=True):
            by_page[block.page].append(block)
            page_block_vectors[block.page].append(vector)
        profiles = {page: build_one(by_page[page], layout.get(page, ())) for page in pages}
        terms = None
        if policy.document_pairs:
            lines = (line['text'] for rows in layout.values() for line in rows if line['box'][1] < .93)
            terms = DocumentTerms(chain((block.text for block in blocks), lines))
        self._anchors = LectureAnchors(pages, by_page, profiles, layout, terms, policy.single_weight)
        self._phrases = BlockPhrases(by_page, terms)
        self._counts = {page: len(rows) for page, rows in by_page.items()}
        self._groups = {page: layout_groups(rows) for page, rows in by_page.items()}
        self._title_blocks = {}
        for page, rows in by_page.items():
            header = normalized(profiles[page].header)
            self._title_blocks[page] = np.array([
                bool(header and row.rects and block_box(row)[1] < .18 and normalized(row.text) == header)
                for row in rows
            ])
        self._body = {}
        for page, rows in by_page.items():
            vectors = page_block_vectors[page]
            body = [vector for block, vector in zip(rows, vectors, strict=True)
                    if normalized(block.text) != normalized(profiles[page].header)]
            self._body[page] = np.stack(body or vectors)
        fields = ('native', 'structure', 'focus')
        descriptions = [getattr(profiles[page], field) or profiles[page].header or by_page[page][0].text
                        for field in fields for page in pages]
        self._vectors = embed(descriptions).reshape(3, len(pages), -1) if len(pages) > 1 or pages[0] != 1 else None

        headers = [normalized(profiles[page].header) for page in pages]
        self._siblings = np.array([[bool(first) and first == second for second in headers] for first in headers])
        self._front = np.arange(len(pages))
        for i in range(1, len(pages)):
            if self._siblings[i, i-1] and pages[i-1] + 1 == pages[i]:
                self._front[i] = self._front[i-1]
        self._lead = self._front == np.arange(len(pages))
        self._baseline = PageTracker(pages)
        self._motion = PageTracker(pages)
        self._baseline_page = 1
        self._since = None
        self._protected = None
        self._last_page_at = None
        self._title_at = np.full(len(pages), -np.inf)
        self._last_title = np.zeros(len(pages), dtype=bool)
        self._block_at = None
        self._block_state = None
        self._block_costs = {}
        self._phrase_state = {page: np.zeros(size) for page, size in self._counts.items()}
        self._block_page = 1
        self._title_highlighted = False
        self._title_finished = False

    def reset_query(self):
        self._title_at.fill(-np.inf)
        self._last_title.fill(False)
        self._motion.clear_pending()
        self._reset_block_context()

    def correct(self, page, at):
        self._enter_block_page(page)
        self._baseline_page = page
        self._baseline.correct(at)
        self._motion.correct(at)
        self._since = at
        self._protected = None
        self.reset_query()

    def _reset_block_context(self):
        self._block_state = None
        self._block_at = None
        for scores in self._phrase_state.values():
            scores.fill(0.)

    def observe(self, page, short, long, vector, context_vector, base, coverage, at):
        weights, heading, numeric = self._anchors.observe(
            short, long, literal_heading=self._policy.pair_scope != 'all',
        )
        change = self._move(page, short, long, vector, context_vector, base, coverage, at, weights, heading, numeric)
        if change is not None:
            page = change
            self._enter_block_page(page)
        memory = self._update_phrases(short, long, page, at)
        return change, weights, memory

    def _move(self, page, short, long, vector, context_vector, base, coverage, at, weights, heading, numeric):
        if self._vectors is None or base is None:
            return None
        if self._since is None:
            self._since = at
        if self._last_page_at is not None and at - self._last_page_at > _GAP_MS:
            self._title_at.fill(-np.inf)
            self._last_title.fill(False)
        self._last_page_at = at
        baseline_change = self._baseline.observe(self._baseline_page, base, at)
        if baseline_change is not None:
            self._baseline_page = baseline_change

        here = self._positions.get(page)
        steps = self.pages - page
        near = abs(self.pages - self._baseline_page) <= 2
        forward = (steps > 0) & (steps <= 2) & near
        siblings = self._siblings[here] if here is not None else np.zeros(len(self.pages), dtype=bool)
        current_base = base[here] if here is not None else 0.
        current_heading = heading[here] if here is not None else 0.
        current_numeric = numeric[here] if here is not None else False
        parsed = normalize(.69 * (self._vectors @ vector) + .31 * (self._vectors @ context_vector)
                           + .06 * coverage, floor=.034)
        pdf = .20 * parsed[0] + .33 * parsed[1] + .47 * parsed[2]
        current_pdf = pdf[here] if here is not None else 0.
        scores = base + .32 * np.clip((pdf - current_pdf) - (base - current_base), 0., .9) * forward

        specific = self._anchors.incoming(weights, here)
        if self._policy.pair_scope == 'siblings':
            literal = self._anchors.incoming(weights, here, include_pairs=False)
            specific = np.where(siblings, specific, literal)
        specific = np.where(abs(steps) <= 2, specific, 0.)
        mention = np.maximum(0., heading - current_heading)
        introduction = bool(_INTRO.search(short) or (_INTRO.search(long) and not _RETRO.search(short)))
        new_group = forward & ~siblings
        if self._policy.intro_clause:
            openings = list(_OPENING.finditer(long))
            if openings:
                focused = self._anchors.literal_titles(long[openings[-1].end():])
                focus_here = focused[here] if here is not None else 0.
                fresh = np.maximum(0., focused - focus_here) * new_group
                mention = np.maximum(mention, fresh)
                introduction = introduction or bool(fresh.any())
        direct = new_group & self._lead & (mention >= .50) & introduction
        self._title_at[direct] = at
        if self._policy.title_guard_ms and np.any(new_group & (at - self._title_at <= self._policy.title_guard_ms)):
            scores[steps < 0] = -np.inf
        if self._policy.semantic_weight:
            query = .70 * vector + .30 * context_vector
            semantic = self._semantic_support(page, steps, query, new_group)
            if self._policy.protect_confirmed and page == self._protected:
                semantic[siblings] = 0.
            specific += semantic
        scores += 2.8 * mention * new_group * self._lead * introduction
        covered_by_prior = (self.pages[self._front] > page) & (specific < .65)
        scores -= 2.8 * mention * new_group * ~self._lead * covered_by_prior * introduction
        if at - self._since < self._policy.same_title_hold_ms:
            scores[siblings & forward & (specific < .65)] = -np.inf
            if page == self._protected:
                scores -= .75 * (steps < 0) * (specific < .65)
                if self._policy.protect_confirmed:
                    scores[(steps < 0) & ~siblings & (heading < .50)] = -np.inf
        unsupported_skip = (abs(steps) > 1) & (specific < .65) & (mention < .70)
        scores[unsupported_skip] = -np.inf

        confirmed = new_group & self._lead & (at - self._title_at <= 12_000) & (specific >= .70)
        if self._policy.title_confirmation:
            title = (steps == 1) & direct & (mention >= self._policy.title_confirmation)
            confirmed |= title & self._last_title
            self._last_title = title
        confirmed |= (steps == 1) & near & numeric & ~current_numeric
        confirmed &= ~unsupported_skip
        if confirmed.any():
            target = int(self.pages[np.argmax(np.where(confirmed, specific, -np.inf))])
            self._motion.correct(at)
            self._protected = target
        else:
            target = self._motion.observe(page, scores, at, allowed=near)
        if target is not None:
            self._since = at
            self._title_at.fill(-np.inf)
            self._last_title.fill(False)
        return target

    def _semantic_support(self, page, steps, vector, new_group):
        scope = self._policy.semantic_scope
        eligible = (steps == 1) if scope in ('adjacent', 'section-first') else (abs(steps) <= 2) & (steps != 0)
        if scope == 'section':
            eligible &= new_group
        if scope == 'section-first':
            here = self._positions.get(page)
            if here is None or not self._lead[here]:
                eligible &= new_group
        current = self._body.get(page)
        result = np.zeros(len(self.pages))
        if current is None or not eligible.any():
            return result
        reference = float((current @ vector).max())
        for col in np.flatnonzero(eligible):
            best = float((self._body[self.pages[col]] @ vector).max())
            margin = best - reference - self._policy.semantic_margin
            if best >= .47:
                result[col] = self._policy.semantic_weight * np.clip(margin / .055, 0., 1.20)
        return result

    def _update_phrases(self, short, long, current, at):
        if self._block_at is None or at - self._block_at > _GAP_MS:
            self._block_state = None
            decay = 0.
        else:
            decay = np.exp(-(at - self._block_at) / 9000.)
        self._block_at = at
        memory = None
        for page, direct in self._phrases.scores(short, long, self._counts):
            state = self._phrase_state[page]
            state *= decay
            reliable = direct > direct.max() - .30
            np.maximum(state, np.where(reliable, direct, 0.), out=state)
            if page == current:
                memory = np.maximum(0., state - direct)
        return memory

    def _enter_block_page(self, page):
        if self._block_page != page:
            self._block_page = page
            self._block_state = None
            self._title_highlighted = False
            self._title_finished = False

    def highlighted(self, page, index):
        if self._title_blocks[page][index]:
            self._title_highlighted = True
        elif self._policy.title_highlight == 'after_body' or (
            self._policy.title_highlight == 'after_title' and self._title_highlighted
        ):
            self._title_finished = True

    def block_scores(self, page, scores, weights, memory):
        scores = normalize(scores, floor=.035)
        scores += .36 * self._anchors.block_support(page, weights, len(scores))
        grouped = scores.copy()
        for group in self._groups[page]:
            indices = list(group)
            values = scores[indices]
            grouped[indices] += .32 * (.55 * values.max() + .45 * values.mean())
        if memory is not None:
            grouped += 1.13 * memory
        if self._block_state is not None:
            if page not in self._block_costs:
                step = np.arange(len(scores))[None, :] - np.arange(len(scores))[:, None]
                distance = abs(step)
                cost = .60 * np.minimum(6., distance) + .35 * (step < 0) + .20 * np.maximum(0., distance - 1)
                self._block_costs = {page: .22 * cost}
            grouped += np.max(.68 * self._block_state[:, None] - self._block_costs[page], axis=0)
        if self._title_finished:
            grouped[self._title_blocks[page]] = -np.inf
        self._block_state = grouped - grouped.max()
        return self._block_state
