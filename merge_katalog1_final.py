#!/usr/bin/env python3
"""Merge gemini-code-*.json chunks with katalog1_answers.json and PDF categories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from parse_katalog import parse_pdf

DEFAULT_ANSWERS = Path("katalog1_answers.json")
DEFAULT_OUTPUT = Path("katalog1_final.json")
DEFAULT_PDF = Path("katalog1.pdf")
GEMINI_GLOB = "gemini-code-*.json"


def load_gemini_chunks(pattern: str = GEMINI_GLOB) -> tuple[dict[int, dict], dict | None]:
    questions: dict[int, dict] = {}
    categories_info: dict | None = None

    for path in sorted(Path(".").glob(pattern)):
        data = json.loads(path.read_text(encoding="utf-8"))
        if categories_info is None and isinstance(data.get("categories_info"), dict):
            categories_info = data["categories_info"]

        block = data.get("questions", data)
        if not isinstance(block, dict):
            continue
        for key, value in block.items():
            if str(key).isdigit():
                questions[int(key)] = value

    return questions, categories_info


def build_final(
    answers_path: Path,
    pdf_path: Path,
    gemini_glob: str = GEMINI_GLOB,
) -> dict:
    answers = json.loads(answers_path.read_text(encoding="utf-8"))
    gemini_questions, categories_info = load_gemini_chunks(gemini_glob)
    pdf_questions = parse_pdf(pdf_path)

    missing_gemini = [number for number in range(1, 451) if number not in gemini_questions]
    if missing_gemini:
        raise SystemExit(f"Missing Gemini questions: {missing_gemini[:20]} ... ({len(missing_gemini)} total)")

    final_questions: dict[str, dict] = {}
    for number in range(1, 451):
        key = str(number)
        gemini = gemini_questions[number]
        answer_record = answers[key]
        pdf_record = pdf_questions.get(key, {})

        final_questions[key] = {
            "number": number,
            "question": gemini.get("question", "").strip(),
            "options": [str(option).strip() for option in gemini.get("options", [])],
            "categories": pdf_record.get("categories", ""),
            "answers": answer_record["answers"],
            "option_count": answer_record["option_count"],
            "question_pic": answer_record["question_pic"],
        }

    payload: dict = {"questions": final_questions}
    if categories_info:
        payload["categories_info"] = categories_info
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge Gemini chunks into katalog1_final.json")
    parser.add_argument("--answers", type=Path, default=DEFAULT_ANSWERS)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--gemini-glob", default=GEMINI_GLOB)
    args = parser.parse_args()

    if not args.answers.exists():
        raise SystemExit(f"Missing {args.answers}")
    if not args.pdf.exists():
        raise SystemExit(f"Missing {args.pdf}")

    payload = build_final(args.answers, args.pdf, args.gemini_glob)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    count = len(payload["questions"])
    print(f"Wrote {count} questions to {args.output}")
    if "categories_info" in payload:
        print(f"Included categories_info ({len(payload['categories_info'])} entries)")


if __name__ == "__main__":
    main()
