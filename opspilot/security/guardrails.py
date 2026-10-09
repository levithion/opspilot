"""Prompt-injection detection, input/output checks and the action allow-list."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from opspilot.security.pii import redact

# (pattern, weight, label). Score = 1 - prod(1 - weight) over distinct matches.
_INJECTION_RULES: list[tuple[re.Pattern, float, str]] = [
    (
        re.compile(
            r"ignore\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|rules|prompts?)", re.I
        ),
        0.9,
        "override-instructions",
    ),
    (
        re.compile(
            r"disregard\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:previous|prior|above|system)?\s*(?:instructions|rules|guidelines|policy)",
            re.I,
        ),
        0.85,
        "override-instructions",
    ),
    (re.compile(r"forget\s+(?:everything|all)\s+(?:you|above|previous)", re.I), 0.7, "override-instructions"),
    (
        re.compile(
            r"(?:reveal|print|show|repeat|leak|output)\b.{0,40}\b(?:system\s+prompt|hidden\s+instructions|initial\s+instructions)",
            re.I,
        ),
        0.75,
        "prompt-exfiltration",
    ),
    (re.compile(r"you\s+are\s+now\s+(?:in\s+)?(?:a|an|dan|developer|god|root|admin)", re.I), 0.6, "role-hijack"),
    (re.compile(r"\b(?:developer|jailbreak|god)\s+mode\b", re.I), 0.6, "role-hijack"),
    (re.compile(r"act\s+as\s+(?:an?\s+)?(?:unrestricted|root|admin(?:istrator)?|dan)\b", re.I), 0.6, "role-hijack"),
    (re.compile(r"<\|?\s*(?:system|im_start|im_end)\s*\|?>|\[\s*system\s*\]|###\s*system", re.I), 0.7, "fake-system-tag"),
    (re.compile(r"(?:do\s+not|don't|never)\s+(?:tell|inform|mention\s+to)\s+the\s+user", re.I), 0.65, "concealment"),
    (
        re.compile(r"(?:without|skip|bypass)\s+(?:the\s+)?(?:human\s+)?(?:approval|confirmation|review)", re.I),
        0.7,
        "approval-bypass",
    ),
    (
        re.compile(r"(?:auto[- ]?approve|approve\s+(?:this|the)\s+request\s+(?:automatically|yourself))", re.I),
        0.7,
        "approval-bypass",
    ),
    (re.compile(r"(?:send|post|forward|upload|exfiltrate)\b.{0,60}\b(?:to|at)\s+https?://", re.I), 0.5, "exfiltration"),
    (re.compile(r"!\[[^\]]*\]\(https?://[^)\s]*[?&][^)\s]*=[^)\s]*\)"), 0.6, "markdown-exfiltration"),
    (
        re.compile(
            r"(?:call|invoke|run|use)\s+(?:the\s+)?(?:reset_request|create_ticket|notify_channel|get_user_access)\b.{0,60}\bfor\s+(?:every|all)\s+users?",
            re.I,
        ),
        0.7,
        "tool-abuse",
    ),
]
_INVISIBLE = re.compile("[​‌‍⁠﻿‪-‮]")


@dataclass
class ScanResult:
    score: float = 0.0
    labels: list[str] = field(default_factory=list)

    def flagged(self, threshold: float) -> bool:
        return self.score >= threshold


def scan_for_injection(text: str) -> ScanResult:
    normalised = unicodedata.normalize("NFKC", text)
    labels: list[str] = []
    survive = 1.0
    seen: set[str] = set()
    for pattern, weight, label in _INJECTION_RULES:
        if pattern.search(normalised) and label not in seen:
            seen.add(label)
            labels.append(label)
            survive *= 1.0 - weight
    if _INVISIBLE.search(text):
        labels.append("invisible-characters")
        survive *= 1.0 - 0.3
    return ScanResult(score=round(1.0 - survive, 3), labels=labels)


@dataclass
class InputDecision:
    allowed: bool
    text: str  # redacted text that is safe to send onwards
    reason: str | None = None
    labels: list[str] = field(default_factory=list)
    redactions: dict[str, int] = field(default_factory=dict)
    injection_score: float = 0.0


def check_user_input(text: str, *, max_chars: int = 4000, threshold: float = 0.6) -> InputDecision:
    if not text or not text.strip():
        return InputDecision(False, "", reason="empty message")
    if len(text) > max_chars:
        return InputDecision(False, "", reason=f"message longer than {max_chars} characters")
    scan = scan_for_injection(text)
    red = redact(text)
    cleaned = _INVISIBLE.sub("", red.text).strip()
    if scan.flagged(threshold):
        return InputDecision(
            False,
            cleaned,
            reason="possible prompt injection",
            labels=scan.labels,
            redactions=dict(red.counts),
            injection_score=scan.score,
        )
    return InputDecision(True, cleaned, labels=scan.labels, redactions=dict(red.counts), injection_score=scan.score)


def sanitize_untrusted(text: str, *, source: str, threshold: float = 0.6) -> tuple[str, ScanResult]:
    """Handle retrieved documents / tool output. Flagged content is quarantined, never forwarded."""
    scan = scan_for_injection(text)
    if scan.flagged(threshold):
        return f"[content from {source} withheld: possible prompt injection ({', '.join(scan.labels)})]", scan
    return _INVISIBLE.sub("", text), scan


def check_output(text: str) -> tuple[str, dict[str, int]]:
    """Final answer filter: never emit secrets/PII the model may have echoed."""
    red = redact(text, keep_emails=True)
    return red.text, dict(red.counts)


# ---- Action allow-list --------------------------------------------------------------------

RESET_SYSTEMS = frozenset({"vpn", "password", "mfa", "sharepoint_access", "software_license"})
NOTIFY_CHANNELS = frozenset({"it-helpdesk", "it-security", "it-oncall"})
TICKET_CATEGORIES = frozenset(
    {"access", "vpn", "password", "hardware", "software", "network", "security", "licensing", "general"}
)
TICKET_PRIORITIES = ("low", "medium", "high", "urgent")
