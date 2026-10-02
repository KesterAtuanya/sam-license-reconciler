"""Turns the messy display names that discovery reports into one product name.

Discovery sees "Microsoft Visio Professional 2021 (64-bit)", "Microsoft Visio
Professional 2021 - en-us" and "Visio Pro 2021" as three different titles, but
they all consume the same license. SAM Pro does this with its content library;
this module does the same job with ordered regex rules, so the rules stay
readable and easy to extend.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .models import Install


@dataclass
class Rule:
    product: str
    pattern: re.Pattern


# First match wins, so more specific editions come before general ones.
DEFAULT_RULES: list[tuple[str, str]] = [
    ("Microsoft Visio Professional 2021", r"\bvisio\b.*\b(pro|professional)\b.*2021"),
    ("Microsoft Visio Standard 2021", r"\bvisio\b.*\bstandard\b.*2021"),
    ("Microsoft Project Professional 2021", r"\bproject\b.*\b(pro|professional)\b.*2021"),
    ("Adobe Acrobat Pro", r"\bacrobat\b.*\bpro\b"),
    ("Adobe Acrobat Reader", r"\bacrobat\b.*\breader\b|\badobe reader\b"),
    ("Adobe Creative Cloud All Apps", r"\bcreative cloud\b"),
    ("Autodesk AutoCAD LT", r"\bautocad\b.*\blt\b"),
    ("Autodesk AutoCAD", r"\bautocad\b"),
    ("Microsoft SQL Server Enterprise 2022", r"\bsql server\b.*2022.*\benterprise\b|\bsql server\b.*\benterprise\b.*2022"),
    ("Microsoft SQL Server Standard 2022", r"\bsql server\b.*2022.*\bstandard\b|\bsql server\b.*\bstandard\b.*2022"),
    ("Microsoft SQL Server Express", r"\bsql server\b.*\bexpress\b"),
    ("Windows Server 2022 Datacenter", r"\bwindows server\b.*2022.*\bdatacenter\b"),
    ("Windows Server 2022 Standard", r"\bwindows server\b.*2022.*\bstandard\b"),
    ("Oracle Database Enterprise Edition", r"\boracle\b.*\bdatabase\b.*\benterprise\b"),
    ("Tableau Desktop", r"\btableau desktop\b"),
    ("TechSmith Snagit", r"\bsnagit\b"),
    ("WinZip", r"\bwinzip\b"),
    ("7-Zip", r"\b7-?zip\b"),
    ("Notepad++", r"\bnotepad\+\+"),
    ("Google Chrome", r"\bgoogle chrome\b"),
    ("Zoom Workplace", r"\bzoom\b"),
]


def build_rules(extra_file: str | Path | None = None) -> list[Rule]:
    pairs = list(DEFAULT_RULES)
    if extra_file:
        with open(extra_file, encoding="utf-8") as fh:
            custom = json.load(fh)
        # Custom rules go first so a team can override a default.
        pairs = [(r["product"], r["pattern"]) for r in custom] + pairs
    return [Rule(product, re.compile(pattern, re.IGNORECASE)) for product, pattern in pairs]


def normalize_installs(installs: list[Install], rules: list[Rule]) -> Counter:
    """Fill in Install.product. Returns a count of display names nothing matched,
    so they can be reported and given a rule."""
    unmatched: Counter = Counter()
    for inst in installs:
        if inst.product:  # already normalized at the source (for example SAM Pro)
            continue
        for rule in rules:
            if rule.pattern.search(inst.raw_name):
                inst.product = rule.product
                break
        else:
            unmatched[inst.raw_name.strip()] += 1
    return unmatched
