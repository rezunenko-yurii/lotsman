"""Markdown docs as first-class index entries: heading sections, search terms
(Russian and English), binding documents and docs-for-code lookup.

Docs are navigated like code — `outline file.md` gives the heading tree with
line ranges, `search` finds sections by their text — so an agent reads one
section instead of a 600-line guide.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from functools import lru_cache

from lotsman.textutil import split_ident

SECTION_KIND = "section"


@dataclass
class Section:
    name: str        # heading text without `{#anchor}`
    level: int       # 1-6; 0 = file without headings
    line: int        # 1-based heading line
    end_line: int    # last line before the next heading of the same or higher level
    signature: str   # `## Heading {#anchor}` as written
    heading_terms: str  # normalized: heading chain (parents › self)
    body_terms: str     # normalized: own text up to the next heading of any level


# ---- parsing ---------------------------------------------------------------

_ATX_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_ANCHOR_RE = re.compile(r"\s*\{#[^}]*\}\s*$")


def parse_sections(source: bytes, filename: str = "") -> list[Section]:
    text = source.decode("utf-8", errors="replace")
    lines = text.splitlines()
    heads: list[tuple[int, int, str]] = []  # (line index, level, raw heading)
    skip = [False] * len(lines)  # front matter / code / comments carry no headings

    i = 0
    if lines and lines[0].strip() == "---":  # YAML front matter
        for j in range(1, len(lines)):
            if lines[j].strip() in ("---", "..."):
                for k in range(0, j + 1):
                    skip[k] = True
                i = j + 1
                break
    fence: str | None = None
    in_comment = False
    for idx in range(i, len(lines)):
        line = lines[idx]
        if fence:
            skip[idx] = True
            if line.lstrip().startswith(fence):
                fence = None
            continue
        if in_comment:
            skip[idx] = True
            if "-->" in line:
                in_comment = False
            continue
        m = _FENCE_RE.match(line)
        if m:
            fence = m.group(1)[0] * 3
            skip[idx] = True
            continue
        if "<!--" in line and "-->" not in line.split("<!--", 1)[1]:
            in_comment = True
            skip[idx] = True
            continue
        m = _ATX_RE.match(line)
        if m:
            heads.append((idx, len(m.group(1)), m.group(2)))
            continue
        m = _SETEXT_RE.match(line)
        if (m and idx > 0 and lines[idx - 1].strip() and not skip[idx - 1]
                and not (heads and heads[-1][0] == idx - 1)
                and "|" not in lines[idx - 1]):
            heads.append((idx - 1, 1 if m.group(1)[0] == "=" else 2,
                          lines[idx - 1].strip()))

    if not heads:
        body = "\n".join(l for l, s in zip(lines, skip) if not s)
        if not body.strip():
            return []
        name = posixpath.basename(filename) or "document"
        return [Section(name, 0, 1, len(lines), name, normalize(name),
                        normalize(body))]

    sections: list[Section] = []
    chain: list[tuple[int, str]] = []  # (level, name) of open ancestors
    for n, (idx, level, raw) in enumerate(heads):
        name = _ANCHOR_RE.sub("", raw).strip() or raw.strip()
        end = len(lines) - 1
        own_end = len(lines) - 1
        for idx2, level2, _ in heads[n + 1:]:
            own_end = min(own_end, idx2 - 1)
            if level2 <= level:
                end = idx2 - 1
                break
        while end > idx and not lines[end].strip():
            end -= 1
        own = [lines[k] for k in range(idx + 1, own_end + 1)
               if not skip[k] and not _SETEXT_RE.match(lines[k])]
        while chain and chain[-1][0] >= level:
            chain.pop()
        chain.append((level, name))
        heading_chain = " › ".join(c[1] for c in chain)
        own_text = "\n".join(own).strip()
        sections.append(Section(
            name=name, level=level, line=idx + 1, end_line=end + 1,
            signature="#" * level + " " + raw.strip(),
            heading_terms=normalize(heading_chain),
            body_terms=normalize(own_text)))
    return sections


# ---- normalization: one function for indexed text and queries ---------------

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_CYRILLIC_RE = re.compile(r"[а-яё]")
RU_STOPWORDS = frozenset("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы
по только ее её мне было вот от меня еще ещё нет о из ему теперь когда даже ну
ли если уже или ни быть был него до вас нибудь опять уж вам ведь там потом
себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам
чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому этого
какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда
зачем всех никогда можно при наконец два об другой хоть после над больше тот
через эти нас про всего них какая много разве три эту моя впрочем хорошо свою
этой перед иногда лучше чуть том нельзя такой им более всегда конечно всю
между это эта это
""".split())
EN_STOPWORDS = frozenset("""
a an and are as at be by for from has have in is it its of on or that the
this to was were will with you your not can do does if into than then there
these they which who why how what when where
""".split())


