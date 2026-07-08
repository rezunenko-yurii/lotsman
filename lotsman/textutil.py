"""Text utilities: token estimation, identifier splitting, stopwords."""

from __future__ import annotations

import re

# Cheap token estimator: ~4 chars per token for code (close enough for budgeting).
def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,63}")

_CAMEL_RE = re.compile(
    r"[A-Z]+(?=[A-Z][a-z0-9])|[A-Z]?[a-z0-9]+|[A-Z]+|[0-9]+"
)

# Keywords/builtins across supported languages; excluded from reference counting
# so they never create graph edges or search noise.
STOPWORDS = frozenset("""
abstract and as assert async await base bool boolean break byte case catch chan
char class const continue crate def default defer del delegate do double dyn
elif else enum except export extends extern false final finally float fn for
foreach from func function get global go goto if impl implements import in
init instanceof int interface internal is lambda let long loop map match mod
module mut namespace new nil none nonlocal not null object of operator or
override package pass print private protected pub public raise range readonly
ref return sealed select self set short sizeof static str strict string struct
super switch sync this throw throws trait true try type typedef typeof uint
undefined union unsafe unsigned use using var vec void volatile where while
with yield
args callable classmethod cls console dict document enumerate exports getattr
hasattr isinstance iter kwargs len list module next open property repr require
setattr sorted staticmethod tuple window zip
add append clear copy count decode discard encode endswith extend filter
format index insert items join keys lower pop popitem push remove replace
reverse setdefault shift slice sort splice split startswith strip title
update upper values write
""".split())


def split_ident(name: str) -> list[str]:
    """Split an identifier into lowercase subtokens: fooBarBaz -> [foo, bar, baz]."""
    parts: list[str] = []
    for chunk in name.split("_"):
        if not chunk:
            continue
        parts.extend(m.group(0).lower() for m in _CAMEL_RE.finditer(chunk))
    return parts


def tokenize(text: str) -> list[str]:
    """Tokenize text for BM25: identifiers plus their subtokens."""
    tokens: list[str] = []
    for m in IDENT_RE.finditer(text):
        ident = m.group(0)
        low = ident.lower()
        if low in STOPWORDS:
            continue
        tokens.append(low)
        subs = split_ident(ident)
        if len(subs) > 1:
            tokens.extend(s for s in subs if len(s) > 1 and s not in STOPWORDS)
    return tokens


def is_test_path(path: str) -> bool:
    parts = path.lower().split("/")
    return any(p in ("tests", "test", "__tests__", "spec") for p in parts[:-1]) \
        or parts[-1].startswith("test_") or parts[-1].endswith("_test.py")


def is_well_named(name: str) -> bool:
    """Long descriptive identifiers (snake_case/camelCase, >=8 chars) — likely
    project-specific, deserve extra graph weight."""
    if len(name) < 8:
        return False
    return "_" in name or (name != name.lower() and name != name.upper())
