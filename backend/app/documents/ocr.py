"""Local OCR for scanned and mixed text/image PDF pages."""
import os
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz


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
    native = page.get_text("dict", flags=TEXT_FLAGS)
    if not config.enabled:
        return native
    images = _image_regions(page)
    if not images:
        return native

    try:
        tessdata = fitz.get_tessdata(config.tessdata)
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
        data = pixmap.pdfocr_tobytes(language=config.language, tessdata=tessdata)
        image_lines = []
        with fitz.open(stream=data, filetype="pdf") as recognized:
            ocr_page = recognized[0]
            target = fitz.Rect(pixmap.x / zoom, pixmap.y / zoom,
                               (pixmap.x + pixmap.width) / zoom, (pixmap.y + pixmap.height) / zoom)
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
        if image_lines:
            # A figure is one searchable unit, regardless of OCR paragraphs or rows.
            image_lines.sort(key=lambda line: (round(line["bbox"][1], 1), line["bbox"][0]))
            blocks.append({"type": 0, "bbox": tuple(region), "lines": image_lines, "_ocr_image": True})
    return {**native, "blocks": blocks}
