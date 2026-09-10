"""Caller check after a signature edit (C#, name-based).

At index time every C# method gets its parameter shape (`arity`: required,
total, `params`, extension `this`), and a changed shape is remembered against
the version the index saw before the edit. `impact` then reads each call site
of such a method in the files `change` would list and counts the arguments:
a count no current overload takes breaks the build; a count that fits only
through optional/`params` parameters may silently change meaning.

Text-based: arguments are split on top-level commas after stripping strings,
comments and generic argument lists. Calls it cannot parse are reported as
unclear, never as fine.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

MAX_DECL_LINES = 30   # a parameter list longer than this is not parsed
MAX_CALL_LINES = 30
FILES_SHOWN = 15

# One left-to-right pass: a `"` inside a comment opens no string, `//` inside
# a string starts no comment. Verbatim strings may span lines.
_TOKEN_RE = re.compile(r'/\*.*?\*/|//[^\n]*|\$?@\$?"(?:[^"]|"")*"|\$?"(?:[^"\\\n]|\\.)*"'
                       r"|'(?:[^'\\\n]|\\.){1,8}'", re.S)
_GENERIC_RE = re.compile(r"(?<=[\w>\]])\s*<[\w\s.,\[\]?]*>")
_ATTRIBUTE_RE = re.compile(r"\[[^\[\]]*\]")
_NON_NEWLINE_RE = re.compile(r"[^\n]")


def _clean_token(m: re.Match) -> str:
    t = m.group(0)
    if t.startswith("/"):
        return _NON_NEWLINE_RE.sub(" ", t)
    # A literal keeps its delimiters: `Log("x")` still has one argument.
    return t[0] + _NON_NEWLINE_RE.sub(" ", t[1:-1]) + t[-1]


def _clean(text: str) -> str:
    """Code with strings, chars and comments blanked (lengths and line
    breaks kept)."""
    return _TOKEN_RE.sub(_clean_token, text)


def clean_lines(source: str) -> list[str]:
    return _clean(source).split("\n")


def _drop_generics(text: str) -> str:
    for _ in range(4):  # nested: Dictionary<string, List<int>>
        new = _GENERIC_RE.sub("", text)
        if new == text:
            break
        text = new
    return text


def _paren_body(text: str, open_at: int) -> str | None:
    """Text between the `(` at `open_at` and its matching `)`."""
    depth = 0
    for i in range(open_at, len(text)):
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i]
    return None


def _split_top(body: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts] if body.strip() else []


_CALL_TAIL_RE = re.compile(r"\s*(?:<[\w\s.,\[\]?<>]*>)?\s*\(")


def _name_calls(text: str, name: str):
    """(start of `name`, index of its `(`) for each `name(` / `name<T>(`.
    str.find, not a per-name regex: compiling one per method dominated."""
    i = text.find(name)
    while i != -1:
        before = text[i - 1] if i else " "
        if not (before.isalnum() or before in "_$@"):
            m = _CALL_TAIL_RE.match(text, i + len(name))
            if m:
                yield i, m.end() - 1
        i = text.find(name, i + 1)


@dataclass(frozen=True)
class Arity:
    required: int
    total: int           # without the `params` array
    variadic: bool = False
    extension: bool = False

    def encode(self) -> str:
        return f"{self.required},{self.total},{'p' if self.variadic else ''}{'t' if self.extension else ''}"

    @staticmethod
    def decode(text: str) -> "Arity":
        req, total, flags = text.split(",")
        return Arity(int(req), int(total), "p" in flags, "t" in flags)

    def fits(self, n: int, static: bool | None = None) -> str | None:
        """'exact', 'defaults' (optional/params fill it) or None. For an
        extension method `static` says whether the call passes `this`
        (`Ext.M(x, a)`) or not (`x.M(a)`); None: either."""
        found = None
        forms = (n, n + 1) if static is None else ((n,) if static else (n + 1,))
        for k in (forms if self.extension else (n,)):
            if k == self.total and not self.variadic:
                return "exact"
            if self.required <= k and (self.variadic or k <= self.total):
                found = "defaults"
        return found

    def show(self) -> str:
        req, total = self.required, self.total
        return f"{req}+" if self.variadic else (f"{req}-{total}" if req != total else str(req))


def arity(source_lines: list[str], line: int, name: str, cleaned: bool = False) -> Arity | None:
    """Parameter shape of the declaration of `name` starting at `line`;
    None for properties and unparsable declarations. `cleaned`: the lines
    already went through `clean_lines` (once per file at index time)."""
    text = "\n".join(source_lines[line - 1:line - 1 + MAX_DECL_LINES])
    if not cleaned:
        text = _clean(text)
    start, paren = next(_name_calls(text, name), (None, None))
    if start is None or re.search(r"[{;]|=>", text[:start]):  # a call in a body, not the declaration
        return None
    body = _paren_body(text, paren)
    if body is None:
        return None
    params = _split_top(_drop_generics(_ATTRIBUTE_RE.sub(" ", body)))
    required = total = 0
    variadic = extension = False
    for i, p in enumerate(params):
        if re.match(r"params\b", p):
            variadic = True
            continue
        if i == 0 and re.match(r"this\b", p):
            extension = True
        total += 1
        if not _top_level_eq(p):
            required += 1
    return Arity(required, total, variadic, extension)


def _top_level_eq(param: str) -> bool:
    depth = 0
    for i, ch in enumerate(param):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "=" and depth == 0 and param[i + 1:i + 2] != ">" and param[i - 1:i] not in "=!<>":
            return True
    return False


_RECEIVER_RE = re.compile(r"(\w+)\s*[?!]?\.\s*$")
_ANY_RECEIVER_RE = re.compile(r"[\w)\]>]\s*[?!]?\.\s*$")


def calls(source_lines: list[str], line: int, name: str) -> list[tuple[int | None, str | None]]:
    """(argument count, receiver) of the calls to `name` starting on `line`.
    Count None: not parsed within MAX_CALL_LINES. Receiver: the identifier
    before `.name`, "" for a bare call, None when it is an expression."""
    chunk = "\n".join(source_lines[line - 1:line - 1 + MAX_CALL_LINES])
    text = _clean(chunk)
    first_len = len(source_lines[line - 1]) if line - 1 < len(source_lines) else 0
    out: list[tuple[int | None, str | None]] = []
    for start, paren in _name_calls(text, name):
        if start > first_len:
            break
        body = _paren_body(text, paren)
        pre = text[max(0, start - 200):start]
        rm = _RECEIVER_RE.search(pre)
        receiver = rm.group(1) if rm else (None if _ANY_RECEIVER_RE.search(pre) else "")
        out.append((None if body is None else len(_split_top(_drop_generics(body))), receiver))
    return out


def call_counts(source_lines: list[str], line: int, name: str) -> list[int | None]:
    return [n for n, _ in calls(source_lines, line, name)]


_BUILTIN_TYPES = {"string", "int", "bool", "float", "double", "long", "object", "byte",
                  "char", "decimal", "short", "uint", "ulong", "ushort", "sbyte", "dynamic"}
_NOT_TYPES = {"return", "new", "out", "ref", "in", "is", "as", "await", "yield", "else",
              "case", "throw", "using", "params", "this", "typeof", "var", "static",
              "readonly", "const", "public", "private", "protected", "internal", "get", "set"}


def declared_types(text: str, ident: str) -> set[str]:
    """Types `ident` is declared with in cleaned file text (fields, locals,
    parameters, properties, `var x = new T(`); empty when unknown."""
    e = re.escape(ident)
    found = set()
    for m in re.finditer(rf"\b([A-Za-z_]\w*)\s*(<[^;(){{}}=]*>)?\s*(\[[\s,]*\])?\??\s+{e}"
                         rf"\s*(?:[;=,){{]|=>)", text):
        t = m.group(1)
        if t in _NOT_TYPES:
            continue
        found.add("[]" if m.group(3) else t)
    found |= set(re.findall(rf"\b(?:var\s+)?{e}\s*=\s*new\s+([A-Za-z_]\w*)", text))
    return found


class _Receivers:
    """Is a call clearly another type's method? Text heuristics over the
    caller file: a receiver declared with a type outside the owner's family,
    a static call on another indexed type, or a bare call inside a type that
    declares its own same-name method."""

    def __init__(self, store, name: str, family: set[str]):
        self.store, self.name, self.family = store, name, family
        self._types: dict[str, bool] = {}

    def _is_type(self, ident: str) -> bool:
        from lotsman.store import TYPE_KINDS
        if ident not in self._types:
            self._types[ident] = any(r.kind in TYPE_KINDS for r in self.store.symbols_named(ident))
        return self._types[ident]

    def other(self, path: str, text: str, line: int, receiver: str | None) -> bool:
        if not self.family or receiver is None or receiver == "base":
            return False
        if receiver in ("", "this"):
            from lotsman.changeview import _owned
            enclosing = owner_at(self.store.symbols_in_file(path), line)
            return (enclosing is not None and enclosing not in self.family
                    and bool(_owned(self.store, path, self.name, enclosing)))
        if receiver in self.family:
            return False
        types = declared_types(text, receiver)
        if types:
            return not (types & self.family)
        return self._is_type(receiver) or receiver in _BUILTIN_TYPES


def fit(n: int | None, current: list[Arity], old: list[Arity] | None = None,
        static: bool | None = None) -> str:
    """'breaks' | 'defaults' | 'exact' | 'unclear' | 'other' for one call.
    With `old`, a call the old shapes did not take either is 'other': a
    same-name method of another type, not a caller of this one."""
    if n is None:
        return "unclear"
    if old is not None and not any(a.fits(n, static) for a in old):
        return "other"
    kinds = {a.fits(n, static) for a in current}
    if "exact" in kinds:
        return "exact"
    return "defaults" if "defaults" in kinds else "breaks"


def shapes(arities: list[Arity]) -> str:
    return " / ".join(a.show() for a in arities) or "?"


# --- impact section --------------------------------------------------------------

@dataclass
class CallIssue:
    path: str
    line: int
    count: int | None
    kind: str  # breaks / defaults / unclear


def _args(n: int | None) -> str:
    return "?" if n is None else f"{n} arg" + ("" if n == 1 else "s")


def check_change(store, root: Path, path: str, name: str, old: list[Arity],
                 owner: str | None, ext_class: str | None = None) -> list[str]:
    """Report lines for one changed method (all overloads in `path`);
    `ext_class`: the static class declaring it when it is an extension."""
    from lotsman.changeview import _family, collect_change
    rows = [r for r in store.method_arities(path) if r[0] == name]
    current = [Arity.decode(a) for _, _, a, _ in rows if a]
    if not current:
        return []
    decl_line, sig = rows[0][1], rows[0][3]
    target = f"{owner}.{name}" if owner else name
    rep = collect_change(store, target)
    family: set[str] = set()
    if rep and rep.owner and not rep.extension:
        supers, subs = _family(store, rep.owner)
        family = {rep.owner} | supers | {t for t, _, _ in subs}
    receivers = _Receivers(store, name, family)
    head = (f"  {target} ({path}:{decl_line}): {shapes(old)} → {shapes(current)} params"
            f"  `{sig}`")
    issues: list[CallIssue] = []
    cache: dict[str, list[str]] = {}
    sites = list(rep.callers) if rep else []
    defs = {(path, ln) for _, ln, _, _ in rows}
    for site in sites:
        try:
            if site.path not in cache:
                raw = (root / site.path).read_text(encoding="utf-8", errors="replace")
                cache[site.path] = (raw.splitlines(), _clean(raw))
        except OSError:
            continue
        lines, text = cache[site.path]
        for ln in site.lines:
            if (site.path, ln) in defs:
                continue
            for n, receiver in calls(lines, ln, name):
                # Extension: `Ext.M(x)` or a bare call inside Ext passes `this`.
                static = receiver in ("", ext_class) if ext_class else None
                kind = fit(n, current, old, static)
                if kind != "other" and receivers.other(site.path, text, ln, receiver):
                    kind = "other"
                if kind != "exact":
                    issues.append(CallIssue(site.path, ln, n, kind))
    out = [head]
    others = [i for i in issues if i.kind == "other"]
    issues = [i for i in issues if i.kind != "other"]
    total_calls = len(issues)
    for kind, title in (("breaks", "breaks — no overload takes this argument count"),
                        ("defaults", "maybe — compiles only through optional/params "
                                     "parameters; check the intent"),
                        ("unclear", "unclear — arguments not parsed")):
        found = [i for i in issues if i.kind == kind]
        if not found:
            continue
        by_file: dict[str, list[CallIssue]] = {}
        for i in found:
            by_file.setdefault(i.path, []).append(i)
        out.append(f"    {title}: {len(found)} calls in {len(by_file)} files")
        for p, items in list(by_file.items())[:FILES_SHOWN]:
            counts = sorted({_args(i.count) for i in items})
            out.append(f"      {p}:{','.join(str(i.line) for i in items)} ({', '.join(counts)})")
        if len(by_file) > FILES_SHOWN:
            out.append(f"      … {len(by_file) - FILES_SHOWN} more files")
    if not total_calls:
        out.append(f"    callers: {sum(len(s.lines) for s in sites)} call lines, all fit")
    if others:
        out.append(f"    ({len(others)} same-name calls skipped as another type's: receiver "
                   "type or an argument count the old version never took)")
    if rep:
        family = [i.row for i in rep.impls + rep.declared_by]
        other = []
        for r in family:
            a = store.arity_at(r.path, r.line)
            if a and Arity.decode(a) not in current:
                other.append(f"      {r.path}:{r.line}  {r.signature}")
        if other:
            out.append("    overrides/declarations with another parameter count:")
            out += other[:FILES_SHOWN]
    return out


def owner_at(syms, line: int) -> str | None:
    """Innermost type whose range holds `line`."""
    from lotsman.store import TYPE_KINDS
    types = [s for s in syms if s.kind in TYPE_KINDS and s.line <= line <= s.end_line
             and s.line != line]
    return max(types, key=lambda s: s.line).name if types else None


def render_signature_changes(store, root: Path, changed: list[str]) -> list[str]:
    """`impact` section: methods of the changed files whose parameter count
    changed since the index last saw them, with call sites checked."""
    out: list[str] = []
    for path in changed:
        rows = store.sig_changes(path)
        if not rows:
            continue
        syms = store.symbols_in_file(path)
        for name, old_json in rows:
            old = [Arity.decode(a) for a in json.loads(old_json)]
            decl = next((s for s in syms if s.name == name and s.kind == "method"), None)
            if decl is None:
                continue
            # extension methods: callers rarely name the class
            owner = owner_at(syms, decl.line)
            if "(this " in decl.signature:
                out += check_change(store, root, path, name, old, None, owner)
            else:
                out += check_change(store, root, path, name, old, owner)
    if out:
        out.insert(0, "Signature changes (parameter count vs the version indexed before the "
                      "edit; call sites counted by text, name-based):")
    return out
