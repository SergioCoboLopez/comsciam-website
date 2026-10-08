#!/usr/bin/env python3
"""Build the ComSCIAM website.

The pages people see are HTML. This script only assembles them:

    site.yaml + content/<language>/*.yaml + templates/ + assets/
        -> docs/

Preview the result with:

    python build.py
    python -m http.server 8000 -d docs

Read README.md for what each folder is for.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape
from markupsafe import Markup, escape

ROOT = Path(__file__).resolve().parent
CONTENT = ROOT / "content"
TEMPLATES = ROOT / "templates"
ASSETS = ROOT / "assets"
OUTPUT = ROOT / "docs"

PALETTES = {"comsciam", "urv", "sea"}
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
ANCHOR_RE = re.compile(r"^[a-z][a-z0-9-]*$")

REQUIRED_BY_LAYOUT = {
    "home.html": [
        "title",
        "meta_description",
        "heading",
        "tagline",
        "intro",
        "areas_heading",
        "areas",
    ],
    "researchers.html": ["title", "meta_description", "heading", "intro", "people"],
    "publications.html": ["title", "meta_description", "heading", "intro", "items"],
    "simple.html": ["title", "meta_description", "heading", "intro"],
}

PERSON_FIELDS = ["id", "name", "role", "email", "photo", "bio"]
PUBLICATION_FIELDS = ["id", "title", "authors", "year", "venue"]
AREA_FIELDS = ["title", "text"]


def fail(message: str) -> None:
    raise SystemExit(f"Build stopped: {message}")


def load_yaml(path: Path) -> dict:
    if not path.is_file():
        fail(f"missing file {path.relative_to(ROOT)}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        fail(f"{path.relative_to(ROOT)} should be a YAML mapping (key: value)")
    return data


def require_keys(data: dict, keys: list[str], label: str) -> None:
    missing = [key for key in keys if key not in data]
    if missing:
        fail(f"{label} is missing: {', '.join(missing)}")


def output_dir(default_lang: str, lang_id: str, page_path: str) -> str:
    """Directory of a page, relative to the site root. '' is the root."""
    parts: list[str] = []
    if lang_id != default_lang:
        parts.append(lang_id)
    if page_path:
        parts.extend(piece for piece in page_path.split("/") if piece)
    return "/".join(parts)


def relative_link(from_dir: str, to_dir: str) -> str:
    """Link from one page directory to another. Both '' mean the site root."""
    source = from_dir or "."
    target = to_dir or "."
    relative = os.path.relpath(target, start=source).replace(os.sep, "/")
    if relative == ".":
        return "./"
    return relative + "/"


def relative_file(from_dir: str, file_path: str) -> str:
    if file_path.startswith("/") or ".." in Path(file_path).parts:
        fail(f"asset path must stay inside the site: {file_path}")
    source = from_dir or "."
    return os.path.relpath(file_path, start=source).replace(os.sep, "/")


def paragraphs(value: object) -> Markup:
    """Turn a YAML text block into HTML paragraphs. Blank lines start a new one."""
    text = str(value or "").strip()
    if not text:
        return Markup("")
    chunks = re.split(r"\n\s*\n", text)
    html: list[str] = []
    for chunk in chunks:
        flat = " ".join(line.strip() for line in chunk.splitlines() if line.strip())
        if flat:
            html.append(f"<p>{escape(flat)}</p>")
    return Markup("".join(html))


def check_same_ids(records: list[tuple[str, list[str]]], label: str) -> None:
    reference_lang, reference = records[0]
    for lang_id, found in records[1:]:
        if found != reference:
            fail(
                f"{label} ids in '{lang_id}' are {found}, but '{reference_lang}' has {reference}. "
                "Use the same ids, in the same order, in every language."
            )


def asset_exists(file_path: str) -> None:
    if not (ROOT / file_path).is_file():
        fail(f"missing asset {file_path}")


def join_paths(parent: str, child: str) -> str:
    return "/".join(piece for piece in (parent, child) if piece)


def child_entries(page: dict) -> list:
    children = page.get("children") or []
    if not isinstance(children, list):
        fail(f"{page['id']} children should be a list")
    return children


def prepare_routes(pages: list) -> tuple[list, list[str]]:
    """Pages that become HTML files, and every id that needs a menu label.

    A child with `anchor` is a subsection of its parent page.
    A child with `path` and `layout` is its own page, listed under the parent.
    """
    routes = []
    menu_ids: list[str] = []
    paths: list[str] = []
    for page in pages:
        menu_ids.append(page["id"])
        routes.append(page)
        paths.append(page["path"])
        for child in child_entries(page):
            require_keys(child, ["id"], f"child of {page['id']}")
            menu_ids.append(child["id"])
            has_anchor = "anchor" in child
            has_page = "path" in child or "layout" in child
            if has_anchor and has_page:
                fail(f"{child['id']} should use anchor, or path and layout, not both")
            if has_anchor:
                if not ANCHOR_RE.match(str(child["anchor"])):
                    fail(f"{child['id']} has an invalid anchor: {child['anchor']}")
                template = (TEMPLATES / page["layout"]).read_text(encoding="utf-8")
                if f'id="{child["anchor"]}"' not in template:
                    fail(
                        f"templates/{page['layout']} has no id=\"{child['anchor']}\". "
                        "That id is the landing spot for the subsection."
                    )
                continue
            require_keys(child, ["path", "layout"], child["id"])
            routes.append({**child, "path": join_paths(page["path"], child["path"])})
            paths.append(routes[-1]["path"])
    if len(menu_ids) != len(set(menu_ids)):
        fail("two pages or children in site.yaml use the same id")
    if len(paths) != len(set(paths)):
        fail("two pages in site.yaml use the same path")
    return routes, menu_ids


def build_nav(site: dict, ui: dict, here: str, default_lang: str, lang_id: str, current_id: str) -> list:
    nav = []
    for item in site["pages"]:
        dest = output_dir(default_lang, lang_id, item["path"])
        children = []
        child_page_ids = []
        for child in child_entries(item):
            if "anchor" in child:
                href = relative_link(here, dest) + "#" + child["anchor"]
                current = False
            else:
                child_dest = output_dir(
                    default_lang, lang_id, join_paths(item["path"], child["path"])
                )
                href = relative_link(here, child_dest)
                current = child["id"] == current_id
                child_page_ids.append(child["id"])
            children.append(
                {
                    "id": child["id"],
                    "label": ui["menu"][child["id"]],
                    "href": href,
                    "current": current,
                }
            )
        nav.append(
            {
                "id": item["id"],
                "label": ui["menu"][item["id"]],
                "href": relative_link(here, dest),
                "current": item["id"] == current_id,
                "open": item["id"] == current_id or current_id in child_page_ids,
                "children": children,
            }
        )
    return nav


def main() -> None:
    site = load_yaml(ROOT / "site.yaml")
    require_keys(
        site,
        ["name", "palette", "url", "email", "phone", "logos", "languages", "pages"],
        "site.yaml",
    )
    if site["palette"] not in PALETTES:
        fail(
            f"palette '{site['palette']}' is unknown. "
            f"Use one of: {', '.join(sorted(PALETTES))}"
        )
    if not site["languages"]:
        fail("site.yaml needs at least one language")

    default_lang = site["languages"][0]["id"]
    routes, menu_ids = prepare_routes(site["pages"])
    page_ids = [page["id"] for page in site["pages"]]

    for logo in site["logos"].values():
        asset_exists(logo)

    loaded: dict[str, dict] = {}
    for language in site["languages"]:
        lang_id = language["id"]
        folder = CONTENT / lang_id
        ui = load_yaml(folder / "ui.yaml")
        require_keys(ui, ["menu", "full_name", "address", "footer"], f"{lang_id}/ui.yaml")
        missing_labels = [page_id for page_id in menu_ids if page_id not in ui["menu"]]
        if missing_labels:
            fail(f"{lang_id}/ui.yaml menu is missing: {', '.join(missing_labels)}")

        pages: dict[str, dict] = {}
        for page in routes:
            label = f"{lang_id}/{page['id']}.yaml"
            content = load_yaml(folder / f"{page['id']}.yaml")
            layout = page["layout"]
            if layout not in REQUIRED_BY_LAYOUT:
                fail(f"no required-field list for layout {layout}")
            require_keys(content, REQUIRED_BY_LAYOUT[layout], label)
            if layout == "home.html":
                for index, area in enumerate(content["areas"], start=1):
                    require_keys(area, AREA_FIELDS, f"{label} area {index}")
            if layout == "researchers.html":
                for person in content["people"]:
                    require_keys(person, PERSON_FIELDS, f"{label} person")
                    if not ID_RE.match(str(person["id"])):
                        fail(f"{label} has an invalid person id: {person['id']}")
                    asset_exists(person["photo"])
            if layout == "publications.html":
                for item in content["items"]:
                    require_keys(item, PUBLICATION_FIELDS, f"{label} publication")
                    if not ID_RE.match(str(item["id"])):
                        fail(f"{label} has an invalid publication id: {item['id']}")
            pages[page["id"]] = content
        loaded[lang_id] = {"language": language, "ui": ui, "pages": pages}

    page_id_set = set(page_ids)
    if "researchers" in page_id_set:
        check_same_ids(
            [
                (lang_id, [person["id"] for person in bundle["pages"]["researchers"]["people"]])
                for lang_id, bundle in loaded.items()
            ],
            "researchers",
        )
    if "publications" in page_id_set:
        check_same_ids(
            [
                (lang_id, [item["id"] for item in bundle["pages"]["publications"]["items"]])
                for lang_id, bundle in loaded.items()
            ],
            "publications",
        )
    if "home" in page_id_set:
        area_counts = [
            (lang_id, len(bundle["pages"]["home"]["areas"]))
            for lang_id, bundle in loaded.items()
        ]
        if len({count for _, count in area_counts}) != 1:
            fail(f"home research lines differ by language: {area_counts}")

    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    shutil.copytree(ASSETS, OUTPUT / "assets")

    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["paragraphs"] = paragraphs

    written: list[str] = []
    year = datetime.now().year
    for lang_id, bundle in loaded.items():
        for page in routes:
            here = output_dir(default_lang, lang_id, page["path"])
            content = bundle["pages"][page["id"]]
            nav = build_nav(site, bundle["ui"], here, default_lang, lang_id, page["id"])
            alternates = []
            for language in site["languages"]:
                dest = output_dir(default_lang, language["id"], page["path"])
                alternates.append(
                    {
                        "id": language["id"],
                        "label": language["label"],
                        "locale": language["locale"],
                        "href": relative_link(here, dest),
                        "url": site["url"].rstrip("/") + ("/" if not dest else f"/{dest}/"),
                    }
                )
            try:
                template = env.get_template(page["layout"])
            except TemplateNotFound:
                fail(f"missing template templates/{page['layout']}")
            html = template.render(
                site=site,
                ui=bundle["ui"],
                content=content,
                page=page,
                lang=bundle["language"],
                nav=nav,
                alternates=alternates,
                default_url=site["url"].rstrip("/") + "/",
                year=year,
                asset=lambda file_path, here=here: relative_file(here, file_path),
            )
            target = OUTPUT.joinpath(*([piece for piece in here.split("/") if piece] + ["index.html"]))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(html, encoding="utf-8")
            public = "/" if not here else f"/{here}/"
            written.append(f"  {lang_id:2}  {public:24}  {target.relative_to(ROOT)}")

    print(f"Palette: {site['palette']}")
    print(f"Default language: {default_lang}")
    print("Pages:")
    print("\n".join(written))


if __name__ == "__main__":
    main()
