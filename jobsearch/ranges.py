"""Pick the right option from "years of experience" ranges (0-2, 2-4, 4-6, 6+, Less than 2, ...)."""

from __future__ import annotations

import math
import re

_NUM = r"(\d+(?:\.\d+)?)"


def parse(option: str) -> tuple[float, float] | None:
    """Return the inclusive [low, high] a range option covers, or None if it isn't a years range.
    Integer upper bounds count the whole year: "2-4" covers up to 4.99 (4.3 years is "4 years")."""
    t = option.lower().replace("–", "-").replace("—", "-")
    m = re.search(rf"{_NUM}\s*(?:-|to)\s*{_NUM}", t)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return lo, (hi + 0.999 if hi.is_integer() else hi)
    m = re.search(rf"(?:less than|under|fewer than|below|<)\s*{_NUM}", t)
    if m:
        return 0.0, float(m.group(1)) - 0.001
    m = re.search(rf"(?:more than|over|above|>)\s*{_NUM}", t)
    if m:
        return float(m.group(1)) + 0.001, math.inf
    m = re.search(rf"{_NUM}\s*(?:\+|years?\s*\+|\s*or more|\s*and above|\s*years? or more|\s*years? and above)", t)
    if m:
        return float(m.group(1)), math.inf
    if re.search(r"\b(no|none|fresher|0 years?)\b", t) and not re.search(r"\d+\s*-", t):
        return 0.0, 0.999
    m = re.fullmatch(rf"\s*{_NUM}\s*(?:years?|yrs?)?\s*", t)
    if m:
        lo = float(m.group(1))
        return lo, lo + 0.999
    return None


def looks_like_ranges(options: list[str]) -> bool:
    return sum(parse(o) is not None for o in options) >= 2


def pick(options: list[str], years: float) -> int | None:
    """Index of the option whose range contains `years`; on overlap prefer the higher lower bound
    (so 4.3 picks "4-6" over "2-4"), then the narrower range."""
    best: tuple[float, float, int] | None = None
    for i, o in enumerate(options):
        r = parse(o)
        if r is None or not (r[0] <= years <= r[1]):
            continue
        key = (r[0], -(r[1] - r[0]), i)
        if best is None or key[:2] > best[:2]:
            best = key
    return best[2] if best else None
