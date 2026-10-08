"""D4 code_features — a numeric style fingerprint per C++ code file, keyed by code_sha256.

Three families, from two views (FEATURES lists every column with its definition):
  layout   from the ORIGINAL bytes (canonical `code`): indentation, braces, spacing, blank lines, line length
  lexical  from the original: comments, macros, includes, I/O idioms, naming, literals
  structure from D3 `expanded` + its tree-sitter parse: functions, loops, nesting, token-type mix
Ratios are per line or per token so files of different length are comparable. A feature that cannot be
computed (e.g. no identifiers) is NaN, never 0 — 0 would be a claim.

usage: python src/derived/code_features.py
"""
import math, re, shutil, sys
from collections import Counter
from multiprocessing import Pool
from pathlib import Path
import pyarrow as pa
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.derived.common import Build  # noqa: E402
from src.canonical.db import fetch_code  # noqa: E402
from src.derived.code_views import parser  # noqa: E402

FEATURES = {
    # layout (original bytes)
    "n_lines": "physical lines", "blank_line_ratio": "blank lines / lines", "mean_line_len": "mean chars per non-blank line",
    "max_line_len": "longest line", "long_line_ratio": "non-blank lines > 100 chars / non-blank lines",
    "tab_indent_ratio": "indented lines starting with a tab / indented lines", "indent_unit": "most common space-indent step (2, 4, 8…); NaN if none",
    "brace_own_line_ratio": "'{' alone on its line / lines containing '{'", "crlf": "file uses CRLF line endings (0/1)",
    "space_after_keyword_ratio": "'if (' 'for (' 'while (' / all if|for|while followed by '('",
    "space_around_op_ratio": "binary = == < > + - * / with spaces both sides / all occurrences",
    "trailing_ws_ratio": "lines with trailing whitespace / lines", "one_liner_ratio": "lines with two or more ';' / non-blank lines",
    # lexical (original bytes)
    "comment_line_ratio": "lines containing // or /* / non-blank lines", "block_comment": "uses /* */ (0/1)",
    "n_defines": "#define directives", "n_func_macros": "function-like #define", "define_int_ll": "#define int long long (0/1)",
    "bits_stdcpp": "#include <bits/stdc++.h> (0/1)", "n_includes": "#include directives", "using_namespace_std": "(0/1)",
    "n_typedef_using": "typedef + 'using X =' aliases", "ll_alias": "an alias named ll / LL (0/1)",
    "fast_io": "ios::sync_with_stdio or cin.tie (0/1)", "cstdio_ratio": "scanf/printf calls / (those + cin/cout uses); NaN if neither",
    "endl_ratio": "endl / (endl + '\\n' literal uses); NaN if neither", "template_count": "'template<' occurrences",
    "pragma": "#pragma present (0/1)", "signed_main": "'signed main' (0/1)", "return0_main": "'return 0;' present (0/1)",
    "auto_per_100tok": "'auto' per 100 tokens", "const_per_100tok": "'const' per 100 tokens",
    "ident_mean_len": "mean identifier length", "ident_single_char_ratio": "1-char identifiers / identifiers",
    "ident_snake_ratio": "identifiers containing '_' (not leading) / identifiers", "ident_camel_ratio": "identifiers with an inner capital / identifiers",
    "ident_upper_ratio": "ALL_CAPS identifiers (len>1) / identifiers", "distinct_ident_ratio": "distinct identifiers / identifier tokens",
    # structure (expanded + tree)
    "n_tokens": "tokens in expanded code", "n_functions": "function definitions", "max_depth": "max AST depth", "mean_depth": "mean leaf depth",
    "for_per_100tok": "for loops per 100 tokens", "while_per_100tok": "while + do loops per 100 tokens", "if_per_100tok": "if per 100 tokens",
    "lambda_count": "lambda expressions", "struct_class_count": "struct/class specifiers", "recursion": "a function calls itself (0/1)",
    "goto_count": "goto statements", "ternary_per_100tok": "?: per 100 tokens", "type_token_entropy": "Shannon entropy of the D3 token-type stream (bits)",
    "parse_error_ratio": "ERROR/missing nodes / nodes",
}
KW = re.compile(r"\b(if|for|while)\s*\(")
KW_SP = re.compile(r"\b(if|for|while) \(")
OPS = re.compile(r"(?<![=<>!+\-*/&|^%])(==|<=|>=|=|<|>|\+|-|\*|/)(?![=<>+\-*/&|])")
IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
CPP_KW = set("""alignas alignof and asm auto bool break case catch char class const constexpr continue decltype default delete do double
else enum explicit extern false float for friend goto if inline int long mutable namespace new noexcept not nullptr operator or private
protected public register return short signed sizeof static struct switch template this throw true try typedef typename union unsigned
using virtual void volatile while define include pragma ifdef ifndef endif std main""".split())


