#!/usr/bin/env python3
"""Turn content/publications.bib into the publication list used by the site.

Run it on its own to print that list:

    python3 scripts/update_publications.py

The website build reads the same function, so editing the BibTeX file and
running python3 build.py is enough to update the three language pages.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIB_PATH = ROOT / "content" / "publications.bib"
CITATIONS_PATH = ROOT / "content" / "citations.json"
# OpenAlex asks callers to identify themselves. Google Scholar has no API for this.
USER_AGENT = "ComSCIAM-website/1.0 (mailto:comsciam@example.com)"

# HTML ids on the page. Same rule as build.py.
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

# BibTeX accent commands, written as {\'o}, {\"a}, {\~n}, {\c{c}}.
ACCENTS = {
    "'": {"a": "á", "e": "é", "i": "í", "o": "ó", "u": "ú", "A": "Á", "E": "É", "I": "Í", "O": "Ó", "U": "Ú"},
    "`": {"a": "à", "e": "è", "i": "ì", "o": "ò", "u": "ù", "A": "À", "E": "È", "O": "Ò", "U": "Ù"},
    "^": {"a": "â", "e": "ê", "i": "î", "o": "ô", "u": "û"},
    '"': {"a": "ä", "e": "ë", "i": "ï", "o": "ö", "u": "ü", "A": "Ä", "O": "Ö", "U": "Ü"},
    "~": {"n": "ñ", "N": "Ñ", "a": "ã", "o": "õ"},
    "c": {"c": "ç", "C": "Ç"},
}
SUBSCRIPTS = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def load_publications(path: Path = BIB_PATH) -> list[dict]:
    """Read a .bib file and return one dictionary per paper, newest first."""
    if not path.is_file():
        raise ValueError(f"missing {path.relative_to(ROOT)}")

    text = path.read_text(encoding="utf-8").replace("\ufeff", "")
    items = []
    seen_ids = set()
    for entry in parse_bib(text):
        item = publication_from_entry(entry)
        if item["id"] in seen_ids:
            raise ValueError(f"two BibTeX entries become the same id: {item['id']}")
        seen_ids.add(item["id"])
        items.append(item)

    items.sort(key=lambda item: (-item["year"], item["id"]))
    attach_citations(items)
    return items


def attach_citations(items: list[dict]) -> None:
    """Add a citation count and an OpenAlex link.

    Published papers are looked up by their DOI. A preprint is looked up by the
    DOI arXiv assigns, 10.48550/arxiv.<id>. A saved copy in content/citations.json
    is used when OpenAlex cannot be reached, so the page still builds. Papers
    with no saved count get no banner.
    """
    cache = read_citation_cache()
    dois = [key for item in items if (key := citation_key(item))]
    try:
        fresh = fetch_openalex(dois)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as error:
        print(f"OpenAlex unavailable, using saved citation counts: {error}", file=sys.stderr)
        fresh = {}
    if fresh:
        cache.update(fresh)
        write_citation_cache(cache)
    for item in items:
        record = cache.get(citation_key(item))
        if not record:
            continue
        item["citations"] = record["count"]
        item["citations_url"] = record["url"]


def citation_key(item: dict) -> str:
    """DOI used to ask OpenAlex for this paper's citation count."""
    if item.get("doi"):
        return normalize_doi(item["doi"])
    arxiv = str(item.get("arxiv") or "").strip()
    if arxiv:
        return normalize_doi(f"10.48550/arxiv.{arxiv}")
    return ""


def normalize_doi(doi: str) -> str:
    return doi.strip().removeprefix("https://doi.org/").lower()


