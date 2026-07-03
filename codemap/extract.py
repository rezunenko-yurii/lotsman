"""Symbol extraction: tree-sitter definition queries with regex fallback,
plus language-agnostic lexical identifier counting."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from codemap.textutil import IDENT_RE, STOPWORDS

MAX_IDENTS_PER_FILE = 2000
SIGNATURE_MAX_LEN = 120


@dataclass
class Symbol:
    name: str
    kind: str  # function | class | method | type | const
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
        (enum_declaration name: (identifier) @def.type)
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
    except Exception:
        return None
    return parser, query


def _get_parser_and_query(lang: str):
    return _compile(lang, "def")


def _run_captures(query, root):
    """Handle tree-sitter API differences: QueryCursor (>=0.24) vs query.captures."""
    try:
        from tree_sitter import QueryCursor
        return QueryCursor(query).captures(root)
    except ImportError:
        return query.captures(root)


def _signature_from_node(node, source: bytes) -> str:
    first_line = source[node.start_byte:node.end_byte].split(b"\n", 1)[0]
    sig = first_line.decode("utf-8", errors="replace").strip()
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
            line = def_node.start_point[0] + 1
            if (name, line) in seen:
                continue
            seen.add((name, line))
            symbols.append(Symbol(
                name=name, kind=kind, line=line,
                end_line=def_node.end_point[0] + 1,
                signature=_signature_from_node(def_node, source)))
    symbols.sort(key=lambda s: s.line)
    return symbols


_DEF_NODE_TYPES = frozenset({
    "function_definition", "class_definition", "function_declaration",
    "generator_function_declaration", "class_declaration", "method_definition",
    "method_declaration", "interface_declaration", "type_alias_declaration",
    "enum_declaration", "variable_declarator", "type_declaration", "type_spec",
    "function_item", "struct_item", "enum_item", "trait_item", "mod_item",
    "struct_specifier", "enum_specifier", "class_specifier", "type_definition",
    "struct_declaration", "method", "class", "module",
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
    pq = _compile(lang, "ref")
    if pq is None:
        return extract_idents(source)
    parser, query = pq
    try:
        tree = parser.parse(source)
        captures = _run_captures(query, tree.root_node)
    except Exception:
        return extract_idents(source)
    counts: Counter = Counter()
    for nodes in captures.values():
        for node in nodes:
            name = source[node.start_byte:node.end_byte].decode(
                "utf-8", errors="replace")
            if len(name) < 3 or len(name) > 64 or name.lower() in STOPWORDS:
                continue
            counts[name] += 1
    if len(counts) > MAX_IDENTS_PER_FILE:
        counts = Counter(dict(counts.most_common(MAX_IDENTS_PER_FILE)))
    return counts


def extract_idents(source: bytes) -> Counter:
    """Language-agnostic identifier occurrence counts (reference signal)."""
    text = source.decode("utf-8", errors="replace")
    counts: Counter = Counter()
    for m in IDENT_RE.finditer(text):
        name = m.group(0)
        if name.lower() in STOPWORDS:
            continue
        counts[name] += 1
    if len(counts) > MAX_IDENTS_PER_FILE:
        counts = Counter(dict(counts.most_common(MAX_IDENTS_PER_FILE)))
    return counts
