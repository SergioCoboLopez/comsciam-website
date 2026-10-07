#!/usr/bin/env python3
"""Add one publication to Catalan, English, and Spanish at once.

Paper titles usually stay in the language of the article, so the same
bibliographic record is appended to each content/<language>/publications.yaml.
The introduction of the page is left untouched.

The new item is added at the end. Move it to the top of the list if it
should appear first.

Example:

    python scripts/update_publications.py \
        --id cobo-2026-example \
        --title "Title of the paper" \
        --authors "Cobo, S.; Other, A." \
        --year 2026 \
        --venue "Journal name" \
        --doi "10.0000/example"
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def language_ids() -> list[str]:
    site = yaml.safe_load((ROOT / "site.yaml").read_text(encoding="utf-8"))
    return [language["id"] for language in site["languages"]]


def publication_path(lang_id: str) -> Path:
    return ROOT / "content" / lang_id / "publications.yaml"


def existing_ids(path: Path) -> list[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "items" not in data:
        raise SystemExit(f"{path.relative_to(ROOT)} has no items list")
    return [item["id"] for item in data["items"]]


def main() -> None:
    parser = argparse.ArgumentParser(description="Append a publication in every language.")
    parser.add_argument("--id", required=True, help="Short stable id, such as surname-2026-keyword")
    parser.add_argument("--title", required=True)
    parser.add_argument("--authors", required=True)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--venue", required=True)
    parser.add_argument("--url", default="")
    parser.add_argument("--doi", default="")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the block that would be added, without writing files",
    )
    args = parser.parse_args()

    if not ID_RE.match(args.id):
        raise SystemExit("--id must start with a letter and use only letters, numbers, _ or -")

    item = {
        "id": args.id,
        "title": args.title,
        "authors": args.authors,
        "year": args.year,
        "venue": args.venue,
    }
    if args.url:
        item["url"] = args.url
    if args.doi:
        item["doi"] = args.doi

    dumped = yaml.safe_dump([item], allow_unicode=True, sort_keys=False).strip()
    # Keep the new item indented under the existing `items:` list.
    block = "\n".join(("  " + line if line else line) for line in dumped.splitlines()) + "\n"
    paths = [publication_path(lang_id) for lang_id in language_ids()]
    for path in paths:
        if not path.is_file():
            raise SystemExit(f"Missing {path.relative_to(ROOT)}")
        if args.id in existing_ids(path):
            raise SystemExit(f"{args.id} is already in {path.relative_to(ROOT)}")

    if args.dry_run:
        print(block, end="")
        return

    for path in paths:
        text = path.read_text(encoding="utf-8")
        if not text.endswith("\n"):
            text += "\n"
        path.write_text(text + block, encoding="utf-8")
        print(f"Added {args.id} to {path.relative_to(ROOT)}")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)
