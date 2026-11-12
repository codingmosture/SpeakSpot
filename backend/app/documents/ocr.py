"""Local OCR for scanned and mixed text/image PDF pages."""
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz


logger = logging.getLogger(__name__)
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
    has_text = any(
        span.get("text", "").strip()
        for block in native.get("blocks", []) if block.get("type") == 0
        for line in block.get("lines", [])
        for span in line.get("spans", [])
    )
    # Visible images include inline images. Unused PDF image resources are ignored.
    if not config.enabled or not page.get_image_info():
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
                return _recognize(upright_page, config, tessdata, full=not has_text)
        return _recognize(page, config, tessdata, full=not has_text)
    except Exception as exc:
        # MuPDF's OCR errors may be FzErrorBase, not RuntimeError.
        message = (
            f"{page.number + 1}페이지의 이미지 OCR을 실행하지 못했습니다. "
            "backend에서 python scripts/setup_ocr.py를 실행하거나 "
            "SPEAKSPOT_OCR_TESSDATA와 OCR 언어 데이터를 확인하세요."
        )
        if has_text:
            # An unavailable OCR engine must not break existing text PDFs.
            logger.warning("%s 기존 텍스트만 사용합니다. (%s)", message, exc)
            return native
        raise ValueError(message) from exc


def _recognize(page: fitz.Page, config: OCRConfig, tessdata: str, *, full: bool) -> dict:
    textpage = page.get_textpage_ocr(
        flags=TEXT_FLAGS, language=config.language, dpi=config.dpi,
        full=full, tessdata=tessdata,
    )
    # Partial OCR retains native text properties and adds image text once.
    return page.get_text("dict", textpage=textpage)
