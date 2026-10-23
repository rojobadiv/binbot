from __future__ import annotations

import html
import random
import re
from typing import Iterable

_BRAND_EMOJI = {
    "VISA": "🔵", "MASTERCARD": "🔴", "AMEX": "🟢", "AMERICAN EXPRESS": "🟢",
    "DISCOVER": "🟠", "JCB": "🟣", "UNIONPAY": "🟤", "DINERS": "⚪",
    "DINERS CLUB": "⚪", "MAESTRO": "🔷", "ELO": "🟡", "MIR": "🟩",
    "HIPERCARD": "🟨", "TROY": "🔶", "RUPAY": "🟪",
}

_PREFIX_BRAND = [
    ("4", "VISA"),
    ("51", "MASTERCARD"), ("52", "MASTERCARD"), ("53", "MASTERCARD"),
    ("54", "MASTERCARD"), ("55", "MASTERCARD"), ("22", "MASTERCARD"),
    ("23", "MASTERCARD"), ("24", "MASTERCARD"), ("25", "MASTERCARD"),
    ("26", "MASTERCARD"), ("27", "MASTERCARD"),
    ("34", "AMEX"), ("37", "AMEX"),
    ("6011", "DISCOVER"), ("65", "DISCOVER"), ("644", "DISCOVER"), ("645", "DISCOVER"),
    ("35", "JCB"), ("62", "UNIONPAY"), ("36", "DINERS"), ("38", "DINERS"), ("39", "DINERS"),
    ("50", "MAESTRO"), ("56", "MAESTRO"), ("57", "MAESTRO"), ("58", "MAESTRO"),
    ("63", "MAESTRO"), ("67", "MAESTRO"),
]


def esc(value) -> str:
    return html.escape(str(value if value not in (None, "") else "—"))


def flag(cc: str | None) -> str:
    if not cc or len(cc) != 2 or not cc.isalpha():
        return "🏳"
    return "".join(chr(127397 + ord(c)) for c in cc.upper())


def brand_emoji(brand: str | None) -> str:
    if not brand:
        return "💳"
    return _BRAND_EMOJI.get(brand.upper(), "💳")


def guess_brand(bin_value: str) -> str | None:
    for prefix, brand in sorted(_PREFIX_BRAND, key=lambda x: -len(x[0])):
        if bin_value.startswith(prefix):
            return brand
    return None


def bar(percent: float, width: int = 22, filled: str = "█", empty: str = "░") -> str:
    percent = max(0.0, min(100.0, percent))
    n = int(round(width * percent / 100))
    return filled * n + empty * (width - n)


def fmt_secs(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds:02d}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m:02d}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def human(n: int) -> str:
    return f"{n:,}"


_TOKEN_RE = re.compile(r"\d{6,19}")
_BIN_RE = re.compile(r"^\d{6,8}$")


def clean_bin(raw: str) -> str | None:
    digits = re.sub(r"\D", "", str(raw))
    if 6 <= len(digits) <= 8:
        return digits
    if len(digits) > 8:
        return digits[:6]
    return None


def parse_bin_list(text: str, cap: int = 5000) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN_RE.findall(text or ""):
        candidate = token if len(token) <= 8 else token[:6]
        if candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
            if len(out) >= cap:
                break
    return out


def is_bin(text: str) -> bool:
    return bool(_BIN_RE.match((text or "").strip()))


def luhn_check_digit(partial: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(partial)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def luhn_card(bin_value: str, length: int = 16) -> str:
    body = bin_value + "".join(random.choice("0123456789") for _ in range(length - len(bin_value) - 1))
    return body + luhn_check_digit(body)


def luhn_valid(number: str) -> bool:
    digits = [int(c) for c in reversed(number)]
    total = 0
    for i, d in enumerate(digits):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def chunked(seq: list, size: int) -> Iterable[list]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]