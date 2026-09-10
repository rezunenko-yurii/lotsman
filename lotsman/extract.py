"""Symbol extraction: tree-sitter definition queries with regex fallback,
plus language-agnostic lexical identifier counting."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from lotsman.textutil import IDENT_RE, STOPWORDS

MAX_IDENTS_PER_FILE = 2000
SIGNATURE_MAX_LEN = 120
_WS_RUN = re.compile(r"\s{2,}")


@dataclass
class Symbol:
    name: str
    kind: str  # function | class | method | type | const | field | event
    line: int  # 1-based
    end_line: int
    signature: str


# Tree-sitter queries capturing definition names. Capture name encodes kind:
# @def.<kind> on the *name* node; signature is taken from the named node's parent
# definition node (first line).
DEF_QUERIES: dict[str, str] = {
    "python": """
        (function_definition name: (identifier) @def.function)
        (class_definition name: (identifier) @def.class)
    """,
    "javascript": """
        (function_declaration name: (identifier) @def.function)
        (generator_function_declaration name: (identifier) @def.function)
        (class_declaration name: (identifier) @def.class)
        (method_definition name: (property_identifier) @def.method)
        (variable_declarator
            name: (identifier) @def.function
            value: [(arrow_function) (function_expression)])
    """,
    "typescript": """
        (function_declaration name: (identifier) @def.function)
        (class_declaration name: (type_identifier) @def.class)
        (method_definition name: (property_identifier) @def.method)
        (interface_declaration name: (type_identifier) @def.type)
        (type_alias_declaration name: (type_identifier) @def.type)
        (enum_declaration name: (identifier) @def.type)
        (variable_declarator
            name: (identifier) @def.function
            value: [(arrow_function) (function_expression)])
    """,
    "go": """
        (function_declaration name: (identifier) @def.function)
        (method_declaration name: (field_identifier) @def.method)
        (type_declaration (type_spec name: (type_identifier) @def.type))
    """,
    "rust": """
        (function_item name: (identifier) @def.function)
        (struct_item name: (type_identifier) @def.type)
        (enum_item name: (type_identifier) @def.type)
        (trait_item name: (type_identifier) @def.type)
        (mod_item name: (identifier) @def.type)
    """,
    "java": """
        (class_declaration name: (identifier) @def.class)
        (interface_declaration name: (identifier) @def.class)
        (enum_declaration name: (identifier) @def.class)
        (method_declaration name: (identifier) @def.method)
    """,
    "c": """
        (function_definition
            declarator: (function_declarator declarator: (identifier) @def.function))
        (struct_specifier name: (type_identifier) @def.type)
        (enum_specifier name: (type_identifier) @def.type)
        (type_definition declarator: (type_identifier) @def.type)
    """,
    "cpp": """
        (function_definition
            declarator: (function_declarator declarator: (identifier) @def.function))
        (function_definition
            declarator: (function_declarator
                declarator: (qualified_identifier name: (identifier) @def.method)))
        (class_specifier name: (type_identifier) @def.class)
        (struct_specifier name: (type_identifier) @def.type)
        (enum_specifier name: (type_identifier) @def.type)
    """,
    "ruby": """
        (method name: (identifier) @def.method)
        (class name: (constant) @def.class)
        (module name: (constant) @def.class)
    """,
    "csharp": """
        (class_declaration name: (identifier) @def.class)
        (interface_declaration name: (identifier) @def.class)
        (struct_declaration name: (identifier) @def.type)
        (method_declaration name: (identifier) @def.method)
        (property_declaration name: (identifier) @def.method)
        (constructor_declaration name: (identifier) @def.method)
        (enum_declaration name: (identifier) @def.type)
        (delegate_declaration name: (identifier) @def.type)
        (record_declaration name: (identifier) @def.class)
        (field_declaration (variable_declaration
            (variable_declarator name: (identifier) @def.field)))
        (event_field_declaration (variable_declaration
            (variable_declarator name: (identifier) @def.event)))
        (event_declaration name: (identifier) @def.event)
        (enum_member_declaration name: (identifier) @def.field)
    """,
    "php": """
        (function_definition name: (name) @def.function)
        (class_declaration name: (name) @def.class)
        (method_declaration name: (name) @def.method)
        (interface_declaration name: (name) @def.type)
    """,
}
DEF_QUERIES["tsx"] = DEF_QUERIES["typescript"]

# Reference queries: capture identifiers at *use* sites (calls, instantiations,
# type usages, decorators). Far more precise than lexical counting — a parameter
# named `request` is not a reference to a `request()` method.
REF_QUERIES: dict[str, str] = {
    "python": """
        (call function: (identifier) @ref)
        (call function: (attribute attribute: (identifier) @ref))
        (decorator (identifier) @ref)
        (decorator (attribute attribute: (identifier) @ref))
        (import_from_statement name: (dotted_name (identifier) @ref))
        (class_definition superclasses: (argument_list (identifier) @ref))
        (class_definition superclasses: (argument_list (attribute attribute: (identifier) @ref)))
    """,
    "javascript": """
        (call_expression function: (identifier) @ref)
        (call_expression function: (member_expression property: (property_identifier) @ref))
        (new_expression constructor: (identifier) @ref)
        (class_heritage (identifier) @ref)
        (import_specifier name: (identifier) @ref)
    """,
    "typescript": """
        (call_expression function: (identifier) @ref)
        (call_expression function: (member_expression property: (property_identifier) @ref))
        (new_expression constructor: (identifier) @ref)
        (type_identifier) @ref
        (import_specifier name: (identifier) @ref)
    """,
    "go": """
        (call_expression function: (identifier) @ref)
        (call_expression function: (selector_expression field: (field_identifier) @ref))
        (type_identifier) @ref
    """,
    "rust": """
        (call_expression function: (identifier) @ref)
        (call_expression function: (field_expression field: (field_identifier) @ref))
        (call_expression function: (scoped_identifier name: (identifier) @ref))
        (macro_invocation macro: (identifier) @ref)
        (type_identifier) @ref
    """,
    "java": """
        (method_invocation name: (identifier) @ref)
        (object_creation_expression type: (type_identifier) @ref)
        (type_identifier) @ref
    """,
    "c": """
        (call_expression function: (identifier) @ref)
        (type_identifier) @ref
    """,
    "cpp": """
        (call_expression function: (identifier) @ref)
        (call_expression function: (field_expression field: (field_identifier) @ref))
        (call_expression function: (qualified_identifier name: (identifier) @ref))
        (type_identifier) @ref
    """,
    "ruby": """
        (call method: (identifier) @ref)
        (constant) @ref
    """,
    # C# naming conventions carry what a name-based index cannot resolve:
    # PascalCase identifiers in code are types, static receivers (`ExLog.Info`),
    # enum types and members, constants, properties and method groups, while
    # camelCase ones are locals, parameters and private fields. So every
    # PascalCase identifier counts — generic arguments, `typeof`, `is`/`as`,
    # casts, `nameof`, property/event types, attribute arguments — minus the
    # declaration names captured as @decl. Members count in any casing.
    "csharp": """
        ((identifier) @ref.pascal (#match? @ref.pascal "^[A-Z]"))
        (invocation_expression function: (identifier) @ref)
        (member_access_expression name: (identifier) @ref)
        (member_binding_expression name: (identifier) @ref)
        (generic_name (identifier) @ref)
        (attribute name: (identifier) @ref.attr)
        (attribute name: (qualified_name name: (identifier) @ref.attr))

        (class_declaration name: (identifier) @decl)
        (struct_declaration name: (identifier) @decl)
        (interface_declaration name: (identifier) @decl)
        (enum_declaration name: (identifier) @decl)
        (record_declaration name: (identifier) @decl)
        (delegate_declaration name: (identifier) @decl)
        (method_declaration name: (identifier) @decl)
        (constructor_declaration name: (identifier) @decl)
        (destructor_declaration name: (identifier) @decl)
        (property_declaration name: (identifier) @decl)
        (event_declaration name: (identifier) @decl)
        (local_function_statement name: (identifier) @decl)
        (enum_member_declaration name: (identifier) @decl)
        (variable_declarator name: (identifier) @decl)
        (parameter name: (identifier) @decl)
        (type_parameter name: (identifier) @decl)
        (declaration_pattern name: (identifier) @decl)
        (declaration_expression name: (identifier) @decl)
        (catch_declaration name: (identifier) @decl)
        (foreach_statement left: (identifier) @decl)
        (tuple_element name: (identifier) @decl)
        (tuple_pattern name: (identifier) @decl)
        (parenthesized_variable_designation (identifier) @decl)
        (argument name: (identifier) @decl)
        (anonymous_object_creation_expression (identifier) @decl)
        (labeled_statement (identifier) @decl)
        (from_clause name: (identifier) @decl)
        (namespace_declaration name: (_) @decl.span)
        (file_scoped_namespace_declaration name: (_) @decl.span)
        (using_directive name: (identifier) @decl)
        (using_directive) @decl.using
    """,
}
REF_QUERIES["tsx"] = REF_QUERIES["typescript"]

# Regex fallback for languages without a working grammar/query.
# NB: `[ \t]*` (not `\s*`) after `^` so matches never bleed across newlines.
FALLBACK_PATTERNS = [
    (re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+([A-Za-z_]\w*)", re.M), "function"),
    (re.compile(r"^[ \t]*(?:export[ \t]+)?(?:default[ \t]+)?function[ \t]*\*?[ \t]*([A-Za-z_]\w*)", re.M), "function"),
    (re.compile(r"^[ \t]*(?:export[ \t]+)?(?:abstract[ \t]+)?class[ \t]+([A-Za-z_]\w*)", re.M), "class"),
    (re.compile(r"^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?fn[ \t]+([A-Za-z_]\w*)", re.M), "function"),
    (re.compile(r"^[ \t]*func[ \t]+(?:\([^)]*\)[ \t]*)?([A-Za-z_]\w*)", re.M), "function"),
    (re.compile(r"^[ \t]*(?:public|private|protected)[ \t]+(?:static[ \t]+)?[\w<>\[\]]+[ \t]+([A-Za-z_]\w*)[ \t]*\(", re.M), "method"),
]


# (lang, "def"|"ref") -> why tree-sitter could not be loaded for it.
_COMPILE_ERRORS: dict[tuple[str, str], str] = {}


@lru_cache(maxsize=None)
def _compile(lang: str, which: str):
    """Compile (parser, query) for a language once; None if unavailable."""
    query_src = (DEF_QUERIES if which == "def" else REF_QUERIES).get(lang)
    if not query_src:
        return None
    try:
        import tree_sitter
        from tree_sitter_language_pack import get_language
        language = get_language(lang)
        # Use py-tree-sitter's Parser with the pack's Language: the pack's own
        # get_parser() may return an incompatible pyo3 object.
        parser = tree_sitter.Parser(language)
        query = tree_sitter.Query(language, query_src)
    except Exception as exc:
        _COMPILE_ERRORS[(lang, which)] = f"{type(exc).__name__}: {exc}"[:200]
        return None
    return parser, query


def grammar_failures(langs) -> dict[str, str]:
    """Languages that have tree-sitter queries but cannot load them, with the
    reason — they silently fall back to regex definitions and lexical refs
    (comments and strings count), so callers must say so."""
    failures: dict[str, str] = {}
    for lang in sorted(set(langs)):
        for which, table in (("def", DEF_QUERIES), ("ref", REF_QUERIES)):
            if lang in table and _compile(lang, which) is None:
                failures[lang] = _COMPILE_ERRORS.get((lang, which), "unavailable")
                break
    return failures


def grammar_fingerprint(langs) -> str:
    """Cheap stand-in for `grammar_failures` on the no-op path (compiling the
    queries costs ~60 ms per process): changes when Python, the tree-sitter
    packages, the queries or the indexed languages change."""
    import hashlib
    import importlib.util
    import os
    import sys
    h = hashlib.sha256(sys.version.encode())
    for module in ("tree_sitter", "tree_sitter_language_pack"):
        try:
            spec = importlib.util.find_spec(module)
        except (ImportError, ValueError):
            spec = None
        origin = (spec.origin or "") if spec else "missing"
        h.update(origin.encode())
        try:
            h.update(str(os.stat(origin).st_mtime_ns).encode())
        except OSError:
            pass
    for lang in sorted(set(langs)):
        h.update(lang.encode())
        h.update(DEF_QUERIES.get(lang, "").encode())
        h.update(REF_QUERIES.get(lang, "").encode())
    return h.hexdigest()[:16]


def degraded_warning(failures: dict[str, str]) -> str:
    langs = ", ".join(failures)
    reason = next(iter(failures.values()), "")
    return (f"warning: tree-sitter unavailable for {langs} ({reason}) — "
            "regex fallback: outline/defs approximate, refs lexical "
            "(comments and strings count). Run `lotsman doctor`.")


def _get_parser_and_query(lang: str):
    return _compile(lang, "def")


def _run_captures(query, root):
    """Handle tree-sitter API differences: QueryCursor (>=0.24) vs query.captures."""
    try:
        from tree_sitter import QueryCursor
        return QueryCursor(query).captures(root)
    except ImportError:
        return query.captures(root)


# Leading children that precede the declaration proper: `[Test]`, `@Override`,
# `@Component(...)`, doc comments. A signature made of them says nothing.
_PREAMBLE_NODES = frozenset({
    "attribute_list", "decorator", "annotation", "marker_annotation", "comment",
})


def _declaration_start(node):
    """First child that is part of the declaration itself (skips attributes,
    decorators, annotations — also inside Java's `modifiers`)."""
    for child in node.children:
        if child.type in _PREAMBLE_NODES:
            continue
        if child.type == "modifiers":  # Java: annotations live in modifiers
            inner = next((c for c in child.children
                          if c.type not in _PREAMBLE_NODES), None)
            if inner is None:
                continue
            return inner
        return child
    return node


def _signature(node, decl, source: bytes) -> str:
    """First line of the declaration `decl` within `node`; attributes on that
    same line stay (`[SerializeField] private int hp;` — worth seeing)."""
    row = decl.start_point[0]
    start = decl.start_byte
    for child in node.children:
        if child.start_byte >= decl.start_byte:
            break
        if child.start_point[0] == row:
            start = child.start_byte
            break
    first_line = source[start:node.end_byte].split(b"\n", 1)[0]
    # Column alignment (`WindowData     data      { get; }`) is paid for in
    # every outline/map/search line; one space carries the same meaning.
    sig = _WS_RUN.sub(" ", first_line.decode("utf-8", errors="replace").strip())
    if len(sig) > SIGNATURE_MAX_LEN:
        sig = sig[:SIGNATURE_MAX_LEN - 1] + "…"
    return sig


def extract_symbols(lang: str, source: bytes) -> list[Symbol]:
    """Extract definitions; tree-sitter if possible, regex fallback otherwise."""
    pq = _get_parser_and_query(lang)
    if pq is None:
        return _extract_symbols_fallback(source)
    parser, query = pq
    try:
        tree = parser.parse(source)
        captures = _run_captures(query, tree.root_node)
    except Exception:
        return _extract_symbols_fallback(source)

    symbols: list[Symbol] = []
    seen: set[tuple[str, int]] = set()
    for capture_name, nodes in captures.items():
        kind = capture_name.split(".", 1)[1] if "." in capture_name else "function"
        for name_node in nodes:
            name = source[name_node.start_byte:name_node.end_byte].decode(
                "utf-8", errors="replace")
            # The definition node is the nearest ancestor spanning multiple lines
            # or having a body — walk up from the name node.
            def_node = name_node.parent or name_node
            while (def_node.parent is not None
                   and def_node.start_point[0] == name_node.start_point[0]
                   and def_node.type not in _DEF_NODE_TYPES):
                def_node = def_node.parent
            # C# `[SerializeField] private int a, b;`: the member declaration,
            # not the bare declarator, carries type and modifiers.
            if (def_node.type == "variable_declarator" and def_node.parent
                    and def_node.parent.parent
                    and def_node.parent.parent.type in _MEMBER_DECL_TYPES):
                def_node = def_node.parent.parent
            # Line and signature point at the declaration, not at `[Test]`
            # above it — like Python, where decorators sit outside the def.
            decl = _declaration_start(def_node)
            line = decl.start_point[0] + 1
            if (name, line) in seen:
                continue
            seen.add((name, line))
            symbols.append(Symbol(
                name=name, kind=kind, line=line,
                end_line=def_node.end_point[0] + 1,
                signature=_signature(def_node, decl, source)))
    symbols.sort(key=lambda s: s.line)
    return symbols


_MEMBER_DECL_TYPES = frozenset({"field_declaration", "event_field_declaration"})

_DEF_NODE_TYPES = frozenset({
    "function_definition", "class_definition", "function_declaration",
    "generator_function_declaration", "class_declaration", "method_definition",
    "method_declaration", "interface_declaration", "type_alias_declaration",
    "enum_declaration", "variable_declarator", "type_declaration", "type_spec",
    "function_item", "struct_item", "enum_item", "trait_item", "mod_item",
    "struct_specifier", "enum_specifier", "class_specifier", "type_definition",
    "struct_declaration", "property_declaration", "constructor_declaration",
    "event_declaration", "delegate_declaration", "record_declaration",
    "enum_member_declaration",
    "method", "class", "module",
})


def _extract_symbols_fallback(source: bytes) -> list[Symbol]:
    text = source.decode("utf-8", errors="replace")
    lines = text.splitlines()
    symbols: list[Symbol] = []
    seen: set[tuple[str, int]] = set()
    for pattern, kind in FALLBACK_PATTERNS:
        for m in pattern.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            name = m.group(1)
            if (name, line) in seen:
                continue
            seen.add((name, line))
            sig = lines[line - 1].strip() if line <= len(lines) else name
            if len(sig) > SIGNATURE_MAX_LEN:
                sig = sig[:SIGNATURE_MAX_LEN - 1] + "…"
            symbols.append(Symbol(name, kind, line, line, sig))
    symbols.sort(key=lambda s: s.line)
    return symbols


def extract_refs(lang: str, source: bytes) -> Counter:
    """Reference counts at use sites via tree-sitter; lexical fallback when the
    language has no reference query."""
    return extract_refs_with_lines(lang, source)[0]


# Ref capture kinds, in the order they claim a node (one node counts once):
#   @ref.attr   attribute name; also counts `<name>Attribute` — `[Test]` uses
#               the `TestAttribute` class;
#   @ref        use site as is;
#   @ref.pascal kept only when PascalCase (belt and braces for #match?).
# Exclusions: @decl (a declaration name), @decl.span (everything inside),
# @decl.using (namespace imports; `using static` and aliases name real types).
_REF_CAPTURE_ORDER = ("ref.attr", "ref", "ref.pascal")


def _excluded_ranges(captures, source: bytes) -> tuple[set[int], list[tuple[int, int]]]:
    starts: set[int] = set()
    spans: list[tuple[int, int]] = []
    for node in captures.get("decl", ()):
        starts.add(node.start_byte)
    for node in captures.get("decl.span", ()):
        spans.append((node.start_byte, node.end_byte))
    for node in captures.get("decl.using", ()):
        text = source[node.start_byte:node.end_byte]
        if b"static" not in text and b"=" not in text:
            spans.append((node.start_byte, node.end_byte))
    return starts, spans


def extract_refs_with_lines(lang: str, source: bytes
                            ) -> tuple[Counter, dict[str, list[int]]]:
    """(name -> use-site count, name -> sorted distinct 1-based lines)."""
    pq = _compile(lang, "ref")
    if pq is None:
        return extract_idents_with_lines(source)
    parser, query = pq
    try:
        tree = parser.parse(source)
        captures = _run_captures(query, tree.root_node)
    except Exception:
        return extract_idents_with_lines(source)
    skip_starts, skip_spans = _excluded_ranges(captures, source)
    order = [c for c in _REF_CAPTURE_ORDER if c in captures]
    order += [c for c in captures if c not in order and not c.startswith("decl")]
    counts: Counter = Counter()
    lines: dict[str, set[int]] = {}
    seen: set[int] = set()
    for capture_name in order:
        for node in captures[capture_name]:
            start = node.start_byte
            if start in seen or start in skip_starts:
                continue
            seen.add(start)
            if skip_spans and any(a <= start < b for a, b in skip_spans):
                continue
            name = source[start:node.end_byte].decode("utf-8", errors="replace")
            if capture_name == "ref.pascal" and not name[:1].isupper():
                continue
            # Case-sensitive: an AST capture is never a keyword, and a class
            # named `Window` or a method `Add` must stay findable by `refs`
            # (graph.build_edges drops such generic names from ranking).
            if len(name) < 3 or len(name) > 64 or name in STOPWORDS:
                continue
            line = node.start_point[0] + 1
            names = [name]
            if capture_name == "ref.attr" and not name.endswith("Attribute"):
                names.append(name + "Attribute")
            for n in names:
                counts[n] += 1
                lines.setdefault(n, set()).add(line)
    return _cap(counts, lines)


def _cap(counts: Counter, lines: dict[str, set[int]]
         ) -> tuple[Counter, dict[str, list[int]]]:
    if len(counts) > MAX_IDENTS_PER_FILE:
        counts = Counter(dict(counts.most_common(MAX_IDENTS_PER_FILE)))
    return counts, {name: sorted(lines[name]) for name in counts}


def extract_idents(source: bytes) -> Counter:
    """Language-agnostic identifier occurrence counts (reference signal)."""
    return extract_idents_with_lines(source)[0]


def extract_idents_with_lines(source: bytes
                              ) -> tuple[Counter, dict[str, list[int]]]:
    text = source.decode("utf-8", errors="replace")
    counts: Counter = Counter()
    lines: dict[str, set[int]] = {}
    line, pos = 1, 0
    for m in IDENT_RE.finditer(text):
        name = m.group(0)
        if name.lower() in STOPWORDS:
            continue
        line += text.count("\n", pos, m.start())
        pos = m.start()
        counts[name] += 1
        lines.setdefault(name, set()).add(line)
    return _cap(counts, lines)
