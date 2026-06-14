"""Render full-question cutouts from driving-test PDFs."""

from __future__ import annotations

from pathlib import Path

import fitz

from extract_answers import PageSlice, QuestionAnchor

RENDER_ZOOM = 2.0
IMAGE_X_MIN = 300.0
CONTENT_PADDING = 6.0


def _slice_content_bottom(
    page: fitz.Page,
    y_min: float,
    next_header_y: float | None,
    question_y_top: float,
) -> float:
    """Lowest y of question content on this page slice."""
    bottom = y_min

    for block in page.get_text("dict")["blocks"]:
        bbox = block["bbox"]
        if bbox[3] <= y_min:
            continue

        if block["type"] == 1 and bbox[0] > IMAGE_X_MIN:
            # Diagram on the right — include if it starts before the next header.
            if bbox[1] >= question_y_top and (
                next_header_y is None or bbox[1] < next_header_y
            ):
                bottom = max(bottom, bbox[3])
            continue

        if block["type"] != 0:
            continue

        for line in block["lines"]:
            for span in line["spans"]:
                span_bbox = span["bbox"]
                if span_bbox[3] <= y_min or span_bbox[1] < question_y_top:
                    continue
                if next_header_y is not None and span_bbox[1] >= next_header_y:
                    continue
                bottom = max(bottom, span_bbox[3])

    return bottom


def _next_header_y_on_page(
    start: QuestionAnchor,
    end: QuestionAnchor | None,
    page_index: int,
) -> float | None:
    if end is not None and end.page == page_index:
        return end.y_top
    return None


def question_pic_slices(
    doc: fitz.Document,
    start: QuestionAnchor,
    end: QuestionAnchor | None,
) -> list[PageSlice]:
    """Full question band from blue header top through all content on each page."""
    if end is None:
        last_page = len(doc) - 1
        slices = [PageSlice(last_page, start.y_top, doc[last_page].rect.height)]
    elif start.page == end.page:
        slices = [PageSlice(start.page, start.y_top, end.y_top)]
    else:
        slices = [PageSlice(start.page, start.y_top, doc[start.page].rect.height)]
        for page_index in range(start.page + 1, end.page):
            slices.append(PageSlice(page_index, 0.0, doc[page_index].rect.height))
        slices.append(PageSlice(end.page, 0.0, end.y_top))

    adjusted: list[PageSlice] = []
    for page_slice in slices:
        page = doc[page_slice.page]
        next_header_y = _next_header_y_on_page(start, end, page_slice.page)
        content_bottom = _slice_content_bottom(
            page,
            page_slice.y_min,
            next_header_y,
            start.y_top,
        )
        y_max = max(page_slice.y_max, content_bottom + CONTENT_PADDING)
        y_max = min(y_max, page.rect.height)
        adjusted.append(PageSlice(page_slice.page, page_slice.y_min, y_max))

    return adjusted


def render_page_slices(doc: fitz.Document, slices: list[PageSlice]) -> fitz.Pixmap:
    matrix = fitz.Matrix(RENDER_ZOOM, RENDER_ZOOM)
    pixmaps: list[fitz.Pixmap] = []

    for page_slice in slices:
        page = doc[page_slice.page]
        clip = fitz.Rect(0, page_slice.y_min, page.rect.width, page_slice.y_max)
        pixmaps.append(page.get_pixmap(matrix=matrix, clip=clip, alpha=False))

    if len(pixmaps) == 1:
        return pixmaps[0]

    width = pixmaps[0].width
    height = sum(pixmap.height for pixmap in pixmaps)
    combined = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False)
    combined.set_rect(combined.irect, (255, 255, 255))

    y_offset = 0
    for pixmap in pixmaps:
        combined.copy(pixmap, (0, y_offset))
        y_offset += pixmap.height

    return combined


def extract_question_pic(
    doc: fitz.Document,
    start: QuestionAnchor,
    end: QuestionAnchor | None,
    questionpics_dir: Path,
    question_number: int,
) -> str:
    slices = question_pic_slices(doc, start, end)
    pixmap = render_page_slices(doc, slices)
    path = questionpics_dir / f"{question_number:03d}.png"
    pixmap.save(path)
    return str(path)
