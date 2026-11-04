"""OCR accuracy metrics. CER/WER are only meaningful against a human-verified
ground truth; never report them for runs without one."""

from __future__ import annotations

import re
import unicodedata


def normalize(text: str, ignore_space: bool = False, ignore_case: bool = False) -> str:
    t = unicodedata.normalize("NFC", text or "")
    t = t.replace("\r\n", "\n").replace(" ", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = "\n".join(line.strip() for line in t.split("\n")).strip()
    if ignore_space:
        t = re.sub(r"\s+", "", t)
    if ignore_case:
        t = t.lower()
    return t


def levenshtein(a, b) -> int:
    """Edit distance between two sequences (str or list)."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(reference: str, hypothesis: str, ignore_space: bool = True) -> float:
    r = normalize(reference, ignore_space=ignore_space)
    h = normalize(hypothesis, ignore_space=ignore_space)
    if not r:
        return 0.0 if not h else 1.0
    return levenshtein(r, h) / len(r)


def wer(reference: str, hypothesis: str) -> float:
    r = normalize(reference).split()
    h = normalize(hypothesis).split()
    if not r:
        return 0.0 if not h else 1.0
    return levenshtein(r, h) / len(r)


def similarity(a: str, b: str) -> float:
    """1 - normalized edit distance, spaces ignored. 1.0 = identical."""
    x = normalize(a, ignore_space=True)
    y = normalize(b, ignore_space=True)
    if not x and not y:
        return 1.0
    return 1.0 - levenshtein(x, y) / max(len(x), len(y))


def missed_lines(reference: str, hypothesis: str, threshold: float = 0.7) -> list[str]:
    """Reference lines with no hypothesis line of similarity >= threshold (missed regions).
    Hypothesis lines may be split/merged, so a reference line also counts as found when it
    appears inside the hypothesis text."""
    hyp_lines = [ln for ln in normalize(hypothesis).split("\n") if ln.strip()]
    out = []
    for ref in [ln for ln in normalize(reference).split("\n") if ln.strip()]:
        if normalize(ref, ignore_space=True) in normalize(hypothesis, ignore_space=True):
            continue
        if not any(similarity(ref, h) >= threshold for h in hyp_lines):
            out.append(ref)
    return out


def has_hangul(text: str) -> bool:
    return any("가" <= c <= "힣" or "ㄱ" <= c <= "ㆎ" for c in text)


def text_width_units(text: str) -> float:
    """Approximate rendered width in units of line height (for plausibility checks)."""
    u = 0.0
    for c in text:
        o = ord(c)
        if "가" <= c <= "힣" or 0x4E00 <= o <= 0x9FFF or 0x3040 <= o <= 0x30FF:
            u += 0.95
        elif c.isspace():
            u += 0.33
        elif c.isdigit():
            u += 0.55
        elif c.isalpha():
            u += 0.68 if c.isupper() else 0.52
        else:
            u += 0.35
    return u
