"""Causal page transitions and block tracking for a loaded lecture PDF."""
from collections import defaultdict
from copy import copy
from dataclasses import dataclass
from itertools import chain
import re

import numpy as np

from pdf import block_box, build_one, layout_groups, normalized
from text_index import BlockPhrases, DocumentTerms, LectureAnchors, normalize

_INTRO = re.compile(r'다음\s*(?:으로|은)|(?:살펴|알아|보)\s*(?:보도록|보겠습니다)|어떻게\s*(?:생겼|구성되어)|이제')
_RETRO = re.compile(r'(?:했었|보았|얘기했|설명했|말했|앞에서)')
_OPENING = re.compile(r'(?<!\w)(?:다음\s*으로|다음은|이어서|이번에는|이제)(?!\w)')
_GAP_MS = 7_000


class PageTracker:
    # 검색할 페이지 위치와 이동 확인 상태를 초기화한다.
    def __init__(self, pages):
        self._pages = pages
        self._positions = {page: index for index, page in enumerate(pages)}
        self._last_at = None
        self._last_move = None
        self.clear_pending()

    # 아직 확정되지 않은 페이지 후보와 관측 횟수를 비운다.
    def clear_pending(self):
        self._pending = None
        self._count = 0

    # 페이지 기준이 바뀐 시각을 기록하고 기존 이동 후보를 지운다.
    def correct(self, at):
        self.clear_pending()
        self._last_move = at

    # 가까운 후보가 연속해서 앞서는지 확인해 페이지 이동을 결정한다.
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
    # 문서 구조와 용어 색인을 준비하고 페이지 및 블록 추적 상태를 초기화한다.
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
        headings = {page: normalized(profile.header) for page, profile in profiles.items()}
        terms = None
        if policy.document_pairs:
            lines = (line['text'] for rows in layout.values() for line in rows if line['box'][1] < .93)
            terms = DocumentTerms(chain((block.text for block in blocks), lines))
        self._anchors = LectureAnchors(pages, by_page, profiles, layout, terms, policy.single_weight)
        self._phrases = BlockPhrases(by_page, terms)
        self._counts = {page: len(rows) for page, rows in by_page.items()}
        self._groups = {page: layout_groups(rows) for page, rows in by_page.items()}
        self._title_blocks, self._body = {}, {}
        for page, rows in by_page.items():
            vectors = page_block_vectors[page]
            header = headings[page]
            heading_text = [normalized(row.text) == header for row in rows]
            self._title_blocks[page] = np.array([
                bool(header and match and row.rects and block_box(row)[1] < .18)
                for row, match in zip(rows, heading_text, strict=True)
            ])
            body = [vec for vec, match in zip(vectors, heading_text, strict=True) if not match]
            self._body[page] = np.stack(body or vectors)
        fields = ('native', 'structure', 'focus')
        descriptions = [getattr(profiles[page], field) or profiles[page].header or by_page[page][0].text
                        for field in fields for page in pages]
        self._vectors = embed(descriptions).reshape(3, len(pages), -1) if len(pages) > 1 or pages[0] != 1 else None

        headers = [headings[page] for page in pages]
        self._siblings = np.array([[bool(first) and first == second for second in headers] for first in headers])
        self._front = np.arange(len(pages))
        for i in range(1, len(pages)):
            if self._siblings[i, i-1] and pages[i-1] + 1 == pages[i]:
                self._front[i] = self._front[i-1]
        self._lead = self._front == np.arange(len(pages))
        self._reset_tracking()

    # 페이지·블록 색인을 공유하고 발화에 따라 바뀌는 상태는 새로 생성한다.
    def new_session(self):
        session = copy(self)
        session._reset_tracking()
        return session

    # 새 발화가 시작될 때 사용할 이동 후보와 블록 문맥을 초기화한다.
    def _reset_tracking(self):
        self._baseline = PageTracker(self.pages)
        self._motion = PageTracker(self.pages)
        self._baseline_page = 1
        self._since = None
        self._protected = None
        self._last_page_at = None
        self._title_at = np.full(len(self.pages), -np.inf)
        self._last_title = np.zeros(len(self.pages), dtype=bool)
        self._block_at = None
        self._block_state = None
        self._block_costs = {}
        self._phrase_state = {page: np.zeros(size) for page, size in self._counts.items()}
        self._block_page = 1
        self._title_highlighted = False
        self._title_finished = False

    # 직전에 관측한 새 주제의 제목 근거를 비운다.
    def _clear_title_evidence(self):
        self._title_at.fill(-np.inf)
        self._last_title.fill(False)

    # 음성 인식 주기가 바뀔 때 이전 발화의 검색 근거를 비운다.
    def reset_query(self):
        self._clear_title_evidence()
        self._motion.clear_pending()
        self._reset_block_context()

    # 수동 보정 페이지를 기준으로 검색과 이동 추적 상태를 다시 맞춘다.
    def correct(self, page, at):
        self._enter_block_page(page)
        self._baseline_page = page
        self._baseline.correct(at)
        self._motion.correct(at)
        self._since = at
        self._protected = None
        self.reset_query()

    # 이전 발화에서 쌓인 블록 점수와 문구 기억을 지운다.
    def _reset_block_context(self):
        self._block_state = None
        self._block_at = None
        for scores in self._phrase_state.values():
            scores.fill(0.)

    # 발화 단서로 페이지 이동을 판단하고 블록 검색에 넘길 근거를 갱신한다.
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

    # 기본 페이지 점수와 제목, 본문의 고유 표현, 도식 숫자로 이동을 확정한다.
    def _move(self, page, short, long, vector, context_vector, base, coverage, at, weights, heading, numeric):
        if self._vectors is None or base is None:
            return None
        if self._since is None:
            self._since = at
        if self._last_page_at is not None and at - self._last_page_at > _GAP_MS:
            self._clear_title_evidence()
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
            self._clear_title_evidence()
        return target

    # 가까운 페이지의 본문 의미 일치가 현재 페이지보다 높은지 계산한다.
    def _semantic_support(self, page, steps, vector, new_group):
        scope = self._policy.semantic_scope
        eligible = (steps == 1) if scope in ('adjacent', 'section-first') else (abs(steps) <= 2) & (steps != 0)
        here = self._positions.get(page)
        section_first = here is not None and self._lead[here]
        if scope == 'section' or (scope == 'section-first' and not section_first):
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

    # 블록별 문구 근거를 시간에 따라 줄이고 최신 발화 근거를 반영한다.
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

    # 다른 페이지에 들어가면 블록 순서와 제목 강조 제한을 다시 시작한다.
    def _enter_block_page(self, page):
        if self._block_page != page:
            self._block_page = page
            self._block_state = None
            self._title_highlighted = False
            self._title_finished = False

    # 강조된 블록을 기록하고 본문으로 넘어가면 제목을 후보에서 뺀다.
    def highlighted(self, page, index):
        if self._title_blocks[page][index]:
            self._title_highlighted = True
        elif self._policy.title_highlight == 'after_body' or (
            self._policy.title_highlight == 'after_title' and self._title_highlighted
        ):
            self._title_finished = True

    # 본문 유사도, 배치 묶음, 최근 문구, 블록 순서로 강조 후보를 채점한다.
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
