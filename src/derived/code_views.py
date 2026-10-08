"""D3 code_views — every C++ code file normalised, macro-expanded and tokenised, keyed by code_sha256.

Competitive-programming C++ cannot be parsed or compared fairly as written: 42% of files use
function-like macros (`#define rep(i,n) for(...)`), and a parser or similarity tool that does not expand
them sees almost nothing (constraint 6). Per distinct code file:

  norm          CRLF -> LF, trailing whitespace stripped, blank lines dropped. Comments kept (they are style).
  expanded      `#include` lines removed, then the clang preprocessor (-E -P) run, if and only if the file has
                any other preprocessor directive; otherwise `norm` minus `#include` lines. Debug blocks under
                `#ifdef LOCAL` etc. disappear here, as they do on the judge.
  cpp_status    not_needed | ok | error | timeout   (on error/timeout, expanded = norm minus #include lines)
                | view_error:<Exception> — the file could not be processed; types empty (counted by a check)
  types         the token-type stream of `expanded`: identifiers -> ID, numbers -> NUM, strings/chars -> STR,
                built-in types -> TYPE, everything else (keywords, operators, punctuation) as itself; comments dropped.
  n_tokens, n_nodes, n_error_nodes, parse_has_error   from tree-sitter-cpp on `expanded`.
  parse_has_error_unexpanded   the same parse on `norm` minus #include lines — measures what expansion fixes.

v2 (2026-10-08): a file is C++ if ANY of its submissions is C++, and the key is the bytes across corpora.
v1 took one submission's language arbitrarily and dropped 43 C++ submissions; removed before any use.

usage: python src/derived/code_views.py
"""
import re, shutil, subprocess, sys
from multiprocessing import Pool
from pathlib import Path
import pyarrow as pa
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.derived.common import Build  # noqa: E402
from src.canonical.db import fetch_code  # noqa: E402

# the real clang binary, not the /usr/bin/clang xcrun shim (12 ms vs 96 ms per call)
CLANG = subprocess.run(["xcrun", "-f", "clang"], capture_output=True, text=True).stdout.strip() or "clang"
CPP = [CLANG, "-E", "-P", "-w", "-x", "c++", "-std=gnu++17", "-"]
PARAMS = {"languages": ["C++"], "is_cpp": "any submission of the bytes has lang_family C++", "key": "code_sha256 (across corpora)", "cpp": " ".join(CPP), "cpp_timeout_s": 10,
          "cpp_trigger": "any line starting with # other than #include / #pragma",
          "type_map": {"identifier|field_identifier|type_identifier|namespace_identifier|statement_identifier": "ID",
                       "number_literal": "NUM", "string_literal|char_literal|raw_string_literal|system_lib_string": "STR",
                       "primitive_type|sized_type_specifier": "TYPE", "comment": "(dropped)", "other leaves": "their text"},
          "tree_sitter_cpp": None, "clang": None}
ID = {"identifier", "field_identifier", "type_identifier", "namespace_identifier", "statement_identifier"}
STR = {"string_literal", "char_literal", "raw_string_literal", "system_lib_string", "concatenated_string"}
TYPE = {"primitive_type", "sized_type_specifier"}
DIRECTIVE = re.compile(r"^\s*#\s*(\w+)", re.M)
INCLUDE = re.compile(r"^\s*#\s*include\b.*$", re.M)

_parser = None


def parser():
    global _parser
    if _parser is None:
        import tree_sitter_cpp
        from tree_sitter import Language, Parser
        _parser = Parser(Language(tree_sitter_cpp.language()))
    return _parser


def normalise(code):
    return "\n".join(ln.rstrip() for ln in code.replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()) + "\n"


def leaves(root, out):
    # leaves = nodes without children; string-ish and type-ish nodes are taken whole. Iterative: macro-expanded
    # code can nest deeper than Python's recursion limit.
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in STR or node.type in TYPE or node.child_count == 0:
            out.append(node)
        else:
            stack.extend(reversed(node.children))


