"""Deterministic checks on the model's answer, independent of the prompt.

The rubric fails a bot that invents a price, so prices are checked in code rather than trusted:
every money amount in an answer must also appear in the retrieved source text (or be the
user's own figure being corrected).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٬٫", "01234567890123456789,.")
_CURRENCY = r"(?:US\$|USD|MXN|EUR|GBP|CAD|AUD|INR|THB|TRY|\$|€|£|₹|฿|₺)"
_NUMBER = r"\d{1,3}(?:[,.\s  ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_WORDS = r"(?:dollars?|d[oó]lares|euros?|pesos?|دولار)"
MONEY = re.compile(
    rf"{_CURRENCY}\s?(?P<a>{_NUMBER})|(?P<b>{_NUMBER})\s?(?:{_CURRENCY}|{_WORDS})(?![A-Za-z])",
    re.IGNORECASE,
)
NUMBER = re.compile(_NUMBER)


def _ascii(text: str) -> str:
    return text.translate(_DIGITS)


def _value(raw: str) -> Decimal | None:
    """'18,995' / '18.995' / '18 995' / '18995.00' -> Decimal('18995')."""
    raw = raw.strip()
    grouped = re.fullmatch(r"(\d{1,3}(?:[,.\s  ]\d{3})+)(?:[.,](\d{1,2}))?", raw)
    if grouped:
        whole = re.sub(r"\D", "", grouped.group(1))
        cents = grouped.group(2)
    else:
        whole, _, cents = raw.replace(",", ".").partition(".")
    try:
        value = Decimal(whole + (f".{cents}" if cents else ""))
    except InvalidOperation:
        return None
    return value.normalize() if value != value.to_integral() else value.to_integral()


def money_amounts(text: str) -> list[tuple[str, Decimal]]:
    found = []
    for match in MONEY.finditer(_ascii(text)):
        value = _value(match.group("a") or match.group("b"))
        if value is not None:
            found.append((match.group(0).strip(), value))
    return found


def numbers_in(text: str) -> set[Decimal]:
    values = set()
    for match in NUMBER.finditer(_ascii(text)):
        value = _value(match.group(0))
        if value is not None:
            values.add(value)
    return values


def unsupported_amounts(answer: str, source_text: str, user_text: str = "") -> list[str]:
    """Money amounts in `answer` found neither in the sources nor in the user's own message."""
    allowed = numbers_in(source_text) | numbers_in(user_text)
    return [raw for raw, value in money_amounts(answer) if value not in allowed]


def unsupported_user_claims(user_text: str, source_text: str) -> list[str]:
    """Prices the user quoted that the sources don't contain (the "I heard it costs $50" trick)."""
    known = numbers_in(source_text)
    return [raw for raw, value in money_amounts(user_text) if value not in known]


DOCTOR = re.compile(r"\bDr\.?\s+((?:[A-Z][A-Za-z\u00C0-\u017F'-]+\s?){1,3})")


def unsupported_doctor_names(answer: str, source_text: str) -> list[str]:
    """Doctor names in the answer that the cited sources don't contain, e.g. a misspelling copied
    from a patient review ("Dr. Carlos Batista") when the cited doctor list says "Bautista"."""
    missing = []
    for match in DOCTOR.finditer(answer):
        # First two words only: "Dr. Carlos Bautista Medical Director" is still the right name.
        name = " ".join(match.group(1).split()[:2])
        if name and name not in source_text and name not in missing:
            missing.append(name)
    return missing
