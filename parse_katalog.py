#!/usr/bin/env python3
"""
Parse driving-test questions from katalog1.pdf using PyMuPDF.

Standard text extraction is insufficient because checkboxes and marked answers
are vector graphics. This script combines text spans (with font/color metadata)
and drawing primitives (separators, checkbox squares, diagonal X marks).
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

import fitz

RED_COLOR = 0xFF0000
WHITE_COLOR = 0xFFFFFF
OPTION_TEXT_X_MIN = 65
CHECKBOX_X_MAX = 58
CATEGORY_X_MIN = 400
CHECKBOX_BOUNDS_PADDING = 6
CHECKBOX_ENDPOINT_PADDING = 4
OPTION_Y_MATCH_TOLERANCE = 35
OPTION_MARKER_X_MAX = 58


def is_bold(font: str) -> bool:
    return "Bold" in font


def is_page_number_span(span: dict) -> bool:
    if span["font"] == "Calibri":
        return True
    bbox = span["bbox"]
    return bbox[1] > 725 and bbox[0] > 500


def is_horizontal_separator(rect: fitz.Rect, fill: tuple | None) -> bool:
    width = abs(rect.x1 - rect.x0)
    height = abs(rect.y1 - rect.y0)
    return fill == (0.0, 0.0, 0.0) and height < 1.5 and width > 200


def get_horizontal_separators(drawings: list[dict], page_height: float) -> list[float]:
    """Return Y-coordinates of long horizontal black lines that segment questions."""
    ys: list[float] = []
    for drawing in drawings:
        fill = drawing.get("fill")
        for item in drawing["items"]:
            if item[0] != "re":
                continue
            rect = item[1]
            if is_horizontal_separator(rect, fill):
                ys.append(rect.y0)

    ys = sorted(set(ys))
    if not ys:
        return [0.0, page_height]
    if ys[0] > 5:
        ys = [0.0] + ys
    if ys[-1] < page_height - 5:
        ys.append(page_height)
    return ys


def iter_spans(page: fitz.Page) -> list[dict]:
    spans: list[dict] = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                if is_page_number_span(span):
                    continue
                text = span["text"]
                if not text.strip() and text != " ":
                    continue
                bbox = span["bbox"]
                spans.append(
                    {
                        "text": text,
                        "font": span["font"],
                        "color": span["color"],
                        "x0": bbox[0],
                        "y0": bbox[1],
                        "x1": bbox[2],
                        "y1": bbox[3],
                        "cy": (bbox[1] + bbox[3]) / 2,
                    }
                )
    return spans


def cluster_tiny_rects(rects: list[fitz.Rect]) -> list[fitz.Rect]:
    """Merge pixel-sized checkbox border fragments into bounding boxes."""
    if not rects:
        return []

    rects = sorted(rects, key=lambda r: (r.y0, r.x0))
    clusters: list[list[fitz.Rect]] = [[rects[0]]]

    for rect in rects[1:]:
        prev = clusters[-1][-1]
        prev_cy = (prev.y0 + prev.y1) / 2
        cy = (rect.y0 + rect.y1) / 2
        if abs(cy - prev_cy) <= 10:
            clusters[-1].append(rect)
        else:
            clusters.append([rect])

    boxes: list[fitz.Rect] = []
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        x0 = min(r.x0 for r in cluster)
        x1 = max(r.x1 for r in cluster)
        y0 = min(r.y0 for r in cluster)
        y1 = max(r.y1 for r in cluster)
        width = x1 - x0
        if width < 10:
            continue
        if (y1 - y0) < 12:
            y1 = y0 + 12
        boxes.append(fitz.Rect(x0, y0, x1, y1))

    merged: list[fitz.Rect] = []
    for box in sorted(boxes, key=lambda r: r.y0):
        if merged and box.y0 - merged[-1].y1 < 5:
            prev = merged[-1]
            candidate = fitz.Rect(
                min(prev.x0, box.x0),
                min(prev.y0, box.y0),
                max(prev.x1, box.x1),
                max(prev.y1, box.y1),
            )
            if candidate.y1 - candidate.y0 <= 16:
                merged[-1] = candidate
            else:
                merged.append(box)
        else:
            merged.append(box)
    return merged


def get_checkbox_squares(drawings: list[dict], y_min: float, y_max: float) -> list[fitz.Rect]:
    """Find checkbox squares (clusters of tiny black rectangles) in a question block."""
    tiny: list[fitz.Rect] = []
    for drawing in drawings:
        if drawing.get("fill") != (0.0, 0.0, 0.0):
            continue
        for item in drawing["items"]:
            if item[0] != "re":
                continue
            rect = item[1]
            width = abs(rect.x1 - rect.x0)
            height = abs(rect.y1 - rect.y0)
            if width >= 2 or height >= 2:
                continue
            if rect.x0 > CHECKBOX_X_MAX or rect.x0 < 30:
                continue
            cy = (rect.y0 + rect.y1) / 2
            if y_min < cy < y_max:
                tiny.append(rect)

    return sorted(cluster_tiny_rects(tiny), key=lambda r: r.y0)


def get_diagonal_lines(drawings: list[dict], y_min: float, y_max: float) -> list[tuple]:
    lines: list[tuple] = []
    for drawing in drawings:
        if drawing.get("type") != "s":
            continue
        for item in drawing["items"]:
            if item[0] != "l":
                continue
            p1, p2 = item[1], item[2]
            dx = abs(p2.x - p1.x)
            dy = abs(p2.y - p1.y)
            if dx <= 5 or dy <= 5:
                continue
            cx = (p1.x + p2.x) / 2
            cy = (p1.y + p2.y) / 2
            if y_min < cy < y_max and cx < CHECKBOX_X_MAX:
                lines.append((p1, p2, cx, cy))
    return lines


def segment_intersection(a1, a2, b1, b2) -> tuple[float, float] | None:
    """Return intersection point of two line segments, if they cross inside both."""
    x1, y1 = a1.x, a1.y
    x2, y2 = a2.x, a2.y
    x3, y3 = b1.x, b1.y
    x4, y4 = b2.x, b2.y

    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-9:
        return None

    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / denom
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / denom

    def on_segment(x, y, sx1, sy1, sx2, sy2) -> bool:
        return (
            min(sx1, sx2) - 1 <= x <= max(sx1, sx2) + 1
            and min(sy1, sy2) - 1 <= y <= max(sy1, sy2) + 1
        )

    if on_segment(px, py, x1, y1, x2, y2) and on_segment(px, py, x3, y3, x4, y4):
        return px, py
    return None


def get_x_mark_centers(drawings: list[dict], y_min: float, y_max: float) -> list[tuple[float, float]]:
    """Find X-mark centers from intersecting diagonals; fall back to shared midpoints."""
    diagonals = get_diagonal_lines(drawings, y_min, y_max)
    centers: list[tuple[float, float]] = []

    for i, (_, _, _, _) in enumerate(diagonals):
        p1a, p2a, _, _ = diagonals[i]
        for j in range(i + 1, len(diagonals)):
            p1b, p2b, _, _ = diagonals[j]
            hit = segment_intersection(p1a, p2a, p1b, p2b)
            if hit:
                centers.append(hit)

    if centers:
        return centers

    counts: dict[tuple[int, int], int] = defaultdict(int)
    for _, _, cx, cy in diagonals:
        counts[(round(cx), round(cy))] += 1
    return [(float(cx), float(cy)) for (cx, cy), count in counts.items() if count >= 2]


def rect_center(rect: fitz.Rect) -> tuple[float, float]:
    return (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2


def expand_rect(rect: fitz.Rect, padding: float) -> fitz.Rect:
    return fitz.Rect(
        rect.x0 - padding,
        rect.y0 - padding,
        rect.x1 + padding,
        rect.y1 + padding,
    )


def point_in_rect(x: float, y: float, rect: fitz.Rect, padding: float = 0.0) -> bool:
    bounds = expand_rect(rect, padding) if padding else rect
    return bounds.x0 <= x <= bounds.x1 and bounds.y0 <= y <= bounds.y1


def diagonal_fully_inside_checkbox(p1, p2, square: fitz.Rect) -> bool:
    bounds = expand_rect(square, CHECKBOX_ENDPOINT_PADDING)
    return bounds.contains(p1) and bounds.contains(p2)


def checkbox_contains_x_midpoint(square: fitz.Rect, diagonals: list[tuple]) -> bool:
    """True when the crossing point of an X lies inside this checkbox rectangle."""
    for index, (p1a, p2a, _, _) in enumerate(diagonals):
        for p1b, p2b, _, _ in diagonals[index + 1 :]:
            hit = segment_intersection(p1a, p2a, p1b, p2b)
            if hit and point_in_rect(hit[0], hit[1], square, CHECKBOX_BOUNDS_PADDING):
                return True

    midpoint_counts: dict[tuple[int, int], int] = defaultdict(int)
    for p1, p2, cx, cy in diagonals:
        if diagonal_fully_inside_checkbox(p1, p2, square):
            midpoint_counts[(round(cx), round(cy))] += 1
        elif point_in_rect(cx, cy, square, CHECKBOX_BOUNDS_PADDING):
            midpoint_counts[(round(cx), round(cy))] += 1

    return any(count >= 2 for count in midpoint_counts.values())


def squares_from_diagonal_clusters(diagonals: list[tuple]) -> list[fitz.Rect]:
    """Build approximate checkbox bounds from groups of crossing diagonal strokes."""
    midpoint_groups: dict[tuple[int, int], list[tuple]] = defaultdict(list)
    for p1, p2, cx, cy in diagonals:
        midpoint_groups[(round(cx), round(cy))].append((p1, p2))

    squares: list[fitz.Rect] = []
    for lines in midpoint_groups.values():
        if len(lines) < 2:
            continue
        xs = [point.x for p1, p2 in lines for point in (p1, p2)]
        ys = [point.y for p1, p2 in lines for point in (p1, p2)]
        squares.append(fitz.Rect(min(xs), min(ys), max(xs), max(ys)))
    return sorted(squares, key=lambda rect: rect.y0)


def get_checkbox_squares_with_fallback(
    drawings: list[dict],
    y_min: float,
    y_max: float,
) -> list[fitz.Rect]:
    squares = get_checkbox_squares(drawings, y_min, y_max)
    if squares:
        return squares
    diagonals = get_diagonal_lines(drawings, y_min, y_max)
    return squares_from_diagonal_clusters(diagonals)


def find_marked_checkbox_indices(
    drawings: list[dict],
    y_min: float,
    y_max: float,
) -> list[int]:
    """
    Return checkbox indices that contain an X mark.

    Each checkbox is a reconstructed square from border vectors. An answer is marked
    when two diagonal lines cross inside that square's bounds (intersection point or
    both strokes contained in the padded rectangle).
    """
    diagonals = get_diagonal_lines(drawings, y_min, y_max)
    squares = get_checkbox_squares_with_fallback(drawings, y_min, y_max)
    if not squares:
        return []

    marked = {
        index
        for index, square in enumerate(squares)
        if checkbox_contains_x_midpoint(square, diagonals)
    }

    if marked:
        return sorted(marked)

    for cx, cy in get_x_mark_centers(drawings, y_min, y_max):
        nearest = min(range(len(squares)), key=lambda i: abs(rect_center(squares[i])[1] - cy))
        if abs(rect_center(squares[nearest])[1] - cy) <= OPTION_Y_MATCH_TOLERANCE:
            marked.add(nearest)

    return sorted(marked)


def get_correct_indices(
    drawings: list[dict],
    y_min: float,
    y_max: float,
    option_ys: list[float],
) -> list[int]:
    """
    Map marked checkboxes to option indices.

    Checkbox squares are reconstructed from vector border fragments and sorted by Y.
    For each square we ask whether diagonal lines are drawn inside its bounds; if so,
    that option index is correct. When square/option counts differ, match by Y.
    """
    squares = get_checkbox_squares_with_fallback(drawings, y_min, y_max)
    marked_local = find_marked_checkbox_indices(drawings, y_min, y_max)
    if not marked_local:
        return []

    if len(squares) == len(option_ys):
        return marked_local

    correct: list[int] = []
    for square_index in marked_local:
        if square_index >= len(squares):
            continue
        _, square_cy = rect_center(squares[square_index])
        option_index = min(range(len(option_ys)), key=lambda i: abs(option_ys[i] - square_cy))
        if abs(option_ys[option_index] - square_cy) <= OPTION_Y_MATCH_TOLERANCE:
            correct.append(option_index)
    return sorted(set(correct))


def separator_above_anchor(drawings: list[dict], page_height: float, anchor_y: float) -> float:
    separators = get_horizontal_separators(drawings, page_height)
    above = [y for y in separators if y <= anchor_y + 1]
    return above[-1] if above else 0.0


def collect_question_anchors(doc: fitz.Document) -> list[dict]:
    """Collect question-number anchors in reading order, keeping strictly increasing IDs."""
    anchors: list[dict] = []
    for page_index, page in enumerate(doc):
        for span in iter_spans(page):
            if span["color"] != WHITE_COLOR:
                continue
            text = span["text"].strip()
            if not text.isdigit():
                continue
            anchors.append(
                {
                    "number": int(text),
                    "page": page_index,
                    "y": span["y0"],
                    "cy": span["cy"],
                }
            )

    anchors.sort(key=lambda anchor: (anchor["page"], anchor["y"]))
    monotonic: list[dict] = []
    last_number = 0
    for anchor in anchors:
        if anchor["number"] > last_number:
            monotonic.append(anchor)
            last_number = anchor["number"]
    return monotonic


def block_y_bounds(
    doc: fitz.Document,
    start_anchor: dict,
    end_anchor: dict | None,
) -> tuple[int, float, int, float]:
    start_page = start_anchor["page"]
    start_drawings = doc[start_page].get_drawings()
    start_y = separator_above_anchor(start_drawings, doc[start_page].rect.height, start_anchor["y"])

    if end_anchor is None:
        end_page = len(doc) - 1
        end_y = doc[end_page].rect.height
    else:
        end_page = end_anchor["page"]
        end_y = end_anchor["y"]

    return start_page, start_y, end_page, end_y


def collect_block_spans(
    doc: fitz.Document,
    start_page: int,
    start_y: float,
    end_page: int,
    end_y: float,
) -> list[dict]:
    spans: list[dict] = []
    for page_index in range(start_page, end_page + 1):
        for span in iter_spans(doc[page_index]):
            if page_index == start_page == end_page:
                if not (start_y < span["cy"] < end_y):
                    continue
            elif page_index == start_page:
                if span["cy"] <= start_y:
                    continue
            elif page_index == end_page:
                if span["cy"] >= end_y:
                    continue
            span["page"] = page_index
            spans.append(span)
    return spans


def collect_block_drawing_slices(
    doc: fitz.Document,
    start_page: int,
    start_y: float,
    end_page: int,
    end_y: float,
) -> list[tuple[int, list[dict], float, float]]:
    slices: list[tuple[int, list[dict], float, float]] = []
    for page_index in range(start_page, end_page + 1):
        page_height = doc[page_index].rect.height
        if page_index == start_page == end_page:
            y_min, y_max = start_y, end_y
        elif page_index == start_page:
            y_min, y_max = start_y, page_height
        elif page_index == end_page:
            y_min, y_max = 0.0, end_y
        else:
            y_min, y_max = 0.0, page_height
        slices.append((page_index, doc[page_index].get_drawings(), y_min, y_max))
    return slices


def is_category_span(span: dict) -> bool:
    return span["color"] == RED_COLOR and span["x0"] >= CATEGORY_X_MIN


def is_option_marker_span(span: dict) -> bool:
    if span["color"] == WHITE_COLOR:
        return False
    return span["x0"] < OPTION_MARKER_X_MAX and bool(re.fullmatch(r"\d+", span["text"].strip()))


def is_option_content_span(span: dict) -> bool:
    if is_category_span(span):
        return False
    if is_bold(span["font"]) and span["color"] != WHITE_COLOR:
        return False
    if span["color"] == WHITE_COLOR:
        return False
    if span["x0"] < OPTION_TEXT_X_MIN:
        return False
    return True


def marker_index_for_content_span(span: dict, markers: list[dict]) -> int:
    """Assign a content line to the nearest option marker (handles wrapped lines above the number)."""
    page, y = span["page"], span["y0"]
    best_index = 0
    best_distance = float("inf")
    for index, marker in enumerate(markers):
        page_delta = abs(page - marker["page"]) * 10_000
        distance = page_delta + abs(y - marker["y0"])
        if distance < best_distance:
            best_distance = distance
            best_index = index
    return best_index


def finalize_option_endings(options: list[str]) -> list[str]:
    """Ensure each answer ends with ';' and the last answer ends with '.'."""
    if not options:
        return []

    finalized: list[str] = []
    for index, option in enumerate(options):
        text = re.sub(r"\s+", " ", option).strip()
        if not text:
            continue
        text = text.rstrip(";.").strip()
        if index < len(options) - 1:
            finalized.append(f"{text};")
        else:
            finalized.append(f"{text}.")
    return finalized


def split_options_on_semicolons(blob: str) -> list[str]:
    """Split merged option text on semicolons (answers end with ';', last with '.')."""
    parts = [part.strip() for part in blob.split(";")]
    parts = [part for part in parts if part]
    if not parts:
        return []
    return finalize_option_endings(parts)


def extract_grouped_options(
    block_spans: list[dict],
) -> tuple[list[str], list[tuple[int, float]]]:
    """
    Build options from answer text separated by semicolons.

    Multi-line answers are merged first: spans are grouped by the nearest checkbox
    number (1, 2, 3…), joined in reading order, then split on ';'. Each answer
    ends with ';' except the last, which ends with '.'.
    """
    markers = sorted(
        [span for span in block_spans if is_option_marker_span(span)],
        key=lambda span: (span["page"], span["y0"]),
    )
    content_spans = sorted(
        [span for span in block_spans if is_option_content_span(span)],
        key=lambda span: (span["page"], span["y0"], span["x0"]),
    )
    if not content_spans:
        return [], []

    if markers:
        grouped: dict[int, list[dict]] = defaultdict(list)
        for span in content_spans:
            grouped[marker_index_for_content_span(span, markers)].append(span)
        raw_options: list[str] = []
        for index in sorted(grouped.keys()):
            parts = sorted(grouped[index], key=lambda span: (span["page"], span["y0"], span["x0"]))
            text = re.sub(r"\s+", " ", "".join(part["text"] for part in parts)).strip()
            if text:
                raw_options.append(text)
        options = finalize_option_endings(raw_options)
    else:
        blob = re.sub(r"\s+", " ", "".join(span["text"] for span in content_spans)).strip()
        options = split_options_on_semicolons(blob)

    option_positions: list[tuple[int, float]] = []
    if markers and len(markers) == len(options):
        option_positions = [(marker["page"], marker["cy"]) for marker in markers]
    elif markers:
        for index, marker in enumerate(markers[: len(options)]):
            option_positions.append((marker["page"], marker["cy"]))
    else:
        for span in content_spans[: len(options)]:
            option_positions.append((span["page"], span["cy"]))

    while len(option_positions) < len(options):
        last = option_positions[-1] if option_positions else (content_spans[0]["page"], content_spans[0]["cy"])
        option_positions.append((last[0], last[1] + 17))

    return options, option_positions[: len(options)]


def normalize_for_compare(text: str) -> str:
    return re.sub(r"\s+", "", text.casefold())


def span_text_for_audit(span: dict) -> str:
    return re.sub(r"\s+", " ", span["text"]).strip()


def is_ignorable_for_audit(span: dict) -> bool:
    text = span_text_for_audit(span)
    if not text:
        return True
    if span["color"] == WHITE_COLOR and text.isdigit():
        return True
    if is_option_marker_span(span):
        return True
    if span["x0"] > 500:
        return True
    return False


def audit_text_coverage(pdf_path: Path, questions: dict[str, dict]) -> dict:
    """
    Check whether meaningful block text was captured in question_text, options, or categories.

    Returns a report listing questions that still have uncaptured spans after parsing.
    Multi-line options should be fully merged before this audit runs.
    """
    doc = fitz.open(pdf_path)
    anchors = collect_question_anchors(doc)
    captured_blob_by_question: dict[str, str] = {}
    for number, payload in questions.items():
        captured_blob_by_question[number] = normalize_for_compare(
            " ".join(
                [
                    payload.get("question_text", ""),
                    *payload.get("options", []),
                    payload.get("categories", ""),
                ]
            )
        )

    gaps: list[dict] = []
    for index, anchor in enumerate(anchors):
        number = str(anchor["number"])
        end_anchor = anchors[index + 1] if index + 1 < len(anchors) else None
        start_page, start_y, end_page, end_y = block_y_bounds(doc, anchor, end_anchor)
        block_spans = collect_block_spans(doc, start_page, start_y, end_page, end_y)
        captured_blob = captured_blob_by_question.get(number, "")

        uncaptured: list[str] = []
        raw_chars = 0
        captured_chars = 0

        for span in block_spans:
            if is_ignorable_for_audit(span):
                continue
            text = span_text_for_audit(span)
            normalized = normalize_for_compare(text)
            if not normalized:
                continue

            raw_chars += len(normalized)
            if normalized in captured_blob:
                captured_chars += len(normalized)
            else:
                uncaptured.append(text)

        if uncaptured:
            gaps.append(
                {
                    "question": number,
                    "uncaptured_spans": uncaptured,
                    "coverage_ratio": round(captured_chars / raw_chars, 4) if raw_chars else 1.0,
                }
            )

    doc.close()
    return {
        "total_questions": len(anchors),
        "questions_with_gaps": len(gaps),
        "questions_complete": len(anchors) - len(gaps),
        "gaps": gaps,
    }


def get_correct_indices_for_block(
    drawing_slices: list[tuple[int, list[dict], float, float]],
    option_positions: list[tuple[int, float]],
) -> list[int]:
    if not option_positions:
        return []

    correct: list[int] = []
    for page_index, drawings, y_min, y_max in drawing_slices:
        page_option_indices = [
            index for index, (option_page, _) in enumerate(option_positions) if option_page == page_index
        ]
        if not page_option_indices:
            continue

        squares = get_checkbox_squares_with_fallback(drawings, y_min, y_max)
        marked_local = find_marked_checkbox_indices(drawings, y_min, y_max)
        option_ys = [option_positions[index][1] for index in page_option_indices]

        if marked_local:
            if len(squares) == len(page_option_indices):
                for local_index in marked_local:
                    if local_index < len(page_option_indices):
                        correct.append(page_option_indices[local_index])
            else:
                for square_index in marked_local:
                    if square_index >= len(squares):
                        continue
                    _, square_cy = rect_center(squares[square_index])
                    local_option = min(
                        range(len(option_ys)),
                        key=lambda i: abs(option_ys[i] - square_cy),
                    )
                    if abs(option_ys[local_option] - square_cy) <= OPTION_Y_MATCH_TOLERANCE:
                        correct.append(page_option_indices[local_option])
        else:
            for local_index in get_correct_indices(drawings, y_min, y_max, option_ys):
                if local_index < len(page_option_indices):
                    correct.append(page_option_indices[local_index])

    return sorted(set(correct))


def parse_question_block(
    block_spans: list[dict],
    drawing_slices: list[tuple[int, list[dict], float, float]],
) -> dict:
    bold_parts: list[str] = []
    for span in sorted(block_spans, key=lambda s: (s["page"], s["y0"], s["x0"])):
        if not is_bold(span["font"]):
            continue
        if span["color"] == WHITE_COLOR:
            continue
        if span["x0"] < OPTION_TEXT_X_MIN - 5:
            continue
        bold_parts.append(span["text"].strip())
    question_text = re.sub(r"\s+", " ", " ".join(bold_parts)).strip()

    categories = ""
    for span in block_spans:
        if not is_category_span(span):
            continue
        candidate = re.sub(r"\s+", "", span["text"].strip())
        if candidate:
            categories = candidate

    options, option_positions = extract_grouped_options(block_spans)
    correct_index = get_correct_indices_for_block(drawing_slices, option_positions)

    return {
        "question_text": question_text,
        "options": options,
        "correct_index": correct_index,
        "categories": categories,
    }


def parse_pdf(pdf_path: Path) -> dict[str, dict]:
    doc = fitz.open(pdf_path)
    questions: dict[str, dict] = {}
    anchors = collect_question_anchors(doc)

    for index, anchor in enumerate(anchors):
        end_anchor = anchors[index + 1] if index + 1 < len(anchors) else None
        start_page, start_y, end_page, end_y = block_y_bounds(doc, anchor, end_anchor)
        block_spans = collect_block_spans(doc, start_page, start_y, end_page, end_y)
        drawing_slices = collect_block_drawing_slices(doc, start_page, start_y, end_page, end_y)
        payload = parse_question_block(block_spans, drawing_slices)
        questions[str(anchor["number"])] = payload

    doc.close()
    return questions


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse driving test questions from a PDF catalog.")
    parser.add_argument(
        "pdf",
        nargs="?",
        default="katalog1.pdf",
        help="Path to the PDF file (default: katalog1.pdf)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="questions.json",
        help="Output JSON file path (default: questions.json)",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Print text-coverage audit after parsing",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    questions = parse_pdf(pdf_path)
    output_path = Path(args.output)

    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(questions, handle, ensure_ascii=False, indent=4)

    print(f"Parsed {len(questions)} questions from {pdf_path}")
    print(f"Wrote {output_path}")

    if args.audit:
        report = audit_text_coverage(pdf_path, questions)
        print(
            f"Text coverage: {report['questions_complete']}/{report['total_questions']} complete, "
            f"{report['questions_with_gaps']} with uncaptured spans"
        )
        for gap in report["gaps"][:10]:
            preview = "; ".join(gap["uncaptured_spans"][:3])
            print(f"  Q{gap['question']} ({gap['coverage_ratio']:.0%}): {preview}")
        if len(report["gaps"]) > 10:
            print(f"  ... and {len(report['gaps']) - 10} more")


if __name__ == "__main__":
    main()
