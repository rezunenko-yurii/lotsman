"""C# conditional compilation, offline: `#if` regions and Unity define symbols.

Regions come from a line scan (C# directives are whole lines; tree-sitter
shows them too but breaks on unbalanced branches). Each branch is stored with
its *effective* condition: `#else` after `#if A` is `!A`, `#elif B` is
`!A && B`, nesting is ANDed in.

Evaluation is three-valued — a symbol is defined, not defined, or unknown
(`None`): the Editor's active build target, Unity's own `ENABLE_*` switches
or the developer's OS are not knowable from files. `UNITY_IOS &&
!UNITY_EDITOR` is still decidable in the Editor: false.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DIRECTIVE_RE = re.compile(r"^[ \t]*#[ \t]*(if|elif|else|endif|define|undef)\b[ \t]*(.*?)[ \t]*$")
TOKEN_RE = re.compile(r"\s*(&&|\|\||==|!=|!|\(|\)|[A-Za-z_][A-Za-z0-9_]*)")
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class Directive:
    kind: str       # "if" (one branch of #if/#elif/#else), "define", "undef"
    line: int       # line of the #if/#elif/#else (or #define)
    end_line: int   # line of the directive closing the branch
    cond: str       # effective condition (or the symbol for define/undef)


def _strip_comment(expr: str) -> str:
    return expr.split("//", 1)[0].strip()


def _wrap(expr: str) -> str:
    """Parenthesize a condition unless it is a single (negated) symbol."""
    return expr if re.fullmatch(r"!?\s*[A-Za-z_]\w*|true|false", expr) else f"({expr})"


def _neg(expr: str) -> str:
    return expr[1:].strip() if re.fullmatch(r"!\s*[A-Za-z_]\w*", expr) else f"!{_wrap(expr)}"


def _join(parts: list[str]) -> str:
    parts = [p for p in parts if p]
    if len(parts) == 1:
        return parts[0]
    return " && ".join(_wrap(p) for p in parts)


def scan(source: bytes) -> list[Directive]:
    """`#if` branches (effective conditions) and `#define`/`#undef` lines."""
    out: list[Directive] = []
    # frame: [outer condition, prior branch conditions, current branch, start line]
    stack: list[list] = []

    def close(frame, end: int) -> None:
        outer, prior, branch, start = frame
        parts = [outer, *(_neg(p) for p in prior)] + ([branch] if branch else [])
        out.append(Directive("if", start, end, _join(parts)))

    lines = source.decode("utf-8-sig", errors="replace").splitlines()
    for n, text in enumerate(lines, 1):
        if "#" not in text:
            continue
        m = DIRECTIVE_RE.match(text)
        if not m:
            continue
        kw, expr = m.group(1), _strip_comment(m.group(2))
        if kw in ("define", "undef"):
            if IDENT_RE.fullmatch(expr):
                out.append(Directive(kw, n, n, expr))
        elif kw == "if":
            outer = stack[-1] if stack else None
            cond = _join([outer[0], *(_neg(p) for p in outer[1]), outer[2] or ""]) if outer else ""
            stack.append([cond, [], expr, n])
        elif stack and kw in ("elif", "else"):
            frame = stack[-1]
            close(frame, n)
            if frame[2]:
                frame[1].append(frame[2])
            frame[2], frame[3] = (expr if kw == "elif" else ""), n
        elif stack and kw == "endif":
            close(stack.pop(), n)
    while stack:  # unterminated: the branch runs to the end of the file
        close(stack.pop(), len(lines) + 1)
    return sorted(out, key=lambda d: (d.line, d.kind))


def region_at(directives: list[Directive], line: int) -> Directive | None:
    """Innermost `#if` branch containing `line` (its effective condition
    already includes the outer ones)."""
    inside = [d for d in directives if d.kind == "if" and d.line < line < d.end_line]
    return max(inside, key=lambda d: d.line) if inside else None


# --- expressions ---------------------------------------------------------------

def symbols(expr: str) -> list[str]:
    return [s for s in dict.fromkeys(IDENT_RE.findall(expr)) if s not in ("true", "false")]


def evaluate(expr: str, lookup) -> bool | None:
    """Three-valued C# preprocessor expression: `lookup(symbol)` returns
    True/False/None; a malformed expression is None."""
    tokens = TOKEN_RE.findall(expr)
    if "".join(tokens) != re.sub(r"\s+", "", expr):
        return None
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def take():
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def primary():
        tok = take() if peek() else None
        if tok == "(":
            val = disj()
            if take() != ")":
                raise ValueError
            return val
        if tok == "!":
            val = primary()
            return None if val is None else not val
        if tok in ("true", "false"):
            return tok == "true"
        if tok and IDENT_RE.fullmatch(tok):
            return lookup(tok)
        raise ValueError

    def equality():
        left = primary()
        while peek() in ("==", "!="):
            op, right = take(), primary()
            left = None if left is None or right is None else ((left == right) == (op == "=="))
        return left

    def conj():
        vals = [equality()]
        while peek() == "&&":
            take()
            vals.append(equality())
        return False if False in vals else (None if None in vals else True)

    def disj():
        vals = [conj()]
        while peek() == "||":
            take()
            vals.append(conj())
        return True if True in vals else (None if None in vals else False)

    try:
        val = disj()
        return val if pos == len(tokens) else None
    except (ValueError, IndexError, TypeError):
        return None


# --- Unity define symbols ------------------------------------------------------

# BuildTargetGroup: ProjectSettings keys (names since ~2020, numbers before).
GROUPS = {"Standalone": "Standalone", "1": "Standalone", "iPhone": "iOS", "4": "iOS",
          "Android": "Android", "7": "Android", "WebGL": "WebGL", "13": "WebGL",
          "tvOS": "tvOS", "25": "tvOS", "WSA": "WSA", "Metro": "WSA", "14": "WSA",
          "Switch": "Switch", "27": "Switch", "PS4": "PS4", "PS5": "PS5",
          "XboxOne": "XboxOne", "GameCoreXboxSeries": "GameCore",
          "VisionOS": "visionOS", "Server": "Server", "EmbeddedLinux": "EmbeddedLinux"}
# Symbols Unity defines for a target (the platform the code is built for).
PLATFORM_SYMBOLS = {
    "iOS": {"UNITY_IOS", "UNITY_IPHONE"}, "Android": {"UNITY_ANDROID"},
    "Standalone": {"UNITY_STANDALONE"}, "WebGL": {"UNITY_WEBGL"}, "tvOS": {"UNITY_TVOS"},
    "WSA": {"UNITY_WSA", "UNITY_WSA_10_0"}, "Switch": {"UNITY_SWITCH"},
    "PS4": {"UNITY_PS4"}, "PS5": {"UNITY_PS5"}, "XboxOne": {"UNITY_XBOXONE"},
    "GameCore": {"UNITY_GAMECORE"}, "visionOS": {"UNITY_VISIONOS"},
    "Server": {"UNITY_SERVER", "UNITY_STANDALONE"}, "EmbeddedLinux": {"UNITY_EMBEDDED_LINUX"},
}
ALL_PLATFORM = set().union(*PLATFORM_SYMBOLS.values()) | {
    "UNITY_STANDALONE_WIN", "UNITY_STANDALONE_OSX", "UNITY_STANDALONE_LINUX"}
EDITOR_SYMBOLS = {"UNITY_EDITOR"}
EDITOR_OS = {"UNITY_EDITOR_WIN", "UNITY_EDITOR_OSX", "UNITY_EDITOR_LINUX"}
# asmdef includePlatforms/excludePlatforms names -> target.
ASMDEF_PLATFORMS = {"Editor": "Editor", "iOS": "iOS", "Android": "Android", "WebGL": "WebGL",
                    "tvOS": "tvOS", "macOSStandalone": "Standalone",
                    "WindowsStandalone32": "Standalone", "WindowsStandalone64": "Standalone",
                    "LinuxStandalone64": "Standalone", "WSA": "WSA", "Switch": "Switch",
                    "PS4": "PS4", "PS5": "PS5", "XboxOne": "XboxOne", "GameCoreXboxSeries": "GameCore",
                    "VisionOS": "visionOS", "EmbeddedLinux": "EmbeddedLinux"}
# Unity/.NET switches whose value depends on the build: unknown, not "undefined".
BUILTIN_RE = re.compile(r"^(UNITY_|ENABLE_|PLATFORM_|NET_|NETSTANDARD|NETFX|CSHARP_|INCLUDE_|"
                        r"DEVELOPMENT_BUILD$|DEBUG$|TRACE$|UNITY$)")
VERSION_RE = re.compile(r"^UNITY_(\d+)(?:_(\d+))?(?:_(\d+))?(_OR_NEWER)?$")
RSP_DEFINE_RE = re.compile(r"(?:^|\s)[-/](?:define|d):(\S+)")


@dataclass
class DefineSite:
    source: str   # display path (relative to the indexed root, `../` outside it)
    line: int
    groups: list[str]
    symbols: list[str]


@dataclass
class ProjectDefines:
    """Everything that defines symbols, read from the project on disk."""
    groups: dict[str, set[str]] = field(default_factory=dict)   # target -> symbols
    sites: list[DefineSite] = field(default_factory=list)
    settings_path: str = ""
    rsp: set[str] = field(default_factory=set)
    rsp_path: str = ""
    version: tuple[int, ...] | None = None
    active: str | None = None          # Editor's build target (from the csproj)
    active_source: str = ""
    editor_defines: set[str] = field(default_factory=set)
    packages: dict[str, str] = field(default_factory=dict)

    @property
    def targets(self) -> list[str]:
        order = ["iOS", "Android", "Standalone", "WebGL"]
        found = [t for t in order if t in self.groups] + sorted(set(self.groups) - set(order))
        return ["Editor", *(found or ["iOS", "Android", "Standalone"])]

    def everywhere(self) -> set[str]:
        return set().union(*self.groups.values()) | self.rsp if self.groups else set(self.rsp)


def _display(path: Path, root: Path) -> str:
    """Path as printed: relative to the indexed root, `../` for the project
    folder above an indexed `Assets/`."""
    for base, prefix in ((root, ""), (root.parent, "../")):
        try:
            return prefix + path.relative_to(base).as_posix()
        except ValueError:
            continue
    return path.as_posix()


def project_dir(root: Path) -> Path:
    return root.parent if root.name == "Assets" and not (root / "ProjectSettings").is_dir() else root


def load_project(root: Path) -> ProjectDefines:
    proj = project_dir(root)
    assets = proj / "Assets"
    pd = ProjectDefines()
    settings = proj / "ProjectSettings" / "ProjectSettings.asset"
    try:
        lines = settings.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for n, text in enumerate(lines, 1):
        if text.strip() != "scriptingDefineSymbols:":
            continue
        pd.settings_path = _display(settings, root)
        indent = len(text) - len(text.lstrip())
        for k in range(n, len(lines)):
            m = re.match(r"^(\s+)([\w]+):\s*(.*)$", lines[k])
            if not m or len(m.group(1)) <= indent:
                break
            group = GROUPS.get(m.group(2), m.group(2))
            syms = {s.strip() for s in m.group(3).split(";") if s.strip()}
            pd.groups[group] = syms
            pd.sites.append(DefineSite(pd.settings_path, k + 1, [m.group(2)], sorted(syms)))
        break
    for name in ("csc.rsp", "mcs.rsp"):
        rsp = assets / name
        if rsp.is_file():
            pd.rsp |= set(read_rsp(rsp))
            pd.rsp_path = _display(rsp, root)
    ver = proj / "ProjectSettings" / "ProjectVersion.txt"
    if ver.is_file():
        m = re.search(r"m_EditorVersion:\s*(\d+)\.(\d+)\.(\d+)", ver.read_text(errors="replace"))
        if m:
            pd.version = tuple(int(x) for x in m.groups())
    csproj = proj / "Assembly-CSharp.csproj"
    if csproj.is_file():
        m = re.search(r"<DefineConstants>([^<]*)</DefineConstants>",
                      csproj.read_text(encoding="utf-8", errors="replace"))
        if m:
            pd.editor_defines = {s for s in m.group(1).split(";") if s}
            pd.active = next((t for t, syms in PLATFORM_SYMBOLS.items()
                              if t != "Server" and syms & pd.editor_defines), None)
            pd.active_source = _display(csproj, root)
    pd.packages = _packages(proj)
    return pd


def read_rsp(path: Path) -> list[str]:
    out = []
    for m in RSP_DEFINE_RE.finditer(path.read_text(encoding="utf-8", errors="replace")):
        out += [s for s in re.split(r"[;,]", m.group(1).strip('"')) if s]
    return out


def _packages(proj: Path) -> dict[str, str]:
    for name, key in (("packages-lock.json", "version"), ("manifest.json", None)):
        try:
            deps = json.loads((proj / "Packages" / name).read_text()).get("dependencies", {})
        except (OSError, ValueError, AttributeError):
            continue
        return {k: str(v.get(key, "") if isinstance(v, dict) else v) for k, v in deps.items()}
    return {}


def _ver(text: str) -> tuple[int, ...] | None:
    nums = re.findall(r"\d+", text.split("-")[0])
    return tuple(int(n) for n in nums) if nums else None


def version_matches(version: str, expr: str) -> bool | None:
    """Unity versionDefines expression: `1.2.3` means >= 1.2.3; `[a,b)` style
    intervals; `[a]` exact."""
    have = _ver(version)
    expr = expr.strip()
    if have is None or not expr:
        return None
    m = re.fullmatch(r"([\[(])\s*([^,\])]*)\s*(?:,\s*([^\])]*))?\s*([\])])", expr)
    if not m:
        want = _ver(expr)
        return None if want is None else have >= want
    lo, hi = _ver(m.group(2)) if m.group(2) else None, _ver(m.group(3)) if m.group(3) else None
    if m.group(3) is None and "," not in expr:
        return have == lo
    ok = True
    if lo is not None:
        ok &= have >= lo if m.group(1) == "[" else have > lo
    if hi is not None:
        ok &= have <= hi if m.group(4) == "]" else have < hi
    return ok


@dataclass
class Context:
    """What decides a symbol for one file: project, its assembly, its own
    `#define`s."""
    project: ProjectDefines
    asm_defines: set[str] = field(default_factory=set)  # versionDefines met + asmdef csc.rsp
    asm_maybe: set[str] = field(default_factory=set)    # versionDefines we cannot decide
    file_defines: dict[str, bool] = field(default_factory=dict)

    def known_somewhere(self, sym: str) -> bool:
        return (sym in self.project.everywhere() or sym in self.asm_defines
                or sym in self.asm_maybe or sym in self.file_defines)

    def value(self, sym: str, target: str) -> bool | None:
        pd = self.project
        if sym in self.file_defines:
            return self.file_defines[sym]
        if sym in self.asm_defines or sym in pd.rsp:
            return True
        if sym in self.asm_maybe:
            return None
        editor = target == "Editor"
        platform = pd.active if editor else target
        if sym in EDITOR_SYMBOLS:
            return editor
        if sym in EDITOR_OS:
            return None if editor else False
        if sym in ALL_PLATFORM:
            if platform is None:
                return None
            if sym in PLATFORM_SYMBOLS.get(platform, set()):
                return True
            return None if sym.startswith("UNITY_STANDALONE_") and platform == "Standalone" else False
        m = VERSION_RE.match(sym)
        if m and pd.version:
            want = tuple(int(x) for x in m.groups()[:3] if x)
            if m.group(4):
                return pd.version[:len(want)] >= want
            return pd.version[:len(want)] == want
        # Player Settings: a target without an entry has no project defines.
        if platform is not None:
            if sym in pd.groups.get(platform, ()):
                return True
        elif any(sym in syms for syms in pd.groups.values()):
            return None  # Editor, active target unknown
        if editor and sym in pd.editor_defines:
            return True
        return None if BUILTIN_RE.match(sym) else False

    def defined_nowhere(self, sym: str) -> bool:
        """A project symbol no settings, csc.rsp, versionDefines or #define
        mention — false on every target (unless a build script sets it)."""
        return (not BUILTIN_RE.match(sym) and sym not in ALL_PLATFORM | EDITOR_SYMBOLS | EDITOR_OS
                and not self.known_somewhere(sym) and sym not in self.project.editor_defines)


def context_for(project: ProjectDefines, asm, directives: list[Directive],
                root: Path) -> Context:
    ctx = Context(project)
    for d in directives:
        if d.kind in ("define", "undef"):
            ctx.file_defines[d.cond] = d.kind == "define"
    if asm is not None and not asm.predefined:
        for vd in asm.version_defines:
            name, define = vd.get("name", ""), vd.get("define", "")
            if not define:
                continue
            have = (".".join(map(str, project.version)) if name == "Unity" and project.version
                    else project.packages.get(name))
            ok = version_matches(have, vd.get("expression", "")) if have else False
            (ctx.asm_defines if ok else ctx.asm_maybe if ok is None else set()).add(define)
        rsp = root / asm.path.rpartition("/")[0] / "csc.rsp"
        if rsp.is_file():
            ctx.asm_defines |= set(read_rsp(rsp))
    return ctx


def compiled_on(asm, ctx: Context, target: str) -> bool | None:
    """Whether the assembly is built for `target` at all."""
    if asm is None:
        return False
    if asm.editor_only:
        return target == "Editor"
    names = lambda xs: {ASMDEF_PLATFORMS.get(p, p) for p in xs}  # noqa: E731
    if asm.include and target not in names(asm.include):
        return False
    if target in names(asm.exclude):
        return False
    vals = [evaluate(c, lambda s: ctx.value(s, target)) for c in asm.constraints]
    return False if False in vals else (None if None in vals else True)


def verdict(values: dict[str, bool | None]) -> str:
    """`iOS only`, `never`, `always`, or `Editor yes · iOS ?`."""
    on = [t for t, v in values.items() if v]
    unknown = [t for t, v in values.items() if v is None]
    if not unknown:
        if not on:
            return "never"
        if len(on) == len(values):
            return "everywhere"
        return " + ".join(on) + " only"
    return " · ".join(f"{t} {'yes' if v else '?' if v is None else 'no'}"
                      for t, v in values.items())
