"""Shared fixtures. The answering brain is a test double, not Jev."""

from __future__ import annotations

import zipfile
from pathlib import Path

from designcraft_brochure.jev.brain import Judgment, scripted_judgment
from designcraft_brochure.models import ChoicePick


def write_idml(path: Path, story: str = "Harbor headline placeholder") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    designmap = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns:idPkg="http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging" DOMVersion="8.0">
  <idPkg:Spread src="Spreads/Spread_s1.xml"/>
  <idPkg:Story src="Stories/Story_t1.xml"/>
</Document>
"""
    spread = """<?xml version="1.0" encoding="UTF-8"?>
<idPkg:Spread xmlns:idPkg="http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging" DOMVersion="8.0">
  <Spread Self="s1">
    <Page Self="p1"/>
    <Image Self="im1"/>
  </Spread>
</idPkg:Spread>
"""
    story_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<idPkg:Story xmlns:idPkg="http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging" DOMVersion="8.0">
  <Story Self="t1"><Content>{story}</Content></Story>
</idPkg:Story>
"""
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("designmap.xml", designmap)
        package.writestr("Spreads/Spread_s1.xml", spread)
        package.writestr("Stories/Story_t1.xml", story_xml)


class CallbackBrain:
    def __init__(self, fn):
        self.fn = fn
        self.calls = 0

    def judge(self, state, questions):
        self.calls += 1
        return self.fn(state, questions)


def answering_brain(state, questions) -> Judgment:
    choices: dict[str, str] = {}
    nouls: dict[str, float] = {}
    if "next_action" in questions:
        choices["next_action"] = _next_action(state, questions["next_action"]["criteria"])
        quality = (state.get("quality") or {}).get("status")
        nouls["screen_understood"] = 0.95
        nouls["task_complete"] = 0.95 if quality == "ok" and state.get("native_saved") else 0.05
    if "safe_to_run" in questions:
        nouls["safe_to_run"] = 0.96
    if "effect_happened" in questions:
        nouls["effect_happened"] = 0.96
    if "proposal_instructs_agent" in questions:
        nouls["proposal_instructs_agent"] = 0.02
        choices["company_line"] = "line_0"
        project = questions["project_line"]["criteria"]
        choices["project_line"] = "line_1" if "line_1" in project else "unknown"
        choices["category"] = "travel"
    if "template" in questions:
        ids = [key for key in questions["template"]["criteria"] if key != "none"]
        choices["template"] = ids[0]
    for key, question in questions.items():
        if not key.startswith("role_"):
            continue
        text = str(question["instructions"]).lower()
        if "guests get" in text:
            choices[key] = "benefit"
        elif "call" in text or "email" in text or "visit" in text:
            choices[key] = "cta"
        elif "boat carries" in text:
            choices[key] = "specification"
        elif "$" in text:
            choices[key] = "other"
        else:
            choices[key] = "service"
    for key in questions:
        if key.startswith("support_"):
            nouls[key] = 0.1
    return scripted_judgment(questions, choices=choices, nouls=nouls, confidence=0.95)


def _next_action(state: dict, criteria: dict) -> str:
    quality = (state.get("quality") or {}).get("status")
    native = bool(state.get("native_saved"))
    document = state.get("document") or {}
    if quality == "ok" and native and "stop_agent" in criteria:
        return "stop_agent"
    if not document.get("document_open") and "open_working" in criteria:
        return "open_working"
    for key in criteria:
        if key.startswith("fill_story_"):
            return key
    for key in criteria:
        if key.startswith("place_image_"):
            return key
    if not native and "save_native" in criteria:
        return "save_native"
    if quality != "ok" and "export_pdf" in criteria:
        return "export_pdf"
    if "stop_agent" in criteria:
        return "stop_agent"
    return "request_human"


def fixed_choice(action_id: str, *, task_complete: float = 0.05, safe: float = 0.96, effect: float = 0.96):
    def judge(state, questions):
        choices = {}
        nouls = {}
        if "next_action" in questions:
            if action_id not in questions["next_action"]["criteria"]:
                return Judgment(
                    "scripted",
                    {
                        "next_action": ChoicePick(
                            choice=action_id,
                            confidence=0.99,
                            probabilities={action_id: 1},
                        )
                    },
                    {"screen_understood": 0.95, "task_complete": task_complete},
                )
            choices["next_action"] = action_id
            nouls["screen_understood"] = 0.95
            nouls["task_complete"] = task_complete
        if "safe_to_run" in questions:
            nouls["safe_to_run"] = safe
        if "effect_happened" in questions:
            nouls["effect_happened"] = effect
        return scripted_judgment(questions, choices=choices, nouls=nouls)

    return judge
