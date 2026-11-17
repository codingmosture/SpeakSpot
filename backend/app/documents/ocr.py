"""Local OCR for scanned and mixed text/image PDF pages."""
import os
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz
from app.documents.labels import label_crops


DEFAULT_TESSDATA = Path(__file__).resolve().parents[2] / ".cache" / "tessdata"
TEXT_FLAGS = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES


@dataclass(frozen=True)
class OCRConfig:
    enabled: bool = True
    language: str = "kor+eng"
    dpi: int = 200
    tessdata: str | None = None

    def __post_init__(self):
        if not 72 <= self.dpi <= 600:
            raise ValueError("PDF OCR DPI는 72~600 사이여야 합니다.")

    @classmethod
    def from_env(cls):
        tessdata = os.getenv("SPEAKSPOT_OCR_TESSDATA") or os.getenv("TESSDATA_PREFIX")
        if not tessdata and DEFAULT_TESSDATA.is_dir():
            tessdata = str(DEFAULT_TESSDATA)
        return cls(
            enabled=os.getenv("SPEAKSPOT_OCR_ENABLED", "true").lower() not in {"false", "0", "off"},
            language=os.getenv("SPEAKSPOT_OCR_LANGUAGE", "kor+eng"),
            dpi=int(os.getenv("SPEAKSPOT_OCR_DPI", "200")),
            tessdata=tessdata,
        )


def extract_page_text(page: fitz.Page, config: OCRConfig) -> dict:
    # Do not load image binaries into the text dictionary.
    native = _native_text(page)
    if not config.enabled:
        return native
    images = _image_regions(page)
    if not images:
        return native

    try:
        data_path = config.tessdata or (str(DEFAULT_TESSDATA) if DEFAULT_TESSDATA.is_dir() else None)
        tessdata = fitz.get_tessdata(data_path)
        missing = [
            lang for lang in config.language.split("+")
            if not (Path(tessdata) / f"{lang}.traineddata").is_file()
        ]
        if missing:
            raise RuntimeError(f"OCR language data missing: {', '.join(missing)}")
        if page.rotation:
            # OCR upright pixels; keep returned coordinates in unrotated PDF space.
            # Changing a temporary copy leaves the uploaded document untouched.
            with fitz.open() as upright:
                upright.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
                upright_page = upright[0]
                upright_page.set_rotation(0)
                return _recognize_images(upright_page, native, images, config, tessdata)
        return _recognize_images(page, native, images, config, tessdata)
    except Exception as exc:
        # MuPDF's OCR errors may be FzErrorBase, not RuntimeError.
        message = (
            f"{page.number + 1}페이지의 이미지 OCR을 실행하지 못했습니다. "
            "backend에서 python scripts/setup_ocr.py를 실행하거나 "
            "SPEAKSPOT_OCR_TESSDATA와 OCR 언어 데이터를 확인하세요."
        )
        # Do not report a mixed document as ready while silently dropping its images.
        raise ValueError(message) from exc