def read_citation_cache() -> dict:
    if not CITATIONS_PATH.is_file():
        return {}
    try:
        data = json.loads(CITATIONS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_citation_cache(cache: dict) -> None:
    text = json.dumps(cache, indent=2, sort_keys=True) + "\n"
    CITATIONS_PATH.write_text(text, encoding="utf-8")


def fetch_openalex(dois: list[str]) -> dict:
    """Ask OpenAlex for cited-by counts. The returned url lists the citing papers."""
    found = {}
    unique = list(dict.fromkeys(doi for doi in dois if doi))
    for start in range(0, len(unique), 25):
        chunk = unique[start : start + 25]
        doi_filter = "|".join(f"https://doi.org/{doi}" for doi in chunk)
        query = urllib.parse.urlencode(
            {
                "filter": f"doi:{doi_filter}",
                "per-page": "25",
                "select": "id,doi,cited_by_count",
            }
        )
        request = urllib.request.Request(
            f"https://api.openalex.org/works?{query}",
            headers={"User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
        for work in payload.get("results", []):
            record = citation_record(work)
            if record is not None:
                doi, details = record
                found[doi] = details
    return found


def citation_record(work: dict) -> tuple[str, dict] | None:
    doi = normalize_doi(str(work.get("doi") or ""))
    openalex_id = str(work.get("id") or "").rstrip("/").rsplit("/", 1)[-1]
    count = work.get("cited_by_count")
    if not doi or not openalex_id.startswith("W") or count is None:
        return None
    return doi, {
        "count": int(count),
        "url": f"https://openalex.org/works?filter=cites:{openalex_id}",
    }


def publication_from_entry(entry: dict) -> dict:
    """Keep the fields the publications page knows how to show."""
    key = entry["key"]
    raw_authors = required(entry, "author")
    title = latex_to_text(required(entry, "title"))
    authors = format_authors(raw_authors)
    year = parse_year(required(entry, "year"), key)
    venue = venue_name(entry)

    item = {
        "id": html_id(key),
        "title": title,
        "authors": authors,
        "author_sigs": author_signatures(raw_authors),
        "year": year,
        "venue": venue,
    }
    doi = latex_to_text(entry.get("doi", "")).removeprefix("https://doi.org/")
    url = latex_to_text(entry.get("url", ""))
    if doi:
        item["doi"] = doi
    if url:
        item["url"] = url
    elif doi:
        item["url"] = f"https://doi.org/{doi}"
    arxiv = arxiv_identifier(entry)
    if arxiv:
        item["arxiv"] = arxiv
    return item


def venue_name(entry: dict) -> str:
    """Journal for a published paper, or arXiv:id for a preprint."""
    published = latex_to_text(entry.get("journal") or entry.get("booktitle") or entry.get("publisher") or "")
    if published:
        return published
    identifier = arxiv_identifier(entry)
    if identifier or is_arxiv(entry):
        return f"arXiv:{identifier}" if identifier else "arXiv"
    other = latex_to_text(entry.get("howpublished") or entry.get("note") or "")
    if other:
        return other
    raise ValueError(f"{entry['key']} needs a journal, booktitle, publisher, or arXiv id")


def arxiv_identifier(entry: dict) -> str:
    """arXiv id when this entry is a preprint, otherwise an empty string."""
    if not is_arxiv(entry):
        return ""
    eprint = latex_to_text(entry.get("eprint") or "")
    url = latex_to_text(entry.get("url") or "")
    return eprint or arxiv_id(url)


def is_arxiv(entry: dict) -> bool:
    archive = latex_to_text(entry.get("archiveprefix") or "").lower()
    url = latex_to_text(entry.get("url") or "").lower()
    return archive == "arxiv" or "arxiv.org" in url


def arxiv_id(url: str) -> str:
    match = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]+)(?:v\d+)?", url, re.IGNORECASE)
    return match.group(1) if match else ""


def required(entry: dict, field: str) -> str:
    value = entry.get(field, "").strip()
    if not value:
        raise ValueError(f"{entry['key']} is missing {field}")
    return value


def parse_year(value: str, key: str) -> int:
    match = re.search(r"\d{4}", value)
    if not match:
        raise ValueError(f"{key} has no four-digit year")
    return int(match.group(0))


def html_id(citation_key: str) -> str:
    """Citation keys may contain dots and slashes. Page ids may not."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", citation_key).strip("-")
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    if cleaned and not cleaned[0].isalpha():
        cleaned = "p-" + cleaned
    if not ID_RE.match(cleaned):
        raise ValueError(f"{citation_key} cannot be used as a page id")
    return cleaned


def author_signatures(value: str) -> list[str]:
    """One comparable name per author, so a researcher can be matched to a paper."""
    signatures = []
    for part in re.split(r"\s+and\s+", value):
        tokens = name_tokens(part)
        if tokens:
            signatures.append(" ".join(sorted(tokens)))
    return signatures


def name_tokens(value: str) -> set[str]:
    """Lower case, without accents or hyphens, so Cobo-López and Cobo-Lopez match."""
    text = latex_to_text(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.lower().replace("-", " ").replace(",", " ").replace(".", " ")
    return {token for token in text.split() if token}


def names_match(researcher: str, author_signature: str) -> bool:
    """True when the bibliography name is the researcher, or the same name minus one surname."""
    person = name_tokens(researcher)
    author = set(author_signature.split())
    if len(person) < 2 or len(author) < 2:
        return False
    if person == author:
        return True
    return author <= person and len(person - author) <= 1


def format_authors(value: str) -> str:
    """Show the first two authors. Mention that others exist when there are more."""
    people = [format_person(part) for part in re.split(r"\s+and\s+", value) if part.strip()]
    if not people:
        raise ValueError("a publication has an empty author list")
    if len(people) <= 2:
        return "; ".join(people)
    return f"{people[0]}; {people[1]} et al"


def format_person(value: str) -> str:
    """BibTeX writes 'Family, Given' or 'Given Family'. The page uses 'Family, Given'."""
    name = latex_to_text(value)
    if "," in name:
        family, given = [part.strip() for part in name.split(",", 1)]
        return f"{family}, {given}" if given else family
    parts = name.split()
    if len(parts) < 2:
        return name
    return f"{parts[-1]}, {' '.join(parts[:-1])}"


def latex_to_text(value: str) -> str:
    """Drop the LaTeX markup that reference managers put in titles and names."""
    text = value.replace("\\%", "%").replace("\\&", "&")
    text = re.sub(
        r"\\(['`^\"~c])\s*\{?([A-Za-z])\}?",
        lambda match: ACCENTS.get(match.group(1), {}).get(match.group(2), match.group(2)),
        text,
    )
    text = re.sub(r"\\(?:mathrm|mathbf|mathit|text|emph|textbf|textit)\s*", "", text)
    text = re.sub(r"_\{([0-9]+)\}", lambda match: match.group(1).translate(SUBSCRIPTS), text)
    text = re.sub(r"_([0-9])", lambda match: match.group(1).translate(SUBSCRIPTS), text)
    text = text.replace("$", "").replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", text).strip()


def parse_bib(text: str) -> list[dict]:
    """Split a .bib file into entries. Each entry is the citation key plus its fields."""
    entries = []
    index = 0
    length = len(text)
    while index < length:
        if text[index] != "@":
            index += 1
            continue
        header = re.match(r"@(\w+)\s*\{", text[index:])
        if not header:
            index += 1
            continue
        kind = header.group(1).lower()
        index += header.end()
        if kind in {"comment", "string", "preamble"}:
            _, index = read_braced(text, index - 1)
            continue
        comma = text.find(",", index)
        if comma < 0:
            raise ValueError("a BibTeX entry is missing its citation key")
        key = text[index:comma].strip()
        index = comma + 1
        fields = {"key": key, "type": kind}
        while index < length:
            while index < length and text[index] in " \t\r\n,":
                index += 1
            if index >= length or text[index] == "}":
                index += 1
                break
            name_match = re.match(r"([A-Za-z][\w:-]*)\s*=\s*", text[index:])
            if not name_match:
                snippet = text[index : index + 40]
                raise ValueError(f"{key}: could not read a field near {snippet!r}")
            name = name_match.group(1).lower()
            index += name_match.end()
            if index >= length:
                raise ValueError(f"{key} is missing a value for {name}")
            if text[index] == "{":
                raw, index = read_braced(text, index)
            elif text[index] == '"':
                raw, index = read_quoted(text, index)
            else:
                bare = re.match(r"[^\s,]+", text[index:])
                raw = bare.group(0) if bare else ""
                index += len(raw)
            fields[name] = raw.strip()
        entries.append(fields)
    return entries


def read_braced(text: str, index: int) -> tuple[str, int]:
    """Read a {...} value, including braces nested inside it, and return the inside text."""
    if index >= len(text) or text[index] != "{":
        raise ValueError("expected an opening brace in the BibTeX file")
    depth = 0
    chars: list[str] = []
    index += 1
    while index < len(text):
        character = text[index]
        if character == "\\" and index + 1 < len(text):
            chars.append(character)
            chars.append(text[index + 1])
            index += 2
            continue
        if character == "{":
            depth += 1
            chars.append(character)
        elif character == "}":
            if depth == 0:
                return "".join(chars), index + 1
            depth -= 1
            chars.append(character)
        else:
            chars.append(character)
        index += 1
    raise ValueError("a BibTeX value is missing its closing brace")


def read_quoted(text: str, index: int) -> tuple[str, int]:
    """Read a "quoted" value."""
    chars: list[str] = []
    index += 1
    while index < len(text):
        character = text[index]
        if character == "\\" and index + 1 < len(text):
            chars.append(text[index + 1])
            index += 2
            continue
        if character == '"':
            return "".join(chars), index + 1
        chars.append(character)
        index += 1
    raise ValueError("a BibTeX value is missing its closing quote")


def main() -> None:
    items = load_publications()
    if not items:
        print(f"No publications in {BIB_PATH.relative_to(ROOT)}")
        return
    for item in items:
        print(f"{item['year']}  {item['authors']}")
        print(f"     {item['title']}")
        print(f"     {item['venue']}")
        if item.get("url"):
            print(f"     {item['url']}")
        if "citations" in item:
            print(f"     citations: {item['citations']}")
        print()


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)
    except ValueError as error:
        raise SystemExit(error) from error
