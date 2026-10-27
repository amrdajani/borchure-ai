"""Jev decision client.

Jev is TypeSafe's System One model. A call sends text state and typed
questions to TypeSafeClient.system_one. Choice, score, and noul are the
documented question types. Jev does not accept images and does not generate
text. https://docs.typesafe.ai/sdk/python
"""

from __future__ import annotations

from typing import Protocol

from designcraft_brochure.models import ChoicePick


class BrainError(RuntimeError):
    pass


class Judgment:
    def __init__(
        self,
        model: str,
        choices: dict[str, ChoicePick],
        nouls: dict[str, float],
        scores: dict[str, float] | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        self.model = model
        self.choices = choices
        self.nouls = nouls
        self.scores = scores or {}
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def choice(self, key: str) -> ChoicePick:
        try:
            return self.choices[key]
        except KeyError as exc:
            raise BrainError(f"Jev response did not include choice {key}.") from exc

    def noul(self, key: str) -> float:
        try:
            return self.nouls[key]
        except KeyError as exc:
            raise BrainError(f"Jev response did not include noul {key}.") from exc


class Brain(Protocol):
    def judge(self, state: dict, questions: dict) -> Judgment: ...


def scripted_judgment(
    questions: dict,
    *,
    choices: dict[str, str],
    nouls: dict[str, float],
    confidence: float = 0.95,
    model: str = "scripted",
) -> Judgment:
    """Build a judgment whose choices are members of the offered criteria."""
    built: dict[str, ChoicePick] = {}
    for key, selected in choices.items():
        question = questions[key]
        criteria = question.get("criteria") or {}
        if selected not in criteria:
            raise BrainError(f"Scripted choice {selected!r} is not in {key} criteria.")
        others = [name for name in criteria if name != selected]
        remainder = 0.0 if not others else (1.0 - 0.99) / len(others)
        probabilities = {name: (0.99 if name == selected else remainder) for name in criteria}
        built[key] = ChoicePick(
            choice=selected,
            confidence=confidence,
            probabilities=probabilities,
        )
    missing = [key for key in nouls if key not in questions]
    if missing:
        raise BrainError(f"Scripted noul was not asked: {missing[0]}")
    return Judgment(model=model, choices=built, nouls=dict(nouls))


class TypeSafeBrain:
    """Live client. Call open() or use it as a context manager before judge()."""

    def __init__(self, model: str = "jev-latest", timeout: float = 90) -> None:
        self.model = model
        self.timeout = timeout
        self._client = None

    def open(self) -> TypeSafeBrain:
        try:
            from typesafe_sdk import TypeSafeClient
        except ImportError as exc:
            raise BrainError(
                "typesafe-sdk is not installed. Install requirements.txt with Python 3.10 or newer."
            ) from exc
        try:
            self._client = TypeSafeClient(model=self.model, timeout=self.timeout)
        except Exception as exc:
            raise BrainError(f"Could not create the TypeSafe client: {exc}") from exc
        return self

    def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            client.close()

    def __enter__(self) -> TypeSafeBrain:
        return self.open()

    def __exit__(self, *_) -> None:
        self.close()

    def judge(self, state: dict, questions: dict) -> Judgment:
        if self._client is None:
            raise BrainError("TypeSafe client is not open.")
        if not questions:
            raise BrainError("Refusing to call Jev without questions.")
        try:
            response = self._client.system_one(state=state, questions=questions)
        except Exception as exc:
            raise BrainError(f"Jev request failed: {exc}") from exc
        return judgment_from_response(response)


def judgment_from_response(response: object) -> Judgment:
    choices: dict[str, ChoicePick] = {}
    nouls: dict[str, float] = {}
    scores: dict[str, float] = {}
    for key, answer in getattr(response, "choices", {}).items():
        probabilities = {
            str(name): float(value) for name, value in dict(answer.probabilities).items()
        }
        choices[str(key)] = ChoicePick(
            choice=str(answer.choice),
            confidence=float(answer.confidence),
            probabilities=probabilities,
        )
    for key, answer in getattr(response, "nouls", {}).items():
        nouls[str(key)] = float(answer.noul)
    for key, answer in getattr(response, "scores", {}).items():
        scores[str(key)] = float(answer.score)
    usage = getattr(response, "usage", None)
    return Judgment(
        model=str(getattr(response, "model", "")),
        choices=choices,
        nouls=nouls,
        scores=scores,
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
    )
