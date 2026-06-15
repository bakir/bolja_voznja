#!/usr/bin/env python3
"""Extract full-question images from katalog2.pdf (5 questions per page)."""

from __future__ import annotations

import argparse
from pathlib import Path

from page_question_images import extract_all_images


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract question images from katalog2.pdf.")
    parser.add_argument("pdf", nargs="?", default="katalog2.pdf")
    parser.add_argument(
        "-o",
        "--output-dir",
        default="katalog2_questionpics",
        help="Directory for PNG output (default: katalog2_questionpics)",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    paths = extract_all_images(pdf_path, Path(args.output_dir))
    print(f"Extracted {len(paths)} question images to {args.output_dir}/")


if __name__ == "__main__":
    main()
