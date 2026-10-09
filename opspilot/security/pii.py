"""PII / secret redaction applied before text reaches any LLM, log line or audit record."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


@dataclass
class RedactionResult:
    text: str
    counts: Counter = field(default_factory=Counter)

    @property
    def redacted(self) -> bool:
        return bool(self.counts)


_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}", re.I),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"),
    # "password is X" only counts when X looks like a credential (contains a digit or symbol), not prose like "password is issued"
    re.compile(r"(?i)\b(?:password|passwd|pwd|passcode|secret|api[_ -]?key|token)\s*(?:is|=|:)\s*(?=\S*[\d!@#$%^&*_\-])\S{6,}"),
]
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_AADHAAR = re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_IPV4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
_PHONE = re.compile(r"(?<![\w-])\+?\d[\d\s().\-]{7,16}\d(?![\w-])")
_EMP_ID = re.compile(r"\bEMP-?\d{4,7}\b", re.I)


def redact(text: str, *, keep_emails: bool = False) -> RedactionResult:
    """Replace PII/secrets with typed placeholders such as [EMAIL] and report what was removed."""
    counts: Counter = Counter()
    out = text

    def sub(pattern: re.Pattern, label: str, validator=None) -> None:
        nonlocal out

        def _r(m: re.Match) -> str:
            if validator and not validator(m.group(0)):
                return m.group(0)
            counts[label] += 1
            return f"[{label}]"

        out = pattern.sub(_r, out)

    for p in _SECRET_PATTERNS:
        sub(p, "SECRET")
    if not keep_emails:
        sub(_EMAIL, "EMAIL")
    sub(_CARD, "CARD", lambda s: _luhn_ok(re.sub(r"\D", "", s)) and 13 <= len(re.sub(r"\D", "", s)) <= 19)
    sub(_SSN, "NATIONAL_ID")
    sub(_AADHAAR, "NATIONAL_ID")
    sub(_IBAN, "IBAN")
    sub(_IPV4, "IP")
    sub(_PHONE, "PHONE", lambda s: 9 <= len(re.sub(r"\D", "", s)) <= 15)
    sub(_EMP_ID, "EMPLOYEE_ID")
    return RedactionResult(out, counts)


def redact_obj(obj: Any, *, keep_emails: bool = False) -> Any:
    """Recursively redact every string inside a JSON-like structure."""
    if isinstance(obj, str):
        return redact(obj, keep_emails=keep_emails).text
    if isinstance(obj, dict):
        return {k: redact_obj(v, keep_emails=keep_emails) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v, keep_emails=keep_emails) for v in obj]
    return obj