def _native_text(page: fitz.Page) -> dict:
    """Keep PDF word gaps that MuPDF omits between Korean glyphs."""
    native = page.get_text("rawdict", flags=TEXT_FLAGS)
    for block in native.get("blocks", []):
        for line in block.get("lines", []):
            glyphs = [(span, char) for span in line.get("spans", []) for char in span.get("chars", [])]
            gaps = {}
            horizontal = line.get("dir", (1, 0))[0] > .99
            for (left_span, left), (right_span, right) in zip(glyphs, glyphs[1:]):
                a, b = left["c"], right["c"]
                size = min(left_span["size"], right_span["size"])
                if (horizontal and size > 0 and a.isalnum() and b.isalnum()
                        and any('가' <= c <= '힣' for c in a + b)
                        and abs(left["origin"][1] - right["origin"][1]) <= size * .1):
                    gaps[id(right)] = max(0, right["bbox"][0] - left["bbox"][2]) / size
            # Subtract ordinary letter tracking so a spaced-out word stays intact.
            tracking = sorted(gaps.values())[len(gaps) // 4] if gaps else 0
            for span in line.get("spans", []):
                span["text"] = ''.join(
                    (' ' if gaps.get(id(char), 0) > tracking + .15 else '') + char["c"]
                    for char in span.pop("chars", [])
                )
    return native


def _image_regions(page: fitz.Page) -> list[fitz.Rect]:
    bounds = page.rect * page.derotation_matrix
    regions = []
    seen = set()
    for image in page.get_image_info():
        rect = fitz.Rect(image["bbox"]) & bounds
        # Slide separators and one-pixel decorations are not text-bearing images.
        if rect.is_empty:
            continue
        short, long = sorted((rect.width, rect.height))
        if long < 8 or (short < 3 and long / short > 20):
            continue
        key = tuple(round(value, 2) for value in rect)
        if key not in seen:
            seen.add(key)
            regions.append(rect)
    return regions


def _line_text(line: dict) -> str:
    return re.sub(r"\s+", "", "".join(s.get("text", "") for s in line.get("spans", []))).casefold()


def _duplicate_line(line: dict, existing: list[dict]) -> bool:
    rect = fitz.Rect(line["bbox"])
    text = _line_text(line)
    for other in existing:
        other_rect = fitz.Rect(other["bbox"])
        overlap = (rect & other_rect).get_area()
        if overlap <= 0 or min(rect.get_area(), other_rect.get_area()) <= 0:
            continue
        same_text = text == _line_text(other)
        if same_text and overlap / min(rect.get_area(), other_rect.get_area()) > 0.5:
            return True
        if overlap / rect.get_area() > 0.8 and overlap / other_rect.get_area() > 0.6:
            return True
    return False


def _recognize_images(page: fitz.Page, native: dict, regions: list[fitz.Rect],
                      config: OCRConfig, tessdata: str) -> dict:
    blocks = list(native.get("blocks", []))
    existing = [line for block in blocks for line in block.get("lines", [])]
    zoom = config.dpi / 72
    for region in regions:
        # OCR each image crop independently. Full-page layout detection misses cells
        # in slide screenshots and can take longer than the upload timeout.
        pixmap = page.get_pixmap(dpi=config.dpi, clip=region, alpha=False)
        image_lines = []
        # White lettering in colored diagram boxes is often omitted by the
        # engine's page layout detector. Read those labels before the full crop.
        for label, bounds in label_crops(pixmap):
            target = fitz.Rect((pixmap.x + bounds.x0) / zoom, (pixmap.y + bounds.y0) / zoom,
                               (pixmap.x + bounds.x1) / zoom, (pixmap.y + bounds.y1) / zoom)
            _recognize_pixmap(label, target, config, tessdata, image_lines, existing)
        target = fitz.Rect(pixmap.x / zoom, pixmap.y / zoom,
                           (pixmap.x + pixmap.width) / zoom, (pixmap.y + pixmap.height) / zoom)
        _recognize_pixmap(pixmap, target, config, tessdata, image_lines, existing)
        if image_lines:
            # A figure is one searchable unit, regardless of OCR paragraphs or rows.
            image_lines.sort(key=lambda line: (round(line["bbox"][1], 1), line["bbox"][0]))
            blocks.append({"type": 0, "bbox": tuple(region), "lines": image_lines, "_ocr_image": True})
    return {**native, "blocks": blocks}


def _recognize_pixmap(pixmap, target, config, tessdata, image_lines, existing):
    data = pixmap.pdfocr_tobytes(language=config.language, tessdata=tessdata)
    with fitz.open(stream=data, filetype="pdf") as recognized:
        ocr_page = recognized[0]
        matrix = ocr_page.rect.torect(target)
        for block in ocr_page.get_text("dict", flags=TEXT_FLAGS).get("blocks", []):
            for line in block.get("lines", []):
                line["bbox"] = tuple(fitz.Rect(line["bbox"]) * matrix)
                for span in line.get("spans", []):
                    span["bbox"] = tuple(fitz.Rect(span["bbox"]) * matrix)
                    if "origin" in span:
                        span["origin"] = tuple(fitz.Point(span["origin"]) * matrix)
                if _line_text(line) and not _duplicate_line(line, existing):
                    image_lines.append(line)
                    existing.append(line)
