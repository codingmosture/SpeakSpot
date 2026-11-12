import math
import re
from collections import Counter
from typing import Literal
import pymupdf as fitz
from kiwipiepy import Kiwi
from app.schemas.models import Block, BBox
from app.documents.ocr import OCRConfig, extract_page_text

# ---------------------------------------------------------------------------
# 전역
# ---------------------------------------------------------------------------

# 형태소 분석기 초기화 및 Warm-up
kiwi = Kiwi()
kiwi.tokenize("사전 로드 예열")

BlockType = Literal[
    "title", "paragraph", "bullet",
    "table", "caption", "equation", "other"
]

# 수식용 중괄호 등 특수 유니코드 범위
BROKEN_MATH_CHARS_RE = re.compile(r"[\uf000-\uf8ff\u239b-\u23b3]")

class PDFParser:
    def __init__(self, ocr_config: OCRConfig | None = None):
        self.ocr_config = ocr_config if ocr_config is not None else OCRConfig.from_env()

    # -----------------------------------------------------------------------
    # 메인 파싱 함수: PDF 열기 -> 텍스트/좌표 추출 -> 블록 분류 -> 키워드 채우기
    # -----------------------------------------------------------------------
    def parse(self, file_path: str) -> tuple[int, list[Block]]:
        doc = fitz.open(file_path)

        try:
            # 암호화된 PDF 및 빈 PDF
            if doc.needs_pass:
                raise ValueError("암호화된 PDF는 지원하지 않습니다.")

            page_count = len(doc)
            if page_count == 0:
                raise ValueError("페이지가 없는 PDF입니다.")

            blocks: list[Block] = []

            for page_index in range(page_count):
                page = doc[page_index]
                page_num = page_index + 1

                width = page.rect.width
                height = page.rect.height
                if width <= 0 or height <= 0:
                    continue

                # 텍스트 블록 추출
                page_dict = extract_page_text(page, self.ocr_config)
                raw_text_blocks_initial = [
                    b for b in page_dict.get("blocks", []) if b.get("type") == 0
                ]

                # Remove footer/header page labels before grouping or keyword extraction.
                # Work in upright coordinates so rotated documents use the same rules.
                upright = page.rect * page.derotation_matrix
                filtered_blocks = []
                for block in raw_text_blocks_initial:
                    lines = [line for line in block.get("lines", [])
                             if not self._is_page_number(line, page_num, upright.width, upright.height)]
                    if lines:
                        block["lines"] = lines
                        block["bbox"] = (min(l["bbox"][0] for l in lines), min(l["bbox"][1] for l in lines),
                                         max(l["bbox"][2] for l in lines), max(l["bbox"][3] for l in lines))
                        filtered_blocks.append(block)
                raw_text_blocks_initial = filtered_blocks

                # Text/OCR coordinates are unrotated; the viewer uses display orientation.
                if page.rotation:
                    for block in raw_text_blocks_initial:
                        block["bbox"] = tuple(fitz.Rect(block["bbox"]) * page.rotation_matrix)
                        for line in block.get("lines", []):
                            line["bbox"] = tuple(fitz.Rect(line["bbox"]) * page.rotation_matrix)

                # 표 영역 감지
                table_bboxes = self._detect_tables_fast(
                    page, raw_text_blocks_initial, width, height
                )

                # 표 내부 텍스트 및 다중 불릿 강제 분할
                raw_text_blocks = []
                for b in raw_text_blocks_initial:
                    b_bbox = self._normalize_bbox(b["bbox"], width, height)
                    if b_bbox is None:
                        continue

                    is_in_table = any(
                        self._is_inside_table(b_bbox, t_bbox) for t_bbox in table_bboxes
                    )

                    if is_in_table:
                        # 표 안의 글자는 칸(줄) 단위로 분리
                        for line in b.get("lines", []):
                            raw_text_blocks.append({
                                "bbox": line["bbox"],
                                "lines": [line]
                            })
                    else:
                        # 표 밖이라도 불릿 단위로 분리
                        lines = b.get("lines", [])
                        bullet_indices = []
                        for i, line in enumerate(lines):
                            l_text = "".join([s.get("text", "") for s in line.get("spans", [])]).strip()
                            if re.match(r"^([•●○▪▫◆◇▶▷\-*]|(\d+|\([0-9a-zA-Z가-힣]+\))[\.\)])\s*", l_text):
                                bullet_indices.append(i)

                        # 한 블록 안에 불릿 시작 줄이 2개 이상 존재하면 독립된 블록으로 분할
                        if len(bullet_indices) >= 2:
                            for i in range(len(bullet_indices)):
                                start_idx = bullet_indices[i]
                                end_idx = bullet_indices[i + 1] if i + 1 < len(bullet_indices) else len(lines)
                                sub_lines = lines[start_idx:end_idx]
                                if sub_lines:
                                    x0 = min(l["bbox"][0] for l in sub_lines)
                                    y0 = min(l["bbox"][1] for l in sub_lines)
                                    x1 = max(l["bbox"][2] for l in sub_lines)
                                    y1 = max(l["bbox"][3] for l in sub_lines)
                                    raw_text_blocks.append({
                                        "bbox": (x0, y0, x1, y1),
                                        "lines": sub_lines
                                    })
                        else:
                            raw_text_blocks.append(b)
                            
                # y0 좌표, x0 좌표 기준으로 블록 순서 정렬
                raw_text_blocks.sort(key=lambda b: (round(b["bbox"][1], 1), b["bbox"][0]))
                
                # 페이지 기준 폰트 크기(중앙값 및 최대값) 계산
                font_sizes: list[float] = []
                for b in raw_text_blocks:
                    for line in b.get("lines", []):
                        for span in line.get("spans", []):
                            txt = span.get("text", "").strip()
                            if len(txt) >= 2 and span.get("size"):
                                font_sizes.append(span["size"])

                if font_sizes:
                    font_sizes.sort()
                    base_font_size = font_sizes[len(font_sizes) // 2]
                    max_page_font_size = font_sizes[-1]
                else:
                    base_font_size = 11.0
                    max_page_font_size = 18.0

                block_seq = 1

                # 개별 블록 생성 및 7종 분류
                for b in raw_text_blocks:
                    lines = b.get("lines", [])
                    full_text_parts: list[str] = []
                    rects: list[BBox] = []
                    block_font_sizes: list[float] = []
                    block_fonts: set[str] = set()

                    for line in lines:
                        spans = line.get("spans", [])
                        raw_line_text = "".join(span.get("text", "") for span in spans)
                        clean_line_text = BROKEN_MATH_CHARS_RE.sub("", raw_line_text).strip()

                        if clean_line_text:
                            # 좌표를 0-1 비율로 정규화
                            line_bbox = self._normalize_bbox(line["bbox"], width, height)
                            if line_bbox is not None:
                                full_text_parts.append(clean_line_text)
                                rects.append(line_bbox)
                                for span in spans:
                                    if span.get("size"):
                                        block_font_sizes.append(span["size"])
                                    if span.get("font"):
                                        block_fonts.add(span["font"])

                    # 여러 줄의 텍스트를 공백으로 연결
                    full_text = " ".join(full_text_parts).strip()
                    if not full_text:
                        continue

                    norm_bbox = self._normalize_bbox(b["bbox"], width, height)
                    if norm_bbox is None:
                        continue

                    max_block_font = max(block_font_sizes) if block_font_sizes else base_font_size

                    block_type = self._classify_block_type(
                        text=full_text,
                        max_font_size=max_block_font,
                        base_page_font_size=base_font_size,
                        max_page_font_size=max_page_font_size,
                        fonts=block_fonts,
                        norm_bbox=norm_bbox,
                        table_bboxes=table_bboxes
                    )

                    # 블록 ID 생성
                    block_id = f"p{page_num:04d}_b{block_seq:04d}"
                    block_seq += 1

                    blocks.append(
                        Block(
                            block_id=block_id,
                            page=page_num,
                            text=full_text,
                            block_type=block_type,
                            bbox=norm_bbox,
                            rects=rects if rects else [norm_bbox],
                            keywords=[]
                        )
                    )

        finally:
            doc.close()

        # 전체 블록 대상 키워드 추출
        if blocks:
            self._populate_keywords(blocks, page_count, top_k=5)

        return page_count, blocks

    @staticmethod
    def _is_page_number(line: dict, page_number: int, width: float, height: float) -> bool:
        text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
        text = text.strip(" -–—()[]")
        match = re.fullmatch(
            r"(?:(?:page|p\.?)\s*)?(\d{1,4})(?:\s*(?:/|of)\s*\d{1,4})?(?:\s*(?:페이지|쪽))?",
            text, flags=re.IGNORECASE,
        )
        if not match:
            return False
        # A bare numeric table value must not disappear just because it is small.
        if text.isdigit() and int(match.group(1)) != page_number:
            return False
        x0, y0, x1, y1 = line["bbox"]
        center = (x0 + x1) / (2 * width)
        margin = y0 / height >= 0.90 or y1 / height <= 0.10
        typical_position = center <= 0.2 or center >= 0.8 or 0.4 <= center <= 0.6
        return (margin and typical_position and (x1 - x0) / width <= 0.2
                and (y1 - y0) / height <= 0.06)

    # -----------------------------------------------------------------------
    # 7종 블록 타입 분류 함수
    # -----------------------------------------------------------------------
    def _classify_block_type(
        self,
        text: str,
        max_font_size: float,
        base_page_font_size: float,
        max_page_font_size: float,
        fonts: set[str],
        norm_bbox: BBox,
        table_bboxes: list[BBox]
    ) -> BlockType:
        # 텍스트 패턴/폰트 크기/종류/좌표로 block_type 반환
        stripped = text.strip()

        # 1. table
        for t_bbox in table_bboxes:
            if self._is_inside_table(norm_bbox, t_bbox):
                return "table"

        # 2. caption
        if re.match(r"^(\[|<|\()?\s*(그림|표|도표|사진|fig\.?|figure|table)\s*\d+", stripped, re.IGNORECASE):
            return "caption"

        # 3. title: 페이지 최대 글자 크기의 85% 이상이면서 본문(중앙값)보다 1.3배 이상 큰 경우
        if (
            max_font_size >= max_page_font_size * 0.85
            and max_font_size >= base_page_font_size * 1.3
            and len(stripped) < 100
        ):
            return "title"

        # 4. other (쪽번호, 작은 머리글, 코드 폰트)
        if (norm_bbox[1] > 0.90 or norm_bbox[3] < 0.08) and len(stripped) < 20:
            return "other"
        # code_fonts = ("consolas", "courier", "menlo", "monaco", "d2coding", "monospace")
        # if any(cf in f.lower() for f in fonts for cf in code_fonts):
        #   return "other"

        # 5. bullet (글머리 기호)
        if re.match(r"^([•●○▪▫◆◇▶▷\-*]|(\d+|\([0-9a-zA-Z가-힣]+\))[\.\)])\s*", stripped):
            return "bullet"

        # 6. equation
        korean_count = len(re.findall(r"[가-힣]", stripped))
        if korean_count < 5:
            math_fonts = ("math", "cmmi", "cmsy", "cmex", "mt extra")
            if any(mf in f.lower() for f in fonts for mf in math_fonts):
                return "equation"
            if any(sym in stripped for sym in ("∑", "∫", "√", "≈", "≠", "≤", "≥", "∞", "∂", "±", "∈", "∀", "f(x")):
                return "equation"

        # 7. paragraph
        return "paragraph"

    # -----------------------------------------------------------------------
    # 표 영역 감지 함수
    # -----------------------------------------------------------------------
    def _detect_tables_fast(
        self,
        page: fitz.Page,
        raw_text_blocks: list[dict],
        width: float,
        height: float
    ) -> list[BBox]:
        table_bboxes: list[BBox] = []

        # C 레벨 고속 도형 추출 함수 우선 사용
        drawings = page.get_cdrawings() if hasattr(page, "get_cdrawings") else page.get_drawings()

        # 도형이 3개 미만(표 구성 불가)이거나 300개 초과인 페이지는 스킵
        if len(drawings) < 3 or len(drawings) > 300:
            return table_bboxes

        # 1단계: 가로로 길고 두께가 얇은 수평선만 필터링
        h_lines: list[tuple[float, float, float]] = []
        for d in drawings:
            r_data = d.get("rect")
            if r_data is None:
                continue
            r = fitz.Rect(r_data) * page.rotation_matrix
            w = r.width / width
            h = r.height / height

            # 페이지 가로폭의 25% 이상이면서 높이가 페이지의 1.5% 미만인 수평선만 추출
            if w > 0.25 and h < 0.015:
                x0 = round(r.x0 / width, 3)
                x1 = round(r.x1 / width, 3)
                y_mid = round((r.y0 + r.y1) / (2 * height), 4)
                h_lines.append((x0, x1, y_mid))

        # 표를 구성하려면 최소 3개의 가로선이 필요하므로
        if len(h_lines) < 3:
            return table_bboxes

        # 2단계: 왼쪽 시작점(x0)과 오른쪽 끝점(x1)이 서로 일치하는(오차 2% 이내) 평행한 가로선끼리 그룹화
        used = [False] * len(h_lines)
        for i in range(len(h_lines)):
            if used[i]:
                continue
            x0_base, x1_base, y_base = h_lines[i]
            group_ys = [y_base]
            used[i] = True

            for j in range(i + 1, len(h_lines)):
                if not used[j]:
                    x0_curr, x1_curr, y_curr = h_lines[j]
                    if abs(x0_base - x0_curr) < 0.02 and abs(x1_base - x1_curr) < 0.02:
                        group_ys.append(y_curr)
                        used[j] = True

            # 서로 다른 높이에 평행한 가로선이 3개 이상 존재하면 표 후보
            unique_ys = sorted(set(group_ys))
            if len(unique_ys) >= 3:
                y_top, y_bottom = unique_ys[0], unique_ys[-1]
                if y_bottom - y_top > 0.05:
                    candidate_bbox: BBox = (
                        round(max(0.0, x0_base - 0.01), 4),
                        round(max(0.0, y_top - 0.01), 4),
                        round(min(1.0, x1_base + 0.01), 4),
                        round(min(1.0, y_bottom + 0.01), 4),
                    )

                    # 3단계: 후보 영역 내부에 실제로 텍스트 줄이 4개 이상 들어있는지
                    line_count_inside = 0
                    for b in raw_text_blocks:
                        norm_b = self._normalize_bbox(b["bbox"], width, height)
                        if norm_b is not None and self._is_inside_table(norm_b, candidate_bbox):
                            line_count_inside += len(b.get("lines", []))

                    if line_count_inside >= 4:
                        table_bboxes.append(candidate_bbox)

        return table_bboxes
    
    # 표 내부인지 확인
    def _is_inside_table(self, block_bbox: BBox, table_bbox: BBox) -> bool:
        cx = (block_bbox[0] + block_bbox[2]) / 2
        cy = (block_bbox[1] + block_bbox[3]) / 2
        return (table_bbox[0] <= cx <= table_bbox[2]) and (table_bbox[1] <= cy <= table_bbox[3])

    # -----------------------------------------------------------------------
    # Kiwi 형태소 분석 + TF-IDF 기반 핵심 키워드 추출 함수
    # -----------------------------------------------------------------------
    def _populate_keywords(self, blocks: list[Block], total_pages: int, top_k: int = 5) -> None:
        block_words_list: list[list[str]] = []
        page_word_sets: dict[int, set[str]] = {p: set() for p in range(1, total_pages + 1)}         # 단어 기록
        page_word_counts: dict[int, Counter] = {p: Counter() for p in range(1, total_pages + 1)}    # 빈도 기록

        texts = [b.text for b in blocks]
        token_lists = kiwi.tokenize(texts)

        # 형태소 분석 결과에서 명사, 영단어만
        for block, tokens in zip(blocks, token_lists):
            words = []
            for t in tokens:
                if t.tag in ("NNG", "NNP", "SL"):   # NNG: 일반명사, NNP: 고유명사, SL: 외국어
                    word = t.form.lower() if t.tag == "SL" else t.form
                    if len(word) > 1:   # 1글자짜리 무의미한 단어
                        words.append(word)
            
            block_words_list.append(words)
            page_word_sets[block.page].update(words)
            page_word_counts[block.page].update(words)

        # 전체 페이지 기준 등장 빈도 계산
        df_counts: Counter = Counter()
        for word_set in page_word_sets.values():
            for word in word_set:
                df_counts[word] += 1

        # 블록별 TF-IDF 점수 계산
        for block, words in zip(blocks, block_words_list):
            if not words:
                continue

            p_counts = page_word_counts[block.page]
            total_words_in_page = sum(p_counts.values()) or 1
            unique_words = list(dict.fromkeys(words))
            scored_words: list[tuple[str, float]] = []

            for w in unique_words:
                tf = (p_counts[w] / total_words_in_page) * (1 + 0.2 * words.count(w))
                idf = math.log((total_pages + 1) / (df_counts[w] + 1)) + 1.0
                scored_words.append((w, tf * idf))

            # TF-IDF 점수가 높은 것부터 정렬후 상위 top k개(기본 5개) 단어 저장
            scored_words.sort(key=lambda x: x[1], reverse=True)
            block.keywords = [w for w, score in scored_words[:top_k]]

    # -----------------------------------------------------------------------
    # 좌표 0-1 정규화 보조 함수
    # -----------------------------------------------------------------------
    def _normalize_bbox(
        self,
        bbox: tuple[float, float, float, float],
        width: float,
        height: float
    ) -> BBox | None:
        if width <= 0 or height <= 0:
            return None

        rect = fitz.Rect(bbox)
        rect.normalize()

        # 페이지 영역(0, 0, width, height)과 교집합을 구해 화면 밖으로 삐져나간 좌표 클리핑
        page_rect = fitz.Rect(0, 0, width, height)
        rect = rect & page_rect

        if rect.is_empty or rect.width <= 0 or rect.height <= 0:
            return None

        norm_bbox: BBox = (
            round(rect.x0 / width, 4),
            round(rect.y0 / height, 4),
            round(rect.x1 / width, 4),
            round(rect.y1 / height, 4),
        )

        # 소수점 반올림 후 너비나 높이가 0이 된 경우 제외
        if norm_bbox[0] >= norm_bbox[2] or norm_bbox[1] >= norm_bbox[3]:
            return None

        return norm_bbox
