#!/usr/bin/env python3
"""Add the group's OpenAlex works to content/publications.bib.

Reads each orcid in content/researchers.yaml, asks OpenAlex for that
author's works, and appends the ones that are not already in the BibTeX
file. An existing entry is never rewritten. A paper already present is
recognized by its DOI or its arXiv id.

Then refresh citation counts and rebuild:

    python3 scripts/fetch_publications.py --period last-five-years
    python3 scripts/update_publications.py
    python3 build.py

Periods: always, this-year, last-year, last-five-years.
last-five-years is the current calendar year and the four years before it.
--dry-run prints the same report and does not change the BibTeX file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from update_publications import (  # noqa: E402
    BIB_PATH,
    USER_AGENT,
    latex_to_text,
    normalize_doi,
    parse_bib,
    publication_from_entry,
)

ROOT = Path(__file__).resolve().parents[1]
RESEARCHERS_PATH = ROOT / "content" / "researchers.yaml"
PERIODS = ("always", "this-year", "last-year", "last-five-years")
ARXIV_DOI_PREFIX = "10.48550/arxiv."
SKIP_TYPES = {"paratext"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Append OpenAlex works to the BibTeX file.")
    parser.add_argument("--period", required=True, choices=PERIODS)
    parser.add_argument("--dry-run", action="store_true", help="Print what would be added, without writing")
    args = parser.parse_args()

    start, end = period_bounds(args.period, date.today())
    people = load_people()
    known = known_publications()
    already_on_file = {"doi": set(known["doi"]), "arxiv": set(known["arxiv"])}
    blocks: list[str] = []
    added = 0
    skipped = 0

    if start is None:
        print("Period: always")
    else:
        print(f"Period: {start} to {end}")

    for person in people:
        author = fetch_author(person["orcid"])
        if author is None:
            raise SystemExit(f"OpenAlex has no author for {person['name']} ({person['orcid']})")
        author_id = str(author.get("id") or "").rstrip("/").rsplit("/", 1)[-1]
        works = fetch_works(author_id, start, end)
        print(f"\n{person['name']}  {author_id}  {len(works)} works")
        for work in works:
            decision, block = consider_work(work, known, already_on_file)
            title = work.get("display_name") or "(no title)"
            year = work.get("publication_year") or "?"
            if decision == "add":
                blocks.append(block)
                added += 1
                print(f"  add   {year}  {title}")
            else:
                skipped += 1
                print(f"  skip  {year}  {title}  ({decision})")

    print(f"\n{added} to add, {skipped} already in the BibTeX file or not usable.")
    if args.dry_run:
        print("Dry run: content/publications.bib was not changed.")
        return
    if not blocks:
        return
    append_blocks(blocks, args.period)
    print(f"Appended {added} entries to {BIB_PATH.relative_to(ROOT)}")


def period_bounds(period: str, today: date) -> tuple[str | None, str | None]:
    """Inclusive publication dates. always has no bounds."""
    year = today.year
    if period == "always":
        return None, None
    if period == "this-year":
        return f"{year}-01-01", f"{year}-12-31"
    if period == "last-year":
        return f"{year - 1}-01-01", f"{year - 1}-12-31"
    return f"{year - 4}-01-01", f"{year}-12-31"


def load_people() -> list[dict]:
    data = yaml.safe_load(RESEARCHERS_PATH.read_text(encoding="utf-8"))
    people = data.get("people") if isinstance(data, dict) else None
    if not isinstance(people, list):
        raise SystemExit("content/researchers.yaml needs a people list")
    selected = []
    for index, person in enumerate(people, start=1):
        if not isinstance(person, dict):
            raise SystemExit(f"person {index} in content/researchers.yaml should be a mapping")
        orcid = str(person.get("orcid") or "").strip()
        name = str(person.get("name") or "").strip() or f"person {index}"
        if not orcid:
            print(f"{name} has no orcid, so this run skips them.", file=sys.stderr)
            continue
        selected.append({"name": name, "orcid": canonical_orcid(orcid, name)})
    if not selected:
        raise SystemExit("No orcid values in content/researchers.yaml")
    return selected


def canonical_orcid(value: str, name: str) -> str:
    number = value.rstrip("/").rsplit("/", 1)[-1]
    digits = number.replace("-", "")
    if len(digits) != 16 or not all(character.isdigit() or character.upper() == "X" for character in digits):
        raise SystemExit(f"{name} has an orcid that is not 16 characters: {value}")
    grouped = "-".join(number.replace("-", "")[offset : offset + 4] for offset in range(0, 16, 4))
    return f"https://orcid.org/{grouped}"


def known_publications() -> dict[str, set[str]]:
    """DOIs and arXiv ids already in the BibTeX file, plus citation keys."""
    known = {"doi": set(), "arxiv": set(), "key": set()}
    if not BIB_PATH.is_file():
        return known
    text = BIB_PATH.read_text(encoding="utf-8").replace("\ufeff", "")
    for entry in parse_bib(text):
        known["key"].add(entry["key"])
        doi, arxiv = identity_of_entry(entry)
        remember(known, doi, arxiv)
    return known


def identity_of_entry(entry: dict) -> tuple[str, str]:
    doi = normalize_doi(latex_to_text(entry.get("doi", "")))
    arxiv = ""
    try:
        item = publication_from_entry(entry)
    except ValueError:
        item = {}
    doi = normalize_doi(str(item.get("doi") or doi))
    arxiv = str(item.get("arxiv") or "")
    if not arxiv:
        arxiv = arxiv_from_doi(doi)
    return doi, arxiv


def remember(known: dict[str, set[str]], doi: str, arxiv: str) -> None:
    if doi:
        known["doi"].add(doi)
        embedded = arxiv_from_doi(doi)
        if embedded:
            known["arxiv"].add(embedded)
    if arxiv:
        known["arxiv"].add(arxiv)
        known["doi"].add(ARXIV_DOI_PREFIX + arxiv)


def already_known(known: dict[str, set[str]], doi: str, arxiv: str) -> bool:
    if doi and doi in known["doi"]:
        return True
    if arxiv and (arxiv in known["arxiv"] or ARXIV_DOI_PREFIX + arxiv in known["doi"]):
        return True
    return False


def consider_work(work: dict, known: dict[str, set[str]], on_file: dict[str, set[str]] | None = None) -> tuple[str, str]:
    """Return ('add', bibtex) or a skip reason and an empty block."""
    if str(work.get("type") or "") in SKIP_TYPES:
        return "paratext", ""
    title = str(work.get("display_name") or "").strip()
    year = work.get("publication_year")
    if not title or not isinstance(year, int):
        return "missing title or year", ""
    doi, arxiv, arxiv_url = work_identity(work)
    as_arxiv = preprint_on_arxiv(work, doi, arxiv)
    if already_known(known, doi, arxiv):
        if on_file is not None and not already_known(on_file, doi, arxiv):
            return "already added in this run", ""
        return "already in the BibTeX file", ""
    authors = author_list(work)
    if not authors:
        return "no authors", ""
    venue = "" if as_arxiv else venue_of(work)
    if not as_arxiv and not venue:
        return "no journal or arXiv id", ""
    key = citation_key(work, known["key"])
    block = render_entry(key, title, authors, int(year), venue, doi, arxiv if as_arxiv else "", arxiv_url, work)
    remember(known, doi, arxiv)
    known["key"].add(key)
    return "add", block


def work_identity(work: dict) -> tuple[str, str, str]:
    doi = normalize_doi(str(work.get("doi") or ""))
    arxiv = arxiv_from_doi(doi)
    arxiv_url = ""
    for place in [work.get("primary_location"), *(work.get("locations") or [])]:
        if not isinstance(place, dict):
            continue
        landing = str(place.get("landing_page_url") or "")
        found = arxiv_id_from_url(landing)
        if found:
            arxiv = arxiv or found
            arxiv_url = landing if "arxiv.org" in landing.lower() else arxiv_url
    if arxiv and not arxiv_url:
        arxiv_url = f"https://arxiv.org/abs/{arxiv}"
    return doi, arxiv, arxiv_url


def arxiv_from_doi(doi: str) -> str:
    if doi.startswith(ARXIV_DOI_PREFIX):
        return doi[len(ARXIV_DOI_PREFIX) :]
    return ""


def arxiv_id_from_url(url: str) -> str:
    match = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]+)(?:v\d+)?", url, re.IGNORECASE)
    return match.group(1) if match else ""


def preprint_on_arxiv(work: dict, doi: str, arxiv: str) -> bool:
    """True when this record is the arXiv preprint, not a later journal version."""
    location = work.get("primary_location") or {}
    source = str((location.get("source") or {}).get("display_name") or "")
    landing = str(location.get("landing_page_url") or "")
    if "arxiv" in source.lower() or "arxiv.org" in landing.lower():
        return True
    return bool(arxiv) and (not doi or doi == ARXIV_DOI_PREFIX + arxiv)


def author_list(work: dict) -> list[str]:
    names = []
    for authorship in work.get("authorships") or []:
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author") or {}
        name = str(author.get("display_name") or authorship.get("raw_author_name") or "").strip()
        if name:
            names.append(name.replace(" and ", " & "))
    return names


def venue_of(work: dict) -> str:
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    return str(source.get("display_name") or "").strip()


def citation_key(work: dict, used: set[str]) -> str:
    openalex_id = str(work.get("id") or "").rstrip("/").rsplit("/", 1)[-1]
    base = openalex_id if openalex_id[:1].isalpha() else f"W{openalex_id}"
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def render_entry(
    key: str,
    title: str,
    authors: list[str],
    year: int,
    venue: str,
    doi: str,
    arxiv: str,
    arxiv_url: str,
    work: dict,
) -> str:
    fields = [
        ("title", title),
        ("author", " and ".join(authors)),
        ("year", str(year)),
    ]
    if arxiv:
        fields.append(("eprint", arxiv))
        fields.append(("archivePrefix", "arXiv"))
        fields.append(("url", arxiv_url))
    else:
        fields.append(("journal", venue))
    if doi and not arxiv_from_doi(doi):
        fields.append(("doi", doi))
        landing = str((work.get("primary_location") or {}).get("landing_page_url") or "")
        fields.append(("url", landing or f"https://doi.org/{doi}"))
    elif doi and arxiv:
        fields.append(("doi", doi))
    lines = [f"@article{{{key},"]
    for name, value in fields:
        lines.append(f"  {name} = {{{clean_bib(value)}}},")
    lines.append("}")
    return "\n".join(lines)


def clean_bib(value: str) -> str:
    text = value.replace("{", "").replace("}", "").replace("\n", " ")
    return " ".join(text.split())


def append_blocks(blocks: list[str], period: str) -> None:
    existing = BIB_PATH.read_text(encoding="utf-8") if BIB_PATH.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    header = f"\n% Added from OpenAlex on {date.today().isoformat()} ({period}).\n"
    BIB_PATH.write_text(existing + header + "\n\n".join(blocks) + "\n", encoding="utf-8")


def fetch_author(orcid: str) -> dict | None:
    try:
        return get_json(f"https://api.openalex.org/authors/{orcid}")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise SystemExit(f"OpenAlex returned {error.code} while looking up {orcid}") from error


def fetch_works(author_id: str, start: str | None, end: str | None) -> list[dict]:
    filters = [f"authorships.author.id:{author_id}"]
    if start and end:
        filters.append(f"from_publication_date:{start}")
        filters.append(f"to_publication_date:{end}")
    works = []
    cursor = "*"
    seen: set[str] = set()
    while cursor and cursor not in seen and len(seen) < 50:
        seen.add(cursor)
        query = urllib.parse.urlencode(
            {
                "filter": ",".join(filters),
                "per-page": "100",
                "cursor": cursor,
                "select": "id,doi,display_name,publication_year,type,authorships,primary_location,locations",
            }
        )
        payload = get_json(f"https://api.openalex.org/works?{query}")
        works.extend(payload.get("results") or [])
        cursor = str((payload.get("meta") or {}).get("next_cursor") or "")
    return works


def get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            return json.load(response)
    except urllib.error.HTTPError:
        raise
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise SystemExit(f"OpenAlex request failed: {error}") from error


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)
