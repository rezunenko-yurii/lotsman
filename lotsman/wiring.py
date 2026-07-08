"""Project wiring patterns: regexes that surface DI/reflection/config-string
references invisible to tree-sitter. Config: .lotsman/wiring.json."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

CONFIG_REL = ".lotsman/wiring.json"


def load_from_text(text: str) -> tuple[list[re.Pattern[str]], list[str]]:
    patterns: list[re.Pattern[str]] = []
    errors: list[str] = []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [], [f"wiring.json invalid JSON: {exc}"]
    if not isinstance(data, dict):
        return [], ["wiring.json root must be an object"]
    raw_patterns = data.get("patterns", [])
    if not isinstance(raw_patterns, list):
        return [], ['wiring.json "patterns" must be a list']
    for i, entry in enumerate(raw_patterns):
        if not isinstance(entry, dict):
            errors.append(f"patterns[{i}]: pattern entry must be an object")
            continue
        raw = entry.get("regex", "")
        try:
            pat = re.compile(raw)
        except re.error as exc:
            errors.append(f"patterns[{i}]: bad regex: {exc}")
            continue
        if pat.groups != 1:
            errors.append(f"patterns[{i}]: exactly one capture group required")
            continue
        patterns.append(pat)
    return patterns, errors


def load(root: Path) -> tuple[list[re.Pattern[str]], list[str]]:
    cfg = root / CONFIG_REL
    if not cfg.exists():
        return [], []
    try:
        return load_from_text(cfg.read_text(encoding="utf-8"))
    except OSError as exc:
        return [], [f"wiring.json unreadable: {exc}"]


def apply(patterns: list[re.Pattern[str]], data: bytes) -> Counter:
    if not patterns:
        return Counter()
    text = data.decode("utf-8", errors="ignore")
    hits: Counter = Counter()
    for pat in patterns:
        for match in pat.finditer(text):
            name = match.group(1)
            if name:
                hits[name] += 1
    return hits


def config_sha(root: Path) -> str:
    cfg = root / CONFIG_REL
    if not cfg.exists():
        return ""
    return hashlib.sha256(cfg.read_bytes()).hexdigest()
