#!/usr/bin/env python3
"""
Extract correct-answer option numbers from katalog1.pdf vector graphics.

1. Blue rectangles + white question numbers → slice the PDF into question regions.
2. Inside each region, find X marks (two diagonal lines sharing a midpoint).
3. Find option labels 1, 2, 3… from text in the left column.
4. Each X midpoint → closest option label → count[42]=2,3
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import fitz

WHITE = 0xFFFFFF
OPTION_MARKER_X_MAX = 58.0
LINE_X_MAX = 70.0


def is_blue_fill(fill: tuple[float, float, float] | None) -> bool:
    if fill is None:
        return False
    r, g, b = fill
    return b >= 0.35 and r < 0.05 and g < 0.2


@dataclass
class QuestionAnchor:
    number: int
    page: int
    y_top: float
    y_bottom: float


@dataclass
class PageSlice:
    page: int
    y_min: float
    y_max: float


@dataclass
class OptionMarker:
    number: int
    page: int
    x: float
    y: float


def question_number_in_rect(page: fitz.Page, rect: fitz.Rect) -> int | None:
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                if span["color"] != WHITE:
                    continue
                text = span["text"].strip()
                if not text.isdigit():
                    continue
                bbox = span["bbox"]
                cx = (bbox[0] + bbox[2]) / 2
                cy = (bbox[1] + bbox[3]) / 2
                if rect.x0 - 3 <= cx <= rect.x1 + 3 and rect.y0 - 3 <= cy <= rect.y1 + 3:
                    return int(text)
    return None


def collect_blue_anchors(doc: fitz.Document) -> list[QuestionAnchor]:
    candidates: dict[int, QuestionAnchor] = {}

    for page_index, page in enumerate(doc):
        for drawing in page.get_drawings():
            if not is_blue_fill(drawing.get("fill")):
                continue
            for item in drawing["items"]:
                if item[0] != "re":
                    continue
                rect = item[1]
                number = question_number_in_rect(page, rect)
                if number is None:
                    continue
                area = rect.width * rect.height
                existing = candidates.get(number)
                if existing is None or area > (existing.y_bottom - existing.y_top) * 20:
                    candidates[number] = QuestionAnchor(
                        number=number,
                        page=page_index,
                        y_top=rect.y0,
                        y_bottom=rect.y1,
                    )

    ordered = sorted(candidates.values(), key=lambda anchor: (anchor.page, anchor.y_top))
    anchors: list[QuestionAnchor] = []
    last_number = 0
    for anchor in ordered:
        if anchor.number > last_number:
            anchors.append(anchor)
            last_number = anchor.number
    return anchors


def region_slices(
    doc: fitz.Document,
    start: QuestionAnchor,
    end: QuestionAnchor | None,
) -> list[PageSlice]:
    if end is None:
        last_page = len(doc) - 1
        return [PageSlice(last_page, start.y_bottom, doc[last_page].rect.height)]

    slices: list[PageSlice] = []
    if start.page == end.page:
        slices.append(PageSlice(start.page, start.y_bottom, end.y_top))
        return slices

    slices.append(PageSlice(start.page, start.y_bottom, doc[start.page].rect.height))
    for page_index in range(start.page + 1, end.page):
        slices.append(PageSlice(page_index, 0.0, doc[page_index].rect.height))
    slices.append(PageSlice(end.page, 0.0, end.y_top))
    return slices


def parse_option_marker_span(span: dict) -> tuple[int, float, float] | None:
    """Return (option number, center x, center y) for a left-column option label span."""
    if span["color"] == WHITE:
        return None
    if span["bbox"][0] >= OPTION_MARKER_X_MAX:
        return None

    text = span["text"]
    stripped = text.strip()
    bbox = span["bbox"]
    cy = (bbox[1] + bbox[3]) / 2

    if re.fullmatch(r"\d+", stripped):
        cx = (bbox[0] + bbox[2]) / 2
        return int(stripped), cx, cy

    # Rare layout: digit and option text share one span (e.g. Q64: "2     ne").
    match = re.match(r"^(\d+)\s+\S", text)
    if not match:
        return None

    number = int(match.group(1))
    span_width = bbox[2] - bbox[0]
    digit_right = bbox[0] + span_width * len(match.group(1)) / len(text)
    cx = (bbox[0] + digit_right) / 2
    return number, cx, cy


def find_option_markers(page: fitz.Page, page_index: int, y_min: float, y_max: float) -> list[OptionMarker]:
    markers: list[OptionMarker] = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                parsed = parse_option_marker_span(span)
                if parsed is None:
                    continue
                number, cx, cy = parsed
                if y_min < cy < y_max:
                    markers.append(OptionMarker(number, page_index, cx, cy))
    return sorted(markers, key=lambda marker: (marker.page, marker.y))


def find_diagonal_lines(drawings: list[dict], y_min: float, y_max: float) -> list[tuple]:
    lines: list[tuple] = []
    for drawing in drawings:
        if drawing.get("type") != "s":
            continue
        for item in drawing["items"]:
            if item[0] != "l":
                continue
            p1, p2 = item[1], item[2]
            if abs(p2.x - p1.x) <= 5 or abs(p2.y - p1.y) <= 5:
                continue
            cy = (p1.y + p2.y) / 2
            if y_min < cy < y_max and p1.x < LINE_X_MAX:
                lines.append((p1, p2))
    return lines


def segment_intersection(a1, a2, b1, b2) -> tuple[float, float] | None:
    x1, y1 = a1.x, a1.y
    x2, y2 = a2.x, a2.y
    x3, y3 = b1.x, b1.y
    x4, y4 = b2.x, b2.y

    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-9:
        return None

    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / denom
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / denom

    def on_segment(x: float, y: float, sx1: float, sy1: float, sx2: float, sy2: float) -> bool:
        return (
            min(sx1, sx2) - 1 <= x <= max(sx1, sx2) + 1
            and min(sy1, sy2) - 1 <= y <= max(sy1, sy2) + 1
        )

    if on_segment(px, py, x1, y1, x2, y2) and on_segment(px, py, x3, y3, x4, y4):
        return px, py
    return None


def find_x_midpoints(drawings: list[dict], y_min: float, y_max: float) -> list[tuple[float, float]]:
    """Midpoints where two diagonal lines cross (the center of an X mark)."""
    lines = find_diagonal_lines(drawings, y_min, y_max)
    centers: list[tuple[float, float]] = []

    for index, (p1a, p2a) in enumerate(lines):
        for p1b, p2b in lines[index + 1 :]:
            hit = segment_intersection(p1a, p2a, p1b, p2b)
            if hit:
                centers.append(hit)

    if centers:
        return centers

    counts: dict[tuple[int, int], int] = defaultdict(int)
    for p1, p2 in lines:
        mx = round((p1.x + p2.x) / 2)
        my = round((p1.y + p2.y) / 2)
        counts[(mx, my)] += 1

    return [(float(mx), float(my)) for (mx, my), count in counts.items() if count >= 2]


def point_distance(
    page_a: int,
    x_a: float,
    y_a: float,
    page_b: int,
    x_b: float,
    y_b: float,
) -> float:
    if page_a != page_b:
        return 1_000_000.0 + abs(y_a - y_b)
    return ((x_a - x_b) ** 2 + (y_a - y_b) ** 2) ** 0.5


def match_x_midpoint(
    page: int,
    x: float,
    y: float,
    markers: list[OptionMarker],
) -> dict:
    distances = [
        {
            "option": marker.number,
            "x": round(marker.x, 2),
            "y": round(marker.y, 2),
            "page": marker.page,
            "distance": round(point_distance(page, x, y, marker.page, marker.x, marker.y), 2),
        }
        for marker in markers
    ]
    distances.sort(key=lambda item: item["distance"])

    matched = distances[0] if distances else None
    return {
        "page": page,
        "x": round(x, 2),
        "y": round(y, 2),
        "matched_option": matched["option"] if matched else None,
        "distance": matched["distance"] if matched else None,
        "distances_to_options": distances,
    }


def extract_question_detail(
    doc: fitz.Document,
    slices: list[PageSlice],
) -> dict:
    markers: list[OptionMarker] = []
    x_points: list[tuple[int, float, float]] = []

    for page_slice in slices:
        page = doc[page_slice.page]
        drawings = page.get_drawings()
        markers.extend(
            find_option_markers(page, page_slice.page, page_slice.y_min, page_slice.y_max)
        )
        for mx, my in find_x_midpoints(drawings, page_slice.y_min, page_slice.y_max):
            x_points.append((page_slice.page, mx, my))

    x_marks = [match_x_midpoint(page, mx, my, markers) for page, mx, my in x_points]
    answers = sorted(
        {
            mark["matched_option"]
            for mark in x_marks
            if mark["matched_option"] is not None
        }
    )

    return {
        "answers": answers,
        "option_labels": [
            {
                "number": marker.number,
                "page": marker.page,
                "x": round(marker.x, 2),
                "y": round(marker.y, 2),
            }
            for marker in markers
        ],
        "x_marks": x_marks,
    }


def extract_all_answers(pdf_path: Path) -> dict[int, list[int]]:
    detail = extract_all_detail(pdf_path)
    return {number: payload["answers"] for number, payload in detail.items()}


def extract_all_detail(pdf_path: Path) -> dict[int, dict]:
    doc = fitz.open(pdf_path)
    anchors = collect_blue_anchors(doc)
    detail: dict[int, dict] = {}

    for index, anchor in enumerate(anchors):
        end = anchors[index + 1] if index + 1 < len(anchors) else None
        slices = region_slices(doc, anchor, end)
        detail[anchor.number] = extract_question_detail(doc, slices)

    doc.close()
    return detail


def format_answer_line(question_number: int, option_numbers: list[int]) -> str:
    if not option_numbers:
        return f"count[{question_number}]="
    return f"count[{question_number}]={','.join(str(n) for n in option_numbers)}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract correct answer option numbers (1, 2, 3…) from X marks in a driving-test PDF.",
    )
    parser.add_argument("pdf", nargs="?", default="katalog1.pdf")
    parser.add_argument(
        "-o",
        "--output",
        default="answers_debug.json",
        help="JSON path with answers + midpoints + distances (default: answers_debug.json)",
    )
    parser.add_argument(
        "--answers-only",
        action="store_true",
        help="Write only {question: [answers]} to JSON, no midpoint debug data",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    detail = extract_all_detail(pdf_path)

    for number in sorted(detail):
        print(format_answer_line(number, detail[number]["answers"]))

    print(
        f"\n# {len(detail)} questions, "
        f"{sum(1 for payload in detail.values() if payload['answers'])} with at least one answer"
    )

    if args.answers_only:
        payload = {str(number): data["answers"] for number, data in detail.items()}
    else:
        payload = {str(number): data for number, data in detail.items()}

    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