def view(item):
    try:
        return _view(item)
    except Exception as e:  # one pathological file must not sink the build; counted by a check
        sha, code = item
        norm = normalise(code)
        return (sha, norm, norm, "view_error:" + type(e).__name__, "", 0, 0, 0, True, True)


def _view(item):
    sha, code = item
    norm = normalise(code)
    no_inc = INCLUDE.sub("", norm)
    directives = {d for d in DIRECTIVE.findall(norm)} - {"include", "pragma"}
    status, expanded = "not_needed", no_inc
    if directives:
        try:
            r = subprocess.run(CPP, input=no_inc.encode(), capture_output=True, timeout=PARAMS["cpp_timeout_s"])
            if r.returncode == 0:
                status, expanded = "ok", normalise(r.stdout.decode("utf-8", "replace"))
            else:
                status = "error"
        except subprocess.TimeoutExpired:
            status = "timeout"
    src = expanded.encode()
    tree = parser().parse(src)
    lv = []
    leaves(tree.root_node, lv)
    toks = []
    for n in lv:
        t = n.type
        if t == "comment":
            continue
        toks.append("ID" if t in ID else "NUM" if t == "number_literal" else "STR" if t in STR else "TYPE" if t in TYPE
                    else src[n.start_byte:n.end_byte].decode("utf-8", "replace") or t)
    n_nodes = n_err = 0
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        n_nodes += 1
        n_err += n.type == "ERROR" or n.is_missing
        stack.extend(n.children)
    raw_has_error = parser().parse(no_inc.encode()).root_node.has_error if status != "not_needed" else tree.root_node.has_error
    return (sha, norm, expanded, status, " ".join(toks), len(toks), n_nodes, n_err, tree.root_node.has_error, raw_has_error)