def nan_div(a, b):
    return a / b if b else math.nan


def features(item):
    sha, code, expanded, types = item
    f = {}
    lines = code.split("\n")
    lines_nl = [ln.rstrip("\r") for ln in lines]
    nonblank = [ln for ln in lines_nl if ln.strip()]
    f["n_lines"] = len(lines_nl)
    f["blank_line_ratio"] = nan_div(len(lines_nl) - len(nonblank), len(lines_nl))
    lens = [len(ln) for ln in nonblank]
    f["mean_line_len"] = nan_div(sum(lens), len(lens))
    f["max_line_len"] = max(lens) if lens else math.nan
    f["long_line_ratio"] = nan_div(sum(x > 100 for x in lens), len(lens))
    indented = [ln for ln in nonblank if ln[:1] in (" ", "\t")]
    f["tab_indent_ratio"] = nan_div(sum(ln.startswith("\t") for ln in indented), len(indented))
    steps = Counter()
    prev = 0
    for ln in nonblank:
        if ln.startswith(" "):
            w = len(ln) - len(ln.lstrip(" "))
            if w > prev:
                steps[w - prev] += 1
            prev = w
        else:
            prev = 0
    f["indent_unit"] = steps.most_common(1)[0][0] if steps else math.nan
    brace_lines = [ln for ln in nonblank if "{" in ln]
    f["brace_own_line_ratio"] = nan_div(sum(ln.strip() == "{" for ln in brace_lines), len(brace_lines))
    f["crlf"] = float("\r\n" in code)
    f["space_after_keyword_ratio"] = nan_div(len(KW_SP.findall(code)), len(KW.findall(code)))
    ops_total = ops_spaced = 0
    for m in OPS.finditer(code):
        ops_total += 1
        s, e = m.start(), m.end()
        ops_spaced += s > 0 and e < len(code) and code[s - 1] == " " and code[e] == " "
    f["space_around_op_ratio"] = nan_div(ops_spaced, ops_total)
    f["trailing_ws_ratio"] = nan_div(sum(ln != ln.rstrip() for ln in lines_nl), len(lines_nl))
    f["one_liner_ratio"] = nan_div(sum(ln.count(";") >= 2 and not ln.lstrip().startswith("for") for ln in nonblank), len(nonblank))
    # lexical
    f["comment_line_ratio"] = nan_div(sum(("//" in ln) or ("/*" in ln) for ln in nonblank), len(nonblank))
    f["block_comment"] = float("/*" in code)
    defines = re.findall(r"^\s*#\s*define\s+(\w+)(\()?", code, re.M)
    f["n_defines"] = len(defines)
    f["n_func_macros"] = sum(1 for _, p in defines if p)
    f["define_int_ll"] = float(bool(re.search(r"#\s*define\s+int\s+long\s+long", code)))
    f["bits_stdcpp"] = float("bits/stdc++.h" in code)
    f["n_includes"] = len(re.findall(r"^\s*#\s*include", code, re.M))
    f["using_namespace_std"] = float(bool(re.search(r"using\s+namespace\s+std\s*;", code)))
    f["n_typedef_using"] = len(re.findall(r"\btypedef\b", code)) + len(re.findall(r"\busing\s+\w+\s*=", code))
    f["ll_alias"] = float(bool(re.search(r"(typedef\s+long\s+long\s+(ll|LL)\b)|(using\s+(ll|LL)\s*=)|(#\s*define\s+(ll|LL)\s)", code)))
    f["fast_io"] = float(bool(re.search(r"sync_with_stdio|cin\s*\.\s*tie", code)))
    c_io = len(re.findall(r"\b(scanf|printf)\s*\(", code))
    cpp_io = len(re.findall(r"\bcin\b|\bcout\b", code))
    f["cstdio_ratio"] = nan_div(c_io, c_io + cpp_io)
    endl = len(re.findall(r"\bendl\b", code))
    nl = code.count("'\\n'") + code.count('"\\n"')
    f["endl_ratio"] = nan_div(endl, endl + nl)
    f["template_count"] = len(re.findall(r"\btemplate\s*<", code))
    f["pragma"] = float(bool(re.search(r"^\s*#\s*pragma", code, re.M)))
    f["signed_main"] = float(bool(re.search(r"\bsigned\s+main\s*\(", code)))
    f["return0_main"] = float("return 0;" in code)
    stripped = re.sub(r"//[^\n]*|/\*.*?\*/|\"(\\.|[^\"\\])*\"|'(\\.|[^'\\])*'", " ", code, flags=re.S)
    idents = [w for w in IDENT.findall(stripped) if w not in CPP_KW]
    f["ident_mean_len"] = nan_div(sum(map(len, idents)), len(idents))
    f["ident_single_char_ratio"] = nan_div(sum(len(w) == 1 for w in idents), len(idents))
    f["ident_snake_ratio"] = nan_div(sum("_" in w.lstrip("_") for w in idents), len(idents))
    f["ident_camel_ratio"] = nan_div(sum(bool(re.search(r"[a-z][A-Z]", w)) for w in idents), len(idents))
    f["ident_upper_ratio"] = nan_div(sum(len(w) > 1 and w.isupper() for w in idents), len(idents))
    f["distinct_ident_ratio"] = nan_div(len(set(idents)), len(idents))
    # structure
    toks = types.split()
    n = len(toks)
    f["n_tokens"] = n
    per100 = lambda k: nan_div(100 * k, n)
    f["auto_per_100tok"] = per100(toks.count("auto"))
    f["const_per_100tok"] = per100(toks.count("const"))
    src = expanded.encode()
    root = parser().parse(src).root_node
    counts, depths, n_nodes, n_err, max_depth = Counter(), [], 0, 0, 0
    funcs, recursion = [], False
    stack = [(root, 0)]
    while stack:
        node, d = stack.pop()
        n_nodes += 1
        n_err += node.type == "ERROR" or node.is_missing
        counts[node.type] += 1
        max_depth = max(max_depth, d)
        if node.child_count == 0:
            depths.append(d)
        if node.type == "function_definition":
            decl = node.child_by_field_name("declarator")
            name = None
            while decl is not None and name is None:
                if decl.type in ("identifier", "field_identifier"):
                    name = src[decl.start_byte:decl.end_byte].decode("utf-8", "replace")
                else:
                    decl = decl.child_by_field_name("declarator")
            body = node.child_by_field_name("body")
            if name and body is not None and not recursion:
                recursion = bool(re.search(r"\b" + re.escape(name) + r"\s*\(", src[body.start_byte:body.end_byte].decode("utf-8", "replace")))
        stack.extend((ch, d + 1) for ch in node.children)
    f["n_functions"] = counts["function_definition"]
    f["max_depth"] = max_depth
    f["mean_depth"] = nan_div(sum(depths), len(depths))
    f["for_per_100tok"] = per100(counts["for_statement"] + counts["for_range_loop"])
    f["while_per_100tok"] = per100(counts["while_statement"] + counts["do_statement"])
    f["if_per_100tok"] = per100(counts["if_statement"])
    f["lambda_count"] = counts["lambda_expression"]
    f["struct_class_count"] = counts["struct_specifier"] + counts["class_specifier"]
    f["recursion"] = float(recursion)
    f["goto_count"] = counts["goto_statement"]
    f["ternary_per_100tok"] = per100(counts["conditional_expression"])
    tc = Counter(toks)
    f["type_token_entropy"] = -sum(v / n * math.log2(v / n) for v in tc.values()) if n else math.nan
    f["parse_error_ratio"] = nan_div(n_err, n_nodes)
    return (sha, *[float(f[k]) for k in FEATURES])


