#!/usr/bin/env python3
"""
Extract answers and full-question PDF cutouts from katalog1.pdf.

Writes {answers, option_count, question_pic} per question and saves PNGs
to katalog1_questionpics/.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import fitz

from extract_answers import (
    collect_blue_anchors,
    extract_question_detail,
    format_answer_line,
    region_slices,
)
from pdf_question_pics import extract_question_pic

DISTANCE_WARN_THRESHOLD = 3.0


def build_answer_record(payload: dict) -> dict:
    record = {
        "answers": payload["answers"],
        "option_count": len(payload["option_labels"]),
        "question_pic": payload["question_pic"],
    }
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


def extract_katalog1(pdf_path: Path, questionpics_dir: Path) -> dict[int, dict]:
    doc = fitz.open(pdf_path)
    anchors = collect_blue_anchors(doc)
    results: dict[int, dict] = {}

    questionpics_dir.mkdir(parents=True, exist_ok=True)

    for index, anchor in enumerate(anchors):
        end = anchors[index + 1] if index + 1 < len(anchors) else None
        slices = region_slices(doc, anchor, end)
        detail = extract_question_detail(doc, slices)
        question_pic_path = extract_question_pic(
            doc, anchor, end, questionpics_dir, anchor.number
        )
        results[anchor.number] = {
            **detail,
            "question_pic": question_pic_path,
        }

    doc.close()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract answers and question cutouts from katalog1.pdf.",
    )
    parser.add_argument("pdf", nargs="?", default="katalog1.pdf")
    parser.add_argument(
        "-o",
        "--output",
        default="katalog1_answers.json",
        help="JSON output path (default: katalog1_answers.json)",
    )
    parser.add_argument(
        "--questionpics-dir",
        default="katalog1_questionpics",
        help="Directory for full question PDF cutouts (default: katalog1_questionpics)",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    questionpics_dir = Path(args.questionpics_dir)
    detail = extract_katalog1(pdf_path, questionpics_dir)

    answers = {
        str(number): build_answer_record(payload)
        for number, payload in detail.items()
    }

    for number in sorted(detail):
        print(format_answer_line(number, detail[number]["answers"]))

    warned = print_large_distance_warnings(detail)
    if warned:
        print(f"\n# {warned} X mark(s) with distance > {DISTANCE_WARN_THRESHOLD}")

    with_answers = sum(1 for record in answers.values() if record["answers"])
    print(f"\n# {len(answers)} questions, {with_answers} with at least one answer")
    print(f"# saved {len(answers)} question cutouts to {questionpics_dir}/")

    Path(args.output).write_text(
        json.dumps(answers, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
