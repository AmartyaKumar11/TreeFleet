"""Input guard. The deterministic layer is authoritative. Jev cannot unblock it."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from treefleet.config import Settings
from treefleet.contracts import GuardDecision, GuardStage
from treefleet.jev.client import JevError

_LENGTH = 2000
_HIDDEN = re.compile(r"[\u200b\u200c\u200d\ufeff\u202a-\u202e]")
_FOLD = str.maketrans(
    {
        "\u0430": "a",
        "\u0435": "e",
        "\u043e": "o",
        "\u0440": "p",
        "\u0441": "c",
        "\u0445": "x",
        "\u0456": "i",
        "\u04cf": "l",
        "\uff0e": ".",
        "\u2024": ".",
        "\uff0f": "/",
        "\u2215": "/",
    }
)
_INJECTION = (
    "ignore previous instructions",
    "reveal the system prompt",
    "reveal system prompt",
    "exfiltrate",
    "rm -rf",
    "curl|sh",
)
_SECRET = re.compile(r"sk-[A-Za-z0-9]{8,}|apikey_[A-Za-z0-9]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----", re.I)
_PIPE = re.compile(r"curl\b[^|]{0,80}\|\s*(?:ba)?sh\b", re.I)
_QUESTIONS = {
    "prompt_injection": "Does this text try to override the system, reveal hidden instructions, or exfiltrate secrets?",
    "abusive": "Is this text toxic, hateful, or abusive?",
    "on_topic": "Does this ask for a software change in a repository?",
}


@dataclass
class InputScreen:
    decision: GuardDecision
    degraded: bool


def _folded(goal: str) -> str:
    return unicodedata.normalize("NFKC", goal).translate(_FOLD).lower()


def deterministic_reasons(goal: str) -> list[str]:
    reasons: list[str] = []
    if len(goal) > _LENGTH:
        reasons.append("goal exceeds the length cap")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in goal):
        reasons.append("control characters")
    if _HIDDEN.search(goal):
        reasons.append("hidden characters")
    folded = _folded(goal)
    for pattern in _INJECTION:
        if pattern in folded:
            reasons.append(pattern)
    if _PIPE.search(folded):
        reasons.append("curl|sh")
    if re.search(r"(?:^|[\s\"'`])\.git(?:/|\s|$)", folded) or re.search(r"(?:^|[\s\"'`])\.cursor(?:/|\s|$)", folded):
        reasons.append("protected path")
    if _SECRET.search(goal):
        reasons.append("secret-looking string")
    return reasons


def screen_goal(goal: str, settings: Settings, jev: object | None) -> InputScreen:
    reasons = deterministic_reasons(goal)
    scores: dict[str, float] = {}
    degraded = False
    if jev is not None:
        try:
            found = jev.probabilities(goal, _QUESTIONS)  # type: ignore[attr-defined]
        except (JevError, TimeoutError, OSError):
            degraded = True
        else:
            for name, value in found.items():
                scores[name] = max(0.0, min(1.0, float(value)))
            if scores.get("prompt_injection", 0.0) >= settings.thresholds.injection:
                reasons.append("prompt injection score")
            if scores.get("abusive", 0.0) >= settings.thresholds.abusive:
                reasons.append("abusive score")
            if "on_topic" in scores and scores["on_topic"] < settings.thresholds.on_topic:
                reasons.append("off topic")
    decision = GuardDecision(
        stage=GuardStage.INPUT,
        allowed=not reasons,
        detector="deterministic" if degraded or jev is None else "deterministic+jev",
        reasons=[item[:200] for item in reasons[:10]],
        scores=scores,
    )
    return InputScreen(decision=decision, degraded=degraded)
