#!/usr/bin/env python3
"""
Extract answers and question images from katalog3.pdf.

Same layout as katalog2 (right-side diagrams, option column at x≈72).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import fitz

import extract_answers as ea
from extract_answers import (
    collect_blue_anchors,
    extract_question_detail,
    format_answer_line,
    region_slices,
)
from pdf_question_pics import extract_question_pic

KATALOG3_OPTION_MARKER_X_MAX = 95.0
KATALOG3_LINE_X_MAX = 95.0
IMAGE_X_MIN = 350.0
DISTANCE_WARN_THRESHOLD = 3.0


def configure_katalog3_geometry() -> None:
    ea.OPTION_MARKER_X_MAX = KATALOG3_OPTION_MARKER_X_MAX
    ea.LINE_X_MAX = KATALOG3_LINE_X_MAX


def find_question_image(page_dict: dict, y_min: float, y_max: float) -> dict | None:
    best: dict | None = None
    best_area = 0.0

    for block in page_dict["blocks"]:
        if block["type"] != 1:
            continue
        bbox = block["bbox"]
        if bbox[0] <= IMAGE_X_MIN:
            continue
        cy = (bbox[1] + bbox[3]) / 2
        if not (y_min < cy < y_max):
            continue
        area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        if area > best_area:
            best = block
            best_area = area

    return best


def extract_question_image(
    doc: fitz.Document,
    slices,
    images_dir: Path,
    question_number: int,
) -> str | None:
    for page_slice in slices:
        page = doc[page_slice.page]
        block = find_question_image(
            page.get_text("dict"),
            page_slice.y_min,
            page_slice.y_max,
        )
        if block is None:
            continue

        ext = block.get("ext") or "png"
        filename = f"{question_number:03d}.{ext}"
        path = images_dir / filename
        path.write_bytes(block["image"])
        return str(path)

    return None


def build_answer_record(payload: dict) -> dict:
    record = {
        "answers": payload["answers"],
        "option_count": len(payload["option_labels"]),
    }
    if payload.get("image") is not None:
        record["image"] = payload["image"]
    if payload.get("question_pic") is not None:
        record["question_pic"] = payload["question_pic"]
    return record


def print_large_distance_warnings(detail: dict[int, dict]) -> int:
    count = 0
    for question_number in sorted(detail):
        for mark in detail[question_number]["x_marks"]:
            distance = mark.get("distance")
            if distance is None or distance <= DISTANCE_WARN_THRESHOLD:
                continue
            count += 1
            option = mark.get("matched_option")
            print(
                f"WARN Q{question_number}: X at ({mark['x']}, {mark['y']}) "
                f"→ option {option}, distance={distance}"
            )
    return count


def extract_katalog3(
    pdf_path: Path,
    images_dir: Path,
    questionpics_dir: Path,
) -> dict[int, dict]:
    configure_katalog3_geometry()
    doc = fitz.open(pdf_path)
    anchors = collect_blue_anchors(doc)
    results: dict[int, dict] = {}

    images_dir.mkdir(parents=True, exist_ok=True)
    questionpics_dir.mkdir(parents=True, exist_ok=True)

    for index, anchor in enumerate(anchors):
        end = anchors[index + 1] if index + 1 < len(anchors) else None
        slices = region_slices(doc, anchor, end)
        detail = extract_question_detail(doc, slices)
        image_path = extract_question_image(doc, slices, images_dir, anchor.number)
        question_pic_path = extract_question_pic(
            doc, anchor, end, questionpics_dir, anchor.number
        )
        results[anchor.number] = {
            **detail,
            "image": image_path,
            "question_pic": question_pic_path,
        }

    doc.close()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract answers and images from katalog3.pdf.",
    )
    parser.add_argument("pdf", nargs="?", default="katalog3.pdf")
    parser.add_argument(
        "-o",
        "--output",
        default="katalog3_answers.json",
        help="JSON output path (default: katalog3_answers.json)",
    )
    parser.add_argument(
        "--images-dir",
        default="katalog3_images",
        help="Directory for embedded diagram images (default: katalog3_images)",
    )
    parser.add_argument(
        "--questionpics-dir",
        default="katalog3_questionpics",
        help="Directory for full question PDF cutouts (default: katalog3_questionpics)",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    images_dir = Path(args.images_dir)
    questionpics_dir = Path(args.questionpics_dir)
    detail = extract_katalog3(pdf_path, images_dir, questionpics_dir)

    answers = {
        str(number): build_answer_record(payload)
        for number, payload in detail.items()
    }

    for number in sorted(detail):
        print(format_answer_line(number, detail[number]["answers"]))

    warned = print_large_distance_warnings(detail)
    if warned:
        print(f"\n# {warned} X mark(s) with distance > {DISTANCE_WARN_THRESHOLD}")

    missing_images = [number for number, payload in detail.items() if not payload.get("image")]
    if missing_images:
        print(f"\n# missing diagram images for questions: {missing_images}")

    with_answers = sum(1 for record in answers.values() if record["answers"])
    print(f"\n# {len(answers)} questions, {with_answers} with at least one answer")
    print(f"# saved {len(answers) - len(missing_images)} diagram images to {images_dir}/")
    print(f"# saved {len(answers)} question cutouts to {questionpics_dir}/")

    Path(args.output).write_text(
        json.dumps(answers, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