PARAMS = {"features": FEATURES, "views": {"layout+lexical": "original bytes (canonical code)", "structure": "D3 expanded + tree-sitter"}}

if __name__ == "__main__":
    names = ["code_sha256", *FEATURES]
    with Build("code_features", 1, PARAMS, __file__, derived_inputs=["code_views"]) as b:
        c = b.c
        cache = b.final.parent / ("_cache_smoke" if b.smoke else "_cache_parts")  # survives failures; removed on success
        cache.mkdir(parents=True, exist_ok=True)
        types = [pa.string()] + [pa.float64()] * len(FEATURES)
        for part in "0123456789abcdef":
            if (cache / f"{part}.parquet").exists():
                print(f"  part {part}: cached", flush=True)
                continue
            c.execute(f"create or replace temp table need as select code_sha256 from code_views where left(code_sha256, 1) = '{part}'")
            raw = fetch_code(c, "need")
            views = dict((h, (e, t)) for h, e, t in c.execute(
                f"select code_sha256, expanded, types from code_views where left(code_sha256, 1) = '{part}'").fetchall())
            items = [(h, raw[h], *views[h]) for h in views]
            del raw, views
            with Pool(7) as pool:
                rows = pool.map(features, items, chunksize=200)
            del items
            cols = list(zip(*rows)) if rows else [()] * len(names)
            t = pa.Table.from_arrays([pa.array(col, type=ty) for col, ty in zip(cols, types)], names=names)
            c.register("t_arrow", t)
            c.execute(f"copy (select * from t_arrow) to '{cache}/{part}.tmp.parquet' (format parquet, compression zstd)")
            c.unregister("t_arrow")
            (cache / f"{part}.tmp.parquet").rename(cache / f"{part}.parquet")
            print(f"  part {part}: {len(rows)} files", flush=True)
            del rows, t
        # written one cached part at a time, 2 threads, small row groups: long text columns must never be
        # buffered for the whole corpus (that ran out of memory twice)
        c.execute("set threads = 2")
        for (corpus,) in c.execute("select distinct corpus from code_views order by 1").fetchall():
            d = b.stage / f"corpus={corpus}"
            d.mkdir()
            for part in sorted(cache.glob("[0-9a-f].parquet")):
                c.execute(f"""copy (select f.* from read_parquet('{part}') f semi join code_views x
                              on x.code_sha256 = f.code_sha256 and x.corpus = '{corpus}')
                              to '{d}/part-{part.stem}.parquet' (format parquet, compression zstd, row_group_size 20000)""")
        c.execute("set threads = 8")
        c.execute(f"create view code_features as select * from read_parquet('{b.stage}/corpus=*/*.parquet', hive_partitioning=true)")
        b.on_success.append(lambda: shutil.rmtree(cache, ignore_errors=True))
        one = b.one
        b.check("every code view has features", one("select count(*) from code_views anti join code_features using (code_sha256)") == 0)
        b.check("one row per blob", one("select count(*) - count(distinct code_sha256) from code_features") == 0)
        nan_rates = c.execute("select " + ", ".join(f"round(avg(isnan({k})::int), 3)" for k in FEATURES) + " from code_features").fetchone()
        b.count("nan_rate_by_feature", {k: v for k, v in zip(FEATURES, nan_rates) if v})
        b.count("medians", dict(zip(FEATURES, c.execute("select " + ", ".join(f"median({k})" for k in FEATURES) + " from code_features").fetchone())))