def is_cyrillic(term: str) -> bool:
    return bool(_CYRILLIC_RE.search(term))


def normalize(text: str) -> str:
    """Space-joined search terms: lowercase words, `ё`→`е`, Russian words
    stemmed, identifiers also split into subwords (`InjectFields` →
    `injectfields inject fields`)."""
    return " ".join(terms(text))


def terms(text: str) -> list[str]:
    out: list[str] = []
    for word in _WORD_RE.findall(text):
        out.extend(_word_terms(word))
    return out


@lru_cache(maxsize=262144)  # per word: the vocabulary is far smaller than the text
def _word_terms(word: str) -> tuple[str, ...]:
    low = word.lower().replace("ё", "е")
    if _CYRILLIC_RE.search(low):
        if low in RU_STOPWORDS or len(low) < 2:
            return ()
        return (stem_ru(low),)
    if low in EN_STOPWORDS or len(low) < 2 or low.isdigit():
        return ()
    subs = split_ident(word)
    if len(subs) > 1:
        return (low, *(s for s in subs if len(s) > 1 and s not in EN_STOPWORDS))
    return (low,)


# ---- Russian stemmer (Snowball algorithm) -----------------------------------

_VOWELS = "аеиоуыэюя"
_PERF_GERUND_1 = ("вшись", "вши", "в")          # after а/я
_PERF_GERUND_2 = ("ившись", "ывшись", "ивши", "ывши", "ив", "ыв")
_REFLEXIVE = ("ся", "сь")
_ADJECTIVE = ("ими", "ыми", "его", "ого", "ему", "ому", "ее", "ие", "ые", "ое",
              "ей", "ий", "ый", "ой", "ем", "им", "ым", "ом", "их", "ых", "ую",
              "юю", "ая", "яя", "ою", "ею")
_PARTICIPLE_1 = ("ем", "нн", "вш", "ющ", "щ")     # after а/я
_PARTICIPLE_2 = ("ивш", "ывш", "ующ")
_VERB_1 = ("ете", "йте", "ешь", "нно", "ла", "на", "ли", "ем", "ло", "но", "ет",
           "ют", "ны", "ть", "й", "л", "н")      # after а/я
_VERB_2 = ("ейте", "уйте", "ила", "ыла", "ена", "ите", "или", "ыли", "ило", "ыло",
           "ено", "ует", "уют", "ены", "ить", "ыть", "ишь", "ей", "уй", "ил", "ыл",
           "им", "ым", "ен", "ят", "ит", "ыт", "ую", "ю")
_NOUN = ("иями", "ями", "ами", "ией", "иям", "ием", "иях", "ев", "ов", "ие", "ье",
         "еи", "ии", "ей", "ой", "ий", "ям", "ем", "ам", "ом", "ах", "ях", "ию",
         "ью", "ия", "ья", "а", "е", "и", "й", "о", "у", "ы", "ь", "ю", "я")
_SUPERLATIVE = ("ейше", "ейш")
_DERIVATIONAL = ("ость", "ост")


def _regions(word: str) -> tuple[int, int]:
    """(RV, R2) start offsets per the Snowball Russian definition."""
    rv = len(word)
    for i, ch in enumerate(word):
        if ch in _VOWELS:
            rv = i + 1
            break

    def r_after(start: int) -> int:
        for i in range(start + 1, len(word)):
            if word[i] not in _VOWELS and word[i - 1] in _VOWELS:
                return i + 1
        return len(word)

    r1 = r_after(0)
    return rv, r_after(r1) if r1 < len(word) else len(word)


def _strip(word: str, rv: int, group1=(), group2=()) -> str | None:
    """Remove the longest matching ending inside RV; group1 endings must follow
    `а` or `я` (which stays). None when nothing matched."""
    best = None
    for end in group2:
        if word.endswith(end) and len(word) - len(end) >= rv:
            if best is None or len(end) > len(best[0]):
                best = (end, False)
    for end in group1:
        cut = len(word) - len(end)
        if (word.endswith(end) and cut - 1 >= rv and word[cut - 1] in "ая"):
            if best is None or len(end) > len(best[0]):
                best = (end, True)
    if best is None:
        return None
    return word[:len(word) - len(best[0])]


