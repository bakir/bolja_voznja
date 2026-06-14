#!/usr/bin/env python3
"""
Extract full-question images from katalog1.pdf.

Katalog1 diagrams are vector graphics (no embedded raster blocks), so each
question is rendered from PDF coordinates. Questions that span a page break are
stitched vertically with OpenCV.

Boundary rules:
  - Top: blue question-number header (same anchor as answer extraction).
  - Bottom (same page as next question): horizontal separator above the next header.
  - Bottom (last slice on a page): last separator before the page-number footer.
  - Continuation slice on the next page only when that strip has visible content
    (handles split diagrams without relying on a blue anchor below).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import fitz
import numpy as np

from extract_answers import QuestionAnchor, collect_blue_anchors
from parse_katalog import get_horizontal_separators

RENDER_ZOOM = 2.0
FOOTER_MARGIN = 35.0
CONTENT_PADDING = 4.0
CONTINUATION_X_MIN = 500.0
CONTINUATION_PROBE_Y = 40.0
WHITE_THRESHOLD = 240


def pixmap_to_bgr(pix: fitz.Pixmap) -> np.ndarray:
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def region_has_content(
    page: fitz.Page,
    y_min: float,
    y_max: float,
    *,
    x_min: float = 0.0,
    threshold: int = WHITE_THRESHOLD,
) -> bool:
    if y_max - y_min < 3:
        return False
    matrix = fitz.Matrix(RENDER_ZOOM, RENDER_ZOOM)
    clip = fitz.Rect(x_min, y_min, page.rect.width - 5, y_max)
    pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
    samples = pix.samples
    for index in range(0, len(samples), pix.n):
        if (
            samples[index] < threshold
            or samples[index + 1] < threshold
            or samples[index + 2] < threshold
        ):
            return True
    return False


def question_bottom_on_page(
    page: fitz.Page,
    y_top: float,
    next_header_y: float | None,
) -> float:
    """Return the Y coordinate of the bottom edge for a question slice on this page."""
    separators = get_horizontal_separators(page.get_drawings(), page.rect.height)
    footer_y = page.rect.height - FOOTER_MARGIN

    if next_header_y is not None and next_header_y > y_top + 20:
        above_next = [y for y in separators if y_top < y <= next_header_y + 1]
        if above_next:
            return above_next[-1]
        return next_header_y

    candidates = [y for y in separators if y_top + 20 < y < footer_y]
    if candidates:
        return candidates[-1]
    return footer_y


def question_image_slices(
    doc: fitz.Document,
    start: QuestionAnchor,
    end: QuestionAnchor | None,
) -> list[tuple[int, float, float]]:
    """Return (page_index, y_min, y_max) bands covering one full question."""
    if end is None:
        page = doc[start.page]
        y_bottom = question_bottom_on_page(page, start.y_top, None)
        return [(start.page, start.y_top, y_bottom)]

    if start.page == end.page:
        page = doc[start.page]
        y_bottom = question_bottom_on_page(page, start.y_top, end.y_top)
        return [(start.page, start.y_top, y_bottom)]

    slices: list[tuple[int, float, float]] = []

    first_page = doc[start.page]
    first_bottom = question_bottom_on_page(first_page, start.y_top, None)
    slices.append((start.page, start.y_top, first_bottom))

    continuation_page = doc[end.page]
    if end.y_top > 20:
        cont_bottom = question_bottom_on_page(continuation_page, 0.0, end.y_top)
        if region_has_content(continuation_page, 0.0, cont_bottom):
            slices.append((end.page, 0.0, cont_bottom))
    elif region_has_content(
        continuation_page,
        0.0,
        CONTINUATION_PROBE_Y,
        x_min=CONTINUATION_X_MIN,
    ):
        cont_bottom = question_bottom_on_page(continuation_page, 0.0, end.y_top)
        cont_bottom = min(cont_bottom, CONTINUATION_PROBE_Y)
        if cont_bottom > 3:
            slices.append((end.page, 0.0, cont_bottom))

    return slices


def render_slice(doc: fitz.Document, page_index: int, y_min: float, y_max: float) -> np.ndarray:
    page = doc[page_index]
    y_max = min(y_max + CONTENT_PADDING, page.rect.height)
    clip = fitz.Rect(0, y_min, page.rect.width, y_max)
    matrix = fitz.Matrix(RENDER_ZOOM, RENDER_ZOOM)
    pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
    return pixmap_to_bgr(pix)


def stitch_slices(parts: list[np.ndarray]) -> np.ndarray:
    if not parts:
        raise ValueError("No image parts to stitch")
    if len(parts) == 1:
        return parts[0]

    width = max(part.shape[1] for part in parts)
    aligned: list[np.ndarray] = []
    for part in parts:
        if part.shape[1] == width:
            aligned.append(part)
            continue
        pad = np.full((part.shape[0], width - part.shape[1], 3), 255, dtype=np.uint8)
        aligned.append(np.hstack([part, pad]))
    return cv2.vconcat(aligned)


def extract_question_image(
    doc: fitz.Document,
    start: QuestionAnchor,
    end: QuestionAnchor | None,
    output_dir: Path,
    question_number: int,
) -> str:
    slices = question_image_slices(doc, start, end)
    parts = [render_slice(doc, page_index, y_min, y_max) for page_index, y_min, y_max in slices]
    image = stitch_slices(parts)

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{question_number:03d}.png"
    cv2.imwrite(str(path), image)
    return str(path)


def extract_all_images(pdf_path: Path, output_dir: Path) -> dict[int, str]:
    doc = fitz.open(pdf_path)
    anchors = collect_blue_anchors(doc)
    paths: dict[int, str] = {}

    for index, anchor in enumerate(anchors):
        end = anchors[index + 1] if index + 1 < len(anchors) else None
        paths[anchor.number] = extract_question_image(
            doc,
            anchor,
            end,
            output_dir,
            anchor.number,
        )

    doc.close()
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract full-question images from katalog1.pdf.",
    )
    parser.add_argument("pdf", nargs="?", default="katalog1.pdf")
    parser.add_argument(
        "-o",
        "--output-dir",
        default="katalog1_questionpics",
        help="Directory for PNG output (default: katalog1_questionpics)",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    output_dir = Path(args.output_dir)
    paths = extract_all_images(pdf_path, output_dir)

    multi_page = 0
    doc = fitz.open(pdf_path)
    anchors = collect_blue_anchors(doc)
    for index, anchor in enumerate(anchors):
        end = anchors[index + 1] if index + 1 < len(anchors) else None
        if len(question_image_slices(doc, anchor, end)) > 1:
            multi_page += 1
    doc.close()

    print(f"Extracted {len(paths)} question images to {output_dir}/")
    print(f"{multi_page} questions stitched from multiple page slices")


if __name__ == "__main__":
    main()
