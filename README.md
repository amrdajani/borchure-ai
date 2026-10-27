# Brochure API

HTTP API that builds a brochure without the DesignCraft desktop app. Edits go to `designcraft-cli mcp`, which runs the layout engine in-process. A phone or another computer only calls this API.

## Run

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TYPESAFE_API_KEY=...
export ANTHROPIC_API_KEY=...
export BROCHURE_API_TOKEN=...
designcraft-brochure serve --host 0.0.0.0 --port 7980 --backend headless --jobs /data/jobs --saved-templates /data/templates
```

`designcraft-cli` must be on `PATH`. The Linux `.deb` from the [v0.13.0 release](https://github.com/storytold/designcraft/releases/tag/v0.13.0) installs it at `/usr/bin/designcraft-cli`. Listening on `0.0.0.0` refuses to start until `BROCHURE_API_TOKEN` is set. `GET /health` is open. Every other route needs `Authorization: Bearer <token>`.

On Railway, deploy this Dockerfile and mount a volume at `/data`. Set the three variables above. On the Vercel site, set `BROCHURE_API_BASE` to the Railway URL and `BROCHURE_API_TOKEN` to the same token.

One brochure runs at a time.

# DesignCraft brochure agent

Jev reads a proposal, picks an IDML or DesignCraft template, and chooses each edit. This program sends only that choice to DesignCraft's control channel. It does not click the window, and it does not run InDesign scripts.

The InDesign agent in `borchure-ai` is unchanged.

## What a run does

1. Read a `.txt`, `.docx`, or `.pdf` proposal. Lines that try to instruct the agent are dropped.
2. List templates. Jev picks one `.idml` or `.designcraft` file.
3. Copy it into `output/working`. The original template is never written.
4. Ask Jev which DesignCraft command to run next. The choice is one id from a list built from the open document.
5. Send that command as one JSON line to `127.0.0.1`.
6. Check the document again. A PDF counts only after PyMuPDF opens it and finds the approved phrases.
7. Save a native `.designcraft` file and export `output/<name>.pdf`.

Jev is TypeSafe System One (`choice`, `noul`, and `score` only). It does not see images and it does not invent the text that gets sent.

## DesignCraft limits

DesignCraft is pre-alpha. This agent opens an IDML or native file and exports a PDF. An `.indd` template is copied and converted to IDML with [indd-utils](https://github.com/storytold/indd-utils) before it is opened. The original `.indd` file is not modified. `.indt`, `.indb`, and `.indl` are still unread. IDML import is implemented and still short of a proven round-trip with real InDesign files. If the deliverable has to stay an `.indd` inside Adobe InDesign, use the other agent.

## Setup

Python 3.10 or newer:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Put `TYPESAFE_API_KEY` in `.env`. That key is what Jev uses. `ANTHROPIC_API_KEY` is optional and only drafts brochure sentences; Jev still has to accept any sentence that is not copied from the proposal.

Start DesignCraft with the control port, then run:

```sh
designcraft --control 7979
designcraft-brochure run --proposal input/proposal.txt --templates templates --output output
```

`doctor` reports whether that port is answering. `list-templates` and `inspect` do not call Jev.

```sh
designcraft-brochure doctor
designcraft-brochure list-templates
designcraft-brochure inspect input/proposal.txt
```

`--backend simulated` runs the same decisions against an in-memory document and writes the PDF with PyMuPDF. That is for tests. It does not talk to DesignCraft, and the `.designcraft` file it saves is a JSON snapshot rather than a real DesignCraft package.

`--backend live` requires the app. `--backend auto` uses the app when `127.0.0.1:7979` answers.

## Upload API

Start DesignCraft with the control port, then start the local API:

```sh
designcraft --control 7979
designcraft-brochure serve
```

`serve` listens on `127.0.0.1:7980`. Send the proposal and the brochure file in one request. Jev chooses each edit, and the server sends that command to DesignCraft.

```sh
curl -F "proposal=@input/proposal.pdf" -F "template=@templates/brochure.indd" http://127.0.0.1:7980/brochures
```

### Saved templates

Templates can be kept in a library so they are uploaded once. They are stored in `templates/saved/` (change it with `--saved-templates`; the folder is git-ignored).

| Request | What it does |
| --- | --- |
| `GET /templates` | List the saved templates, newest first |
| `POST /templates` | Save one. Form file `template` (`.indd`, `.idml`, `.designcraft`) and optional text field `name`. Sending the same file again returns the saved one |
| `POST /templates/<id>` | Rename. Form field `name` |
| `DELETE /templates/<id>` | Remove it. Brochures already made are not affected |

Make a brochure from a saved template by sending `template_id=<id>` instead of the `template` file.

The response includes `/brochures/<id>/pdf`, `/brochures/<id>/native`, and `/brochures/<id>/idml`. Fetch those paths on the same host to download the PDF, the DesignCraft file, and the edited IDML. DesignCraft cannot write a binary `.indd`; the IDML is the edited file InDesign opens, and the uploaded `.indd` is left unchanged. When the run finishes, DesignCraft closes. `GET /health` reports whether DesignCraft is answering. One brochure runs at a time.

## Commands Jev may choose

The client refuses anything else, including clicks and keypresses. Jev cannot quit the app. The run closes DesignCraft itself after the edited IDML is saved.

| Command | When |
|---|---|
| `file.openIdml` / `file.open` | Open the working copy |
| `document.inspect` | Read pages, stories, and frames |
| `story.setText` | Replace one story with approved source blocks |
| `file.place` | Place a proposal image |
| `ui.render` | Write a page preview |
| `file.saveAs` | Save the native file under `output/working` |
| `file.exportPdf` | Write the PDF under `output` |
| `file.exportIdml` | Write an edited IDML copy |

The program, not Jev, also sends `text.select`, `type.char`, `type.para`, `type.selectionAttrs`, `transform.resize`, `object.textFrameOptions` and `object.fit` during the finishing pass below. Jev still cannot choose them.

## Chat edits

`POST /brochures/<id>/chat` takes a `message` (and optionally `history` and `image` files) and changes a finished brochure. The server opens the latest edited IDML in DesignCraft, reads what is on each page, and asks the copy model (`ANTHROPIC_API_KEY`, or the `COPY_*` settings) for a short list of operations. Nothing the model says is sent as it is. `agent/chat.py` checks each operation against the real document and only these can run:

| Operation | Does |
|---|---|
| `replace_text` | Change words inside a story and keep its formatting |
| `set_text` | Rewrite a whole story in its first character's style |
| `style_text` | Bold, size, color, capitals, alignment |
| `fill_shape` | Change the color of a bar or box |
| `replace_picture` | Put an attached picture into a picture frame |
| `tour_code` | Change the tour code bar and point the register QR code at it |
| `move` | Nudge a frame, at most 200 points |
| `reorder_days` | Swap or move itinerary days. The DAY labels stay 1, 2, 3 in order; each day's title and bullets move, then the page is spaced again (even gaps, frames sized to their bullets, the other column's colours when a day crosses over, text stepped down if it will not fit) |
| `undo` | Go back to the previous version |

Each change writes a new revision (PDF, native file, IDML, page pictures) and the reply lists the new `pages`, which `GET /brochures/<id>/page/<n>` serves as PNG. Undo keeps the last 20 versions. A request the operations cannot do, such as a new page or a new font family, gets a plain reply and no change.

## Finishing pass

`setText` flattens a story to its first character's style, and a converted IDML has broken picture links. So once, before the first save or export, `craft/polish.py` puts the reference look back:

- Host panel: bold title, bold 11 pt lines with 12 pt spacing, yellow payment lines.
- Airfare, tour-code and date bars: bold caps text, vertically centred and inset to line up with the yellow bar, shrunk until it fits.
- Cover line: `JOIN <HOST> IN`, bold caps.
- Headlines that overflow are widened inside their own page third.
- Pictures: `assets/manifest.json` lists slots by frame position. Each frame within 30 pt of a slot receives that file (`file.place`, then `object.fit`). Files must stay inside `assets/`. Add a slot to support a new template layout.

Each step is isolated. A failure is reported as a "Finishing:" note and the rest still runs.

Story text has to be an exact approved block from the proposal. Numbers and email addresses that are not in the source are dropped before Jev sees them.

## Safety

- Templates are copied, then hashed again. A run stops if the original file changed.
- Existing output is left in place unless you pass `--yes`.
- Nothing is deleted.
- The control channel is accepted only on `127.0.0.1`.
- The journal records command ids, not proposal sentences.
- Create `logs/PAUSE` to stop at the next step. Ctrl+C stops as well.

Exit codes: `0` completed, `1` failed, `2` needs a person, `3` stopped.

## Tests

```sh
pytest
```

The tests use the simulated backend and a scripted stand-in for Jev. They do not call TypeSafe and they do not need the DesignCraft app.
