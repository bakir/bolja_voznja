"""Extract full-question images from 5-per-page driving-test catalogs (katalog2/3)."""

from __future__ import annotations

from pathlib import Path

import cv2
import fitz
import numpy as np

from extract_answers import is_blue_fill, question_number_in_rect
from parse_katalog import get_horizontal_separators

RENDER_ZOOM = 2.0
CONTENT_PADDING = 2.0
QUESTIONS_PER_PAGE = 5


def pixmap_to_bgr(pix: fitz.Pixmap) -> np.ndarray:
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def question_frame(page: fitz.Page, number: int) -> fitz.Rect | None:
    pieces: list[fitz.Rect] = []
    for drawing in page.get_drawings():
        if not is_blue_fill(drawing.get("fill")):
            continue
        for item in drawing["items"]:
            if item[0] == "re":
                pieces.append(item[1])

    number_rects = [rect for rect in pieces if question_number_in_rect(page, rect) == number]
    if not number_rects:
        return None

    cx = sum((rect.x0 + rect.x1) / 2 for rect in number_rects) / len(number_rects)
    cy = sum((rect.y0 + rect.y1) / 2 for rect in number_rects) / len(number_rects)
    cluster = [
        rect
        for rect in pieces
        if abs((rect.x0 + rect.x1) / 2 - cx) < 15 and abs((rect.y0 + rect.y1) / 2 - cy) < 20
    ]
    return fitz.Rect(
        min(rect.x0 for rect in cluster),
        min(rect.y0 for rect in cluster),
        max(rect.x1 for rect in cluster),
        max(rect.y1 for rect in cluster),
    )


def page_question_numbers(page: fitz.Page) -> list[int]:
    numbers: set[int] = set()
    for drawing in page.get_drawings():
        if not is_blue_fill(drawing.get("fill")):
            continue
        for item in drawing["items"]:
            if item[0] != "re":
                continue
            number = question_number_in_rect(page, item[1])
            if number is not None:
                numbers.add(number)
    return sorted(numbers)


def red_separator_lines(page: fitz.Page) -> list[float]:
    ys: list[float] = []
    for drawing in page.get_drawings():
        color = drawing.get("color")
        if color is None or color[0] < 0.9 or color[1] > 0.1 or color[2] > 0.1:
            continue
        for item in drawing["items"]:
            if item[0] != "l":
                continue
            p1, p2 = item[1], item[2]
            if abs(p2.y - p1.y) < 1 and abs(p2.x - p1.x) > 100:
                ys.append((p1.y + p2.y) / 2)
    return sorted(set(round(y, 2) for y in ys))


def page_footer_y(page: fitz.Page) -> float:
    separators = get_horizontal_separators(page.get_drawings(), page.rect.height)
    footer_candidates = [y for y in separators if y > 700]
    return footer_candidates[0] if footer_candidates else page.rect.height - 25


def question_bounds_on_page(page: fitz.Page) -> dict[int, tuple[float, float]]:
    numbers = page_question_numbers(page)
    if not numbers:
        return {}

    frames = {number: question_frame(page, number) for number in numbers}
    red_lines = red_separator_lines(page)
    footer = page_footer_y(page)
    bounds: dict[int, tuple[float, float]] = {}

    for index, number in enumerate(numbers):
        frame = frames[number]
        if frame is None:
            continue
        y_top = frame.y0
        if index + 1 < len(numbers):
            next_top = frames[numbers[index + 1]].y0
            between = [y for y in red_lines if y_top < y < next_top + 1]
            y_bottom = between[-1] if between else next_top
        else:
            after = [y for y in red_lines if y > y_top]
            y_bottom = after[-1] if after else footer
        bounds[number] = (y_top, y_bottom)

    return bounds


def render_question(page: fitz.Page, y_min: float, y_max: float) -> np.ndarray:
    y_max = min(y_max + CONTENT_PADDING, page.rect.height)
    clip = fitz.Rect(0, y_min, page.rect.width, y_max)
    matrix = fitz.Matrix(RENDER_ZOOM, RENDER_ZOOM)
    pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
    return pixmap_to_bgr(pix)


def extract_all_images(pdf_path: Path, output_dir: Path) -> dict[int, str]:
    doc = fitz.open(pdf_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[int, str] = {}

    for page_index, page in enumerate(doc):
        bounds = question_bounds_on_page(page)
        count = len(bounds)
        if count not in (0, QUESTIONS_PER_PAGE) and not (
            page_index == len(doc) - 1 and count < QUESTIONS_PER_PAGE
        ):
            numbers = sorted(bounds)
            print(
                f"WARN page {page_index + 1}: expected {QUESTIONS_PER_PAGE} questions, "
                f"got {count} ({numbers})"
            )

        for number, (y_top, y_bottom) in bounds.items():
            image = render_question(page, y_top, y_bottom)
            path = output_dir / f"{number:03d}.png"
            cv2.imwrite(str(path), image)
            paths[number] = str(path)

    doc.close()
    return paths
