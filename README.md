# ComSCIAM website

Public site of the ComSCIAM research group, in Catalan, English, and Spanish.

The pages are ordinary HTML and CSS. A short Python script assembles them from text files, so a new paragraph, person, or paper does not require a new layout.

## How a page is made

Four kinds of files, each with one job:

| Folder or file | Job |
| --- | --- |
| `content/ca/`, `content/en/`, `content/es/` | The words, in each language |
| `templates/` | The HTML structure, written once and reused |
| `assets/css/` | The look. Colors are in `palettes.css`, layout is in `site.css` |
| `site.yaml` | The section list, the palette, and shared facts such as the email |
| `build.py` | Reads the files above and writes finished pages into `docs/` |

`docs/` is generated. Do not edit it by hand. Change the source, then build again.

Catalan is the first language in `site.yaml`, so it is the default:

- Catalan: `/`
- English: `/en/`
- Spanish: `/es/`

## See it on your computer

From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python build.py
python -m http.server 8000 -d docs
```

Open <http://localhost:8000>. Stop the server with Ctrl+C.

## Change the words

Edit the language you want, then run `python build.py` again.

- Home: `content/ca/home.yaml` (and the `en` and `es` copies)
- Menu labels, footer, and the long name of the group: `content/ca/ui.yaml`
- A blank line inside a text block starts a new paragraph

Keep the three languages in step. The build stops if one language has a different number of research lines, or different person and publication ids.

## Add a person

Copy a person block in `content/ca/researchers.yaml` and give it a new `id`. Repeat the same `id`, in the same position, in English and Spanish. Translate `role` and `bio`. Names and emails can stay as they are.

Photos go in `assets/images/`. Point `photo` at that file, for example `assets/images/surname.jpg`.

## Add a publication

Add a block under `items` in all three `publications.yaml` files, with the same `id`. Put new papers at the top: the page follows the file order. Leave the period off `authors`, `title`, and `venue`; the template adds it.

Or append the same record to every language from the command line:

```bash
python scripts/update_publications.py \
  --id surname-2026-keyword \
  --title "Title of the paper" \
  --authors "Surname, A.; Surname, B." \
  --year 2026 \
  --venue "Journal name" \
  --doi "10.1234/example"
```

That script writes at the end of each list. Move the new block up if it should be first. Later, the same script can be fed by a query to a publication database; the page template does not need to change.

## Add a subsection

A subsection is a child of a menu entry. Hover **Inici** and the menu offers **Línies de recerca**, which jumps to that part of the home page. On a narrow screen the child is listed under Inici.

Two files define it:

1. `children` under the parent in `site.yaml`. `anchor` is the HTML id of the landing spot:

```yaml
- id: home
  path: ""
  layout: home.html
  children:
    - id: research-lines
      anchor: research-lines
```

2. The visible name, in each `content/<language>/ui.yaml`:

```yaml
menu:
  research-lines: Línies de recerca
```

The anchor must match an `id` in the parent template. For this one, `templates/home.html` has `<h2 id="research-lines">`.

A child can instead be its own page. Give it `path` and `layout`, and add `content/<language>/<id>.yaml`, the same way as a top-level section. It still appears under the parent, because it is listed in `children`.

## Add a section

1. Add a page to `site.yaml`:

```yaml
- id: contact
  path: contact
  layout: simple.html
```

2. Add the menu label in `content/ca/ui.yaml`, `content/en/ui.yaml`, and `content/es/ui.yaml`:

```yaml
menu:
  contact: Contacte
```

3. Create `content/ca/contact.yaml` (and `en`, `es`) with `title`, `meta_description`, `heading`, and `intro`. Optional subsections:

```yaml
sections:
  - title: On som
    text: |
      Text de l'apartat.
```

4. Run `python build.py`.

`path` is the piece of the URL. `research/lines` would publish at `/research/lines/`. Use `simple.html` for a text page. Use a new file in `templates/` only when the section needs a different structure, as researchers and publications do.

## Change the colors

In `site.yaml`, set `palette` to `comsciam`, `urv`, or `sea`, then build again.

`urv` is a red-and-black set in the spirit of the university pages. `comsciam` is a teal set for the group's own identity. To make another set, copy a block in `assets/css/palettes.css` and add its name to `PALETTES` in `build.py`.

## Replace the logos

- Group logo: replace `assets/logos/comsciam.svg` (SVG or PNG; if you change the name, update `logos.group` in `site.yaml`).
- University logo: replace `assets/logos/urv.svg` with the official URV file when the group is allowed to use it.

## GitHub

The source lives in a public repository. GitHub Actions runs `build.py` on every push to `main` and publishes the result with GitHub Pages.

The public address is <https://sergiocobolopez.github.io/comsciam-website/>.

Pushing `main` is what updates the public site. The generated `docs/` folder stays on the build machine and is listed in `.gitignore`.

If the repository name or the domain changes, update `url` in `site.yaml`. That value is only used for the language-alternate links. The menu uses relative links, so it keeps working.

Do not put private data in this repository. Everything here can be read by anyone.
