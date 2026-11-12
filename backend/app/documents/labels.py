"""Recover text in colored, rectangular diagram labels before normal OCR."""
from math import ceil

import numpy as np
import pymupdf as fitz


def label_crops(pixmap: fitz.Pixmap):
    """Yield RGB label crops and their pixel-space bounds, including padding.

    Work on a bounded color mask. Ordinary photos, plain text,
    separators and tiny components do not qualify as dark rectangular labels.
    """
    rgb = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, pixmap.n,
    )[:, :, :3]
    step = max(1, ceil(max(pixmap.width, pixmap.height) / 600))
    small = rgb[::step, ::step].astype(np.int16)
    mask = (small.max(2) - small.min(2) > 45) & (small.mean(2) < 225)
    height, width = mask.shape
    for start in np.flatnonzero(mask):
        y, x = divmod(int(start), width)
        if not mask[y, x]:
            continue
        pending = [(y, x)]
        mask[y, x] = False
        count = 0
        left = right = x
        top = bottom = y
        while pending:
            y, x = pending.pop()
            count += 1
            left, right = min(left, x), max(right, x)
            top, bottom = min(top, y), max(bottom, y)
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < height and 0 <= nx < width and mask[ny, nx]:
                    mask[ny, nx] = False
                    pending.append((ny, nx))
        w, h = right - left + 1, bottom - top + 1
        if (count < 30 or h * step < 12 or not 1.3 <= w / h <= 20
                or count / (w * h) < 0.6 or w * h > width * height * 0.35):
            continue
        # Trim panel borders and normalize the label's foreground polarity.
        left, top = left * step + step, top * step + step
        right = min(pixmap.width, (right + 1) * step) - step
        bottom = min(pixmap.height, (bottom + 1) * step) - step
        gray = rgb[top:bottom, left:right].mean(2)
        if not gray.size:
            continue
        background = np.median(gray)
        ink = gray > background + 30 if background < 185 else gray < background - 30
        if not 0.01 <= np.mean(ink) <= 0.35:
            continue
        pixels = np.pad(np.where(ink, 0, 255).astype(np.uint8), 8, constant_values=255)
        pixels = np.repeat(pixels[:, :, None], 3, axis=2)
        label = fitz.Pixmap(fitz.csRGB, pixels.shape[1], pixels.shape[0], pixels.tobytes(), False)
        label.set_dpi(pixmap.xres, pixmap.yres)
        yield label, fitz.Rect(left - 8, top - 8, right + 8, bottom + 8)