if __name__ == "__main__":
    import tree_sitter_cpp
    PARAMS["tree_sitter_cpp"] = getattr(tree_sitter_cpp, "__version__", None) or \
        subprocess.run([sys.executable, "-m", "pip", "show", "tree-sitter-cpp"], capture_output=True, text=True).stdout.split("Version: ")[1].split()[0]
    PARAMS["clang"] = subprocess.run([CLANG, "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    TYPES = [pa.string(), pa.string(), pa.string(), pa.string(), pa.string(), pa.int64(), pa.int64(), pa.int64(), pa.bool_(), pa.bool_()]
    names = ["code_sha256", "norm", "expanded", "cpp_status", "types", "n_tokens", "n_nodes", "n_error_nodes", "parse_has_error",
             "parse_has_error_unexpanded"]
    import os
    smoke = bool(os.environ.get("SMOKE"))  # SMOKE=1: whole pipeline on 3,000 files, as dataset code_views_smoke
    with Build("code_views", 2, PARAMS, __file__, memory="3GB") as b:
        c = b.c
        # a view describes bytes, not a corpus: one row per distinct code_sha256 (the same bytes can sit in two
        # corpora); a file is C++ if ANY submission of it was C++ (a few files were submitted as both C and C++)
        c.execute("""create table blobs as select code_sha256, min(corpus) corpus,
                       bool_or(lang_family = 'C++') is_cpp, string_agg(distinct lang_family, ',') lang_families
                     from submissions where code_sha256 is not null group by 1""")
        c.execute("create table cpp_blobs as select code_sha256, corpus from blobs where is_cpp"
                  + (" order by code_sha256 limit 3000" if smoke else ""))
        # parts are cached OUTSIDE staging, so a failure later in the build never throws away finished parts;
        # a rerun skips parts already done. The cache is removed only after a successful build.
        cache = b.final.parent / ("_cache_smoke" if smoke else "_cache_parts")
        cache.mkdir(parents=True, exist_ok=True)
        for part in "0123456789abcdef":
            if (cache / f"{part}.parquet").exists():
                print(f"  part {part}: cached", flush=True)
                continue
            c.execute(f"create or replace temp table need as select code_sha256 from cpp_blobs where left(code_sha256, 1) = '{part}'")
            code = fetch_code(c, "need")
            with Pool(8) as pool:
                rows = pool.map(view, code.items(), chunksize=200)
            del code
            t = pa.Table.from_arrays([pa.array(col, type=ty) for col, ty in zip(zip(*rows) if rows else [()] * len(names), TYPES)],
                                     names=names)
            c.register("t_arrow", t)
            c.execute(f"copy (select * from t_arrow) to '{cache}/{part}.tmp.parquet' (format parquet, compression zstd)")
            c.unregister("t_arrow")
            (cache / f"{part}.tmp.parquet").rename(cache / f"{part}.parquet")
            print(f"  part {part}: {len(rows)} files", flush=True)
            del rows, t
        # stream the parts straight into the output, one corpus at a time — never materialise ~4 GB of text
        # written one cached part at a time, 2 threads, small row groups: long text columns must never be
        # buffered for the whole corpus (that ran out of memory twice)
        c.execute("set threads = 2")
        for (corpus,) in c.execute("select distinct corpus from cpp_blobs order by 1").fetchall():
            d = b.stage / f"corpus={corpus}"
            d.mkdir()
            for part in sorted(cache.glob("[0-9a-f].parquet")):
                c.execute(f"""copy (select v.* from read_parquet('{part}') v semi join cpp_blobs x
                              on x.code_sha256 = v.code_sha256 and x.corpus = '{corpus}')
                              to '{d}/part-{part.stem}.parquet' (format parquet, compression zstd, row_group_size 20000)""")
        c.execute("set threads = 8")
        c.execute(f"create or replace view code_views as select * from read_parquet('{b.stage}/corpus=*/*.parquet', hive_partitioning=true)")
        b.on_success.append(lambda: shutil.rmtree(cache, ignore_errors=True))

        one = b.one
        b.check("every C++ blob has a view", one("select count(*) from cpp_blobs anti join code_views using (code_sha256)") == 0)
        b.check("one row per blob", one("select count(*) - count(distinct code_sha256) from code_views") == 0)
        # comment-only files, or a whole program written after `#include <x>` on one line, really have no tokens
        # once preprocessed — the judge's compiler sees nothing either. Such a file must never have been accepted.
        b.check("an empty token stream only comes from compile errors", one("""select count(*) from code_views v
                 join submissions s using (code_sha256) where v.n_tokens = 0 and s.verdict <> 'CE'""") == 0)
        b.count("files_with_no_tokens (all CE)", one("select count(*) from code_views where n_tokens = 0"))
        b.check("view errors are rare (< 0.01%)", one("select count(*) from code_views where cpp_status like 'view_error%'") < 0.0001 * one("select count(*) from code_views"))
        b.count("blobs_by_language", dict(c.execute("select lang_families, count(*) from blobs group by 1 order by 2 desc limit 12").fetchall()))
        if not b.smoke:  # smoke processes a 3,000-file sample by design
            b.check("every C++ submission's code has a view", one("""select count(*) from submissions s anti join code_views v using (code_sha256)
                     where s.lang_family = 'C++' and s.code_sha256 is not null""") == 0)
        b.count("cpp_status", dict(c.execute("select cpp_status, count(*) from code_views group by 1 order by 2 desc").fetchall()))
        b.count("parse_has_error_rate", one("select round(avg(parse_has_error::int), 4) from code_views"))
        b.count("parse_has_error_rate_by_cpp_status", dict(c.execute(
            "select cpp_status, round(avg(parse_has_error::int), 4) from code_views group by 1").fetchall()))
        b.count("median_tokens", one("select median(n_tokens) from code_views"))
        b.count("parse_error_rate_before_vs_after_expansion_where_cpp_ran", c.execute(
            """select round(avg(parse_has_error_unexpanded::int), 4), round(avg(parse_has_error::int), 4)
               from code_views where cpp_status = 'ok'""").fetchone())