@lru_cache(maxsize=262144)  # vocabulary is far smaller than the token stream
def stem_ru(word: str) -> str:
    rv, r2 = _regions(word)
    if rv >= len(word):
        return word
    w = word
    # Step 1
    res = _strip(w, rv, _PERF_GERUND_1, _PERF_GERUND_2)
    if res is not None:
        w = res
    else:
        res = _strip(w, rv, (), _REFLEXIVE)
        if res is not None:
            w = res
        res = _strip(w, rv, (), _ADJECTIVE)
        if res is not None:
            w = res
            part = _strip(w, rv, _PARTICIPLE_1, _PARTICIPLE_2)
            if part is not None:
                w = part
        else:
            res = _strip(w, rv, _VERB_1, _VERB_2)
            if res is not None:
                w = res
            else:
                res = _strip(w, rv, (), _NOUN)
                if res is not None:
                    w = res
    # Step 2
    if w.endswith("и") and len(w) - 1 >= rv:
        w = w[:-1]
    # Step 3
    for end in _DERIVATIONAL:
        if w.endswith(end) and len(w) - len(end) >= r2:
            w = w[:-len(end)]
            break
    # Step 4
    if w.endswith("нн") and len(w) - 2 >= rv:
        w = w[:-1]
    else:
        for end in _SUPERLATIVE:
            if w.endswith(end) and len(w) - len(end) >= rv:
                w = w[:-len(end)]
                if w.endswith("нн"):
                    w = w[:-1]
                break
        else:
            if w.endswith("ь") and len(w) - 1 >= rv:
                w = w[:-1]
    return w


# ---- binding docs and docs-for-code -------------------------------------------

_BINDING_LINE_RE = re.compile(
    r"binding|must be read|read (?:it )?in full|обязател|читать целиком",
    re.IGNORECASE)
_MD_PATH_RE = re.compile(r"[\w./~\-]+\.md\b")


def binding_docs(agents_path: str, text: str, known: set[str]) -> set[str]:
    """Docs an AGENTS.md declares binding (lines saying `binding` /
    `обязателен` next to a `.md` path), resolved against the indexed files."""
    base = posixpath.dirname(agents_path)
    found: set[str] = set()
    for line in text.splitlines():
        if not _BINDING_LINE_RE.search(line):
            continue
        for ref in _MD_PATH_RE.findall(line):
            ref = ref.lstrip("./")
            for cand in (posixpath.normpath(posixpath.join(base, ref)), ref):
                if cand in known and cand != agents_path:
                    found.add(cand)
                    break
    return found


_DOC_DIRS = ("", "Docs", "Documentation", "Editor/Documentation", "docs")
MAX_DOCS_LISTED = 6


def docs_for(path: str, doc_paths: set[str]) -> str | None:
    """One line naming the nearest module docs for a code file: the first
    ancestor folder with docs in itself or a docs subfolder; the most useful
    documents first (AGENTS, README, overview, setup, reference, ...)."""
    by_dir: dict[str, list[str]] = {}
    for doc in doc_paths:
        by_dir.setdefault(posixpath.dirname(doc), []).append(posixpath.basename(doc))
    parts = path.split("/")[:-1]
    for depth in range(len(parts), -1, -1):
        base = "/".join(parts[:depth])
        found = [(sub, name) for gi, sub in enumerate(_DOC_DIRS)
                 for name in by_dir.get(posixpath.join(base, sub) if sub else base, ())]
        if not found:
            continue
        if not base:
            return None  # repo-root docs: agents read those first anyway
        found.sort(key=lambda sn: (_doc_order(sn[1]), _DOC_DIRS.index(sn[0])))
        shown, hidden = found[:MAX_DOCS_LISTED], len(found) - MAX_DOCS_LISTED
        groups: dict[str, list[str]] = {}
        for sub, name in shown:
            groups.setdefault(sub, []).append(name)
        rendered = []
        for sub in _DOC_DIRS:
            names = groups.get(sub)
            if not names:
                continue
            prefix = f"{sub}/" if sub else ""
            rendered.append(prefix + ("{" + ", ".join(names) + "}" if len(names) > 1
                                      else names[0]))
        where = base + "/" if base else ""
        more = f" (+{hidden} more)" if hidden > 0 else ""
        return f"docs: {where}" + " · ".join(rendered) + more
    return None


_DOC_PRIORITY = ("AGENTS.md", "README.md", "overview.md", "setup.md",
                 "reference.md", "MAP.md", "DECISIONS.md", "tasks.md")


def _doc_order(name: str) -> tuple[int, str]:
    return (_DOC_PRIORITY.index(name) if name in _DOC_PRIORITY else len(_DOC_PRIORITY), name)
