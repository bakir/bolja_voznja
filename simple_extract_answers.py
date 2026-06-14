#!/usr/bin/env python3
"""
Simple answer extraction from katalog1.pdf — answers only, no debug geometry.

Reuses extract_answers.py. Prints count[N]=... for every question and warns
when an X mark is matched with distance > 3 pt from the nearest option label.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from extract_answers import extract_all_detail, format_answer_line

DISTANCE_WARN_THRESHOLD = 3.0


def build_answer_record(payload: dict) -> dict:
    return {
        "answers": payload["answers"],
        "option_count": len(payload["option_labels"]),
    }


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract correct answers from a driving-test PDF (simple output).",
    )
    parser.add_argument("pdf", nargs="?", default="katalog1.pdf")
    parser.add_argument(
        "-o",
        "--output",
        default="answers.json",
        help="JSON path for {question: {answers, option_count}} (default: answers.json)",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    detail = extract_all_detail(pdf_path)
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

    Path(args.output).write_text(
        json.dumps(answers, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
