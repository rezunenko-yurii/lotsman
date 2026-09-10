"""JSON configs as data that references code.

Game projects wire code from configs: `"$type": "Device.Connection.
InternetManagerSettings"`, keys named after members (`"p_Checker"` →
property `Checker`), type names in arrays (`"t_CrmConnector"`), enum values
as strings (`"$merge": "Union"`). Renaming the member silently breaks the
config, so these count as references — with lines — next to code.

Same rule as C#: only PascalCase names count; `"logLevel"` or
`"statusBar"` are dictionary keys of the config, not code members.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from lotsman.textutil import STOPWORDS

KEY_KIND = "key"
MAX_JSON_BYTES = 256_000  # configs are small; bigger JSON is data dumps
OUTLINE_DEPTH = 2         # keys shown by `outline file.json`
# Key/value decorations that are not part of the code name: markers like
# `-------LOCKED-------`, `$`/`*` instruction sigils, member prefixes
# (ExDi: `p_` property, `f_` field, `t_` type).
_MARKER_RE = re.compile(r"^-+[A-Z]+-+")
MEMBER_PREFIXES = ("p_", "f_", "t_")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TOKEN_RE = re.compile(
    r'"(?:[^"\\\n]|\\.)*"'        # string
    r"|//[^\n]*|/\*.*?\*/"        # comments (tolerated)
    r"|[{}\[\]:,]"                # punctuation
    r"|[^\s{}\[\]:,\"/]+",        # number / literal
    re.S)


@dataclass
class JsonKey:
    name: str
    line: int
    end_line: int
    signature: str


def looks_like_json(data: bytes) -> bool:
    head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    return head[:1] in (b"{", b"[")


def code_name(text: str) -> str | None:
    """The code name a JSON key or string value stands for, if any."""
    s = _MARKER_RE.sub("", text.strip()).lstrip("$*")
    for prefix in MEMBER_PREFIXES:
        if s.startswith(prefix) and len(s) > len(prefix) and s[len(prefix)].isupper():
            s = s[len(prefix):]
            break
    s = s.split(",", 1)[0].strip()    # `Ns.Type, Assembly`
    if "." in s:
        s = s.rsplit(".", 1)[-1]      # `Ns.Type` → `Type`
    if (not _IDENT_RE.match(s) or not s[:1].isupper() or len(s) < 3
            or len(s) > 64 or s in STOPWORDS):
        return None
    return s


def extract_json(source: bytes) -> tuple[list[JsonKey], Counter, dict[str, list[int]]]:
    """(outline keys up to OUTLINE_DEPTH, name counts, name -> lines)."""
    text = source.decode("utf-8", errors="replace")
    lines_text = text.splitlines()
    tokens: list[tuple[str, int]] = []  # (token, line)
    line, pos = 1, 0
    for m in _TOKEN_RE.finditer(text):
        line += text.count("\n", pos, m.start())
        pos = m.start()
        tok = m.group(0)
        if not tok.startswith(("//", "/*")):
            tokens.append((tok, line))

    counts: Counter = Counter()
    found: dict[str, set[int]] = {}

    def note(raw: str, at: int) -> None:
        name = code_name(raw)
        if name:
            counts[name] += 1
            found.setdefault(name, set()).add(at)

    keys: list[JsonKey] = []
    stack: list[str] = []                 # "{" / "["
    pending: list[tuple[int, int] | None] = []  # per open object: (keys index, depth) awaiting value end
    for i, (tok, at) in enumerate(tokens):
        if tok in ("{", "["):
            stack.append(tok)
            pending.append(None)
        elif tok in ("}", "]"):
            if stack:
                stack.pop()
                pending.pop()
            _close(pending, keys, at)
        elif tok.startswith('"'):
            raw = tok[1:-1]
            is_key = (i + 1 < len(tokens) and tokens[i + 1][0] == ":"
                      and stack and stack[-1] == "{")
            note(raw, at)
            if is_key:
                if len(stack) <= OUTLINE_DEPTH:
                    sig = lines_text[at - 1].strip() if at <= len(lines_text) else tok
                    keys.append(JsonKey(raw, at, at, sig[:120]))
                    pending[-1] = (len(keys) - 1, len(stack))
            else:
                _close(pending, keys, at)
        elif tok not in (":", ","):
            _close(pending, keys, at)  # number / true / false / null
    return keys, counts, {n: sorted(v) for n, v in found.items()}


def _close(pending: list, keys: list[JsonKey], at: int) -> None:
    """A value ended at line `at`: close the key awaiting it in the current object."""
    if pending and pending[-1] is not None:
        idx, _ = pending[-1]
        keys[idx].end_line = max(keys[idx].end_line, at)
        pending[-1] = None
