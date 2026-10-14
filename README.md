# Jev brochure agent

This is a Windows command-line agent that reads a business proposal, asks Jev which of your InDesign templates fits, and then operates Adobe InDesign through the normal window. It saves a working copy and exports a PDF.

Document editing is pointer, keyboard, and accessibility input. The project does not call InDesign scripting (no JSX, ExtendScript, UXP, or the scripting DOM).

## How a step is chosen

Jev is TypeSafe's System One model. The official call is `TypeSafeClient.system_one` on `POST https://api.typesafe.ai/v1/systemone`. You send text state and typed questions. Jev returns a choice, a score, or a yes/no probability (`noul`). It does not accept images, and it does not write brochure copy. The Python package is `typesafe-sdk` (verified against 0.7.4). Model id: `jev-latest`, or a pinned id such as `jev-1.13.0`.

Each cycle is:

1. Capture the screen and, on Windows, read accessible controls with UI Automation.
2. If a vision model is configured, it describes the screenshot as text. Jev never sees the pixels.
3. The program builds a closed list of actions that the current screen actually supports. Coordinates and typed strings come from that list, not from the model.
4. Jev chooses one action id, says whether the screen is understandable, and says whether the brochure already looks finished.
5. A second Jev call judges whether that action is safe to run.
6. The action is checked against an allowlist, then sent with PyAutoGUI or UI Automation.
7. A new screenshot is taken. Jev judges whether the intended effect happened. Exact checks (mouse position, window title, PDF file exists) are done in code.

If the screen is ambiguous, the choice is uncertain, or the same action keeps failing, the run stops and asks for a person. There is no fixed click script.

Copy is handled separately. Exact sentences from the proposal can be used as-is. An optional OpenAI-compatible chat model may paraphrase them. A paraphrase is kept only when every number and email already appears in the proposal and Jev's support judgment clears the threshold. Missing prices, contacts, and dates stay missing.

## Layout

```text
input/                 proposals (.pdf, .docx, .txt)
templates/corporate/   your .indd and .idml files
templates/real-estate/
templates/travel/
templates/products/
output/                working copies and exported PDFs
logs/                  action journal, checkpoints, screenshots
previews/              page renders and extracted images
src/brochure_agent/    agent, Jev client, Windows control
tests/
```

Put your real templates in the category folders. Originals are copied into `output/working/` before anything is opened. A hash of the template is checked again at the end.

`.idml` packages are unzipped only to read page count and placeholder text for template selection. `.indd` files are opaque; their layout is judged from the live window.

## Setup on Windows

Use Python 3.11 (3.10 or newer is required by `typesafe-sdk`).

```powershell
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Set `TYPESAFE_API_KEY` in `.env`. The SDK reads that variable. Leave `COPY_MODEL` and `VISION_MODEL` empty unless you have an OpenAI-compatible endpoint that can write text or describe images. Canvas editing inside a page is weak without a vision model, because InDesign's page items are often invisible to UI Automation. Menu and dialog actions can still be chosen from window titles and accessible controls.

Optional OCR for scanned PDFs needs the Tesseract program on `PATH`, plus `--ocr`.

## Commands

```powershell
python -m brochure_agent doctor
python -m brochure_agent list-templates --templates templates
python -m brochure_agent inspect input\proposal.pdf
python -m brochure_agent poc
python -m brochure_agent run --proposal input\proposal.pdf --templates templates --output output
python -m brochure_agent resume --checkpoint logs\checkpoint.json
```

`inspect` does not call Jev. It prints emails, phones, prices, and dates it can see, and it does not invent the rest.

`run` prints the stage it is in: reading the proposal, inspecting templates, selecting a template, opening InDesign, editing, inspecting pages, exporting, then completed or requires assistance.

Stop the run with Ctrl+C, or create `logs/PAUSE`. On Windows, throwing the pointer to a corner also trips PyAutoGUI's failsafe and stops input. `--max-actions` defaults to 60. A failed action is retried twice, then the run asks for help. `--yes` is required before an existing working copy or PDF is replaced. The agent does not delete files.

`--backend simulated` runs the decision loop against a fake desktop. It does not open InDesign. On a Mac or Linux machine, `--backend auto` refuses to pretend it is driving Windows.

## Safety

- Template files are never overwritten. The working file lives under `output/`.
- Typed text must be an approved proposal span, a paraphrased span Jev accepted, or a path under the output or image folders.
- Keys are a fixed set (open, place, save, export, undo, page navigation, dialog confirm/cancel). There is no shell, PowerShell, or generated code.
- Lines in a proposal that try to instruct the agent are dropped before extraction.
- The action journal records action ids and outcomes, not the proposal text.
- A PDF counts as finished only after it opens and PyMuPDF checks page count, page size, blank pages, clipped text, missing approved phrases, and missing images. The export dialog closing is not success.
- Choice confidence and safety probabilities use conservative starting thresholds in `config.py`. Tune them on your own runs. TypeSafe's confidence guide is the reference, not a guarantee for InDesign.

## What was tested here

The decision loop, proposal reader, template copy, factual filter, PDF checks, and a simulated open-edit-export path were run with Python 3.12 (`pytest`, 19 tests). `typesafe-sdk` 0.7.4 imports and accepts the question shapes this project sends.

This workspace is macOS, so Adobe InDesign was not opened and PyAutoGUI was not aimed at a real window. A live `TYPESAFE_API_KEY` was not called. Before you trust a brochure, run `poc` on the Windows machine, then `run` with one of your templates and watch the first few actions.
