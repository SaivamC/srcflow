#!/usr/bin/env python3
"""
srcflow — Dependency Flow Visualizer for JS/TS/Python/Go/Rust/Java/C++ and more.

Usage:
  srcflow                          # current dir  -> ./depflow.html
  srcflow /path/to/repo            # specific repo
  srcflow /path/to/repo --out /tmp/out.html
  srcflow /path/to/repo --src /path/to/repo/src
"""

import re, json, sys, argparse
from pathlib import Path

# ── CLI args ───────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Dependency flow visualizer for any source repo")
    p.add_argument("repo", nargs="?", default=".",
                   help="Repo root path (default: current dir)")
    p.add_argument("--out", default=None,
                   help="Output HTML path (default: <repo>/depflow.html)")
    p.add_argument("--src", default=None,
                   help="Source root override (default: auto-detect src/ or repo root)")
    return p.parse_args()

# ── File types ─────────────────────────────────────────────────────────────────

SOURCE_EXTS = {
    # Web / JS
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte",
    # Python
    ".py",
    # C / C++
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".hh",
    # JVM
    ".java", ".kt", ".kts", ".scala",
    # Go
    ".go",
    # Rust
    ".rs",
    # Ruby
    ".rb",
    # PHP
    ".php",
    # C#
    ".cs",
    # Dart
    ".dart",
    # Haskell
    ".hs",
    # Lua
    ".lua",
    # Shell
    ".sh", ".bash", ".zsh",
    # R  (stored lowercase; checked with .lower())
    ".r",
    # Swift
    ".swift",
}

SKIP_DIRS = {
    # JS/TS
    "node_modules", "__tests__", "__mocks__", "mocks",
    "coverage", "dist", "build", "out", ".next", ".nuxt",
    "fixtures", "stubs", "e2e", "cypress", ".cache", ".turbo",
    # Python
    "__pycache__", "venv", ".venv", "env", ".tox", "site-packages", ".eggs", "egg-info",
    # Java/Kotlin/Scala
    "target", ".gradle", ".mvn",
    # Rust / Go / general
    "vendor", "bundle",
    # C#
    "bin", "obj", ".vs",
    # General VCS / IDE
    ".git", ".idea", ".vscode",
}

TEST_PATTERNS_SUFFIX = (
    # JS/TS
    ".test.js", ".spec.js", ".test.ts", ".spec.ts",
    ".test.jsx", ".spec.jsx", ".test.tsx", ".spec.tsx",
    # JVM
    "Test.java", "Tests.java", "Spec.java",
    "Test.kt", "Spec.kt", "Test.scala", "Spec.scala",
    # PHP / C#
    "Test.php", "Test.cs", "Tests.cs",
)

def is_test_file(p: Path) -> bool:
    name = p.name
    ext  = p.suffix.lower()
    if any(name.endswith(s) for s in TEST_PATTERNS_SUFFIX):
        return True
    if ext == ".py"  and (name.startswith("test_") or name.endswith("_test.py")):
        return True
    if ext == ".go"  and name.endswith("_test.go"):
        return True
    if ext == ".rb"  and (name.endswith("_spec.rb") or name.endswith("_test.rb")):
        return True
    if ext == ".rs"  and name.startswith("test_"):
        return True
    return False

# ── Source-root detection ──────────────────────────────────────────────────────

def find_src_root(root: Path) -> Path:
    for cand in ["src", "lib", "app", "source", "Sources"]:
        p = root / cand
        if p.is_dir():
            return p
    return root

# ── Column assignment ──────────────────────────────────────────────────────────

BUCKETS = [
    (0, "Model / Store",      ["model", "models", "store", "stores", "data", "entity",
                                "entities", "schema", "db", "database", "migrations",
                                "migration", "domain", "repository", "repositories"]),
    (1, "Pages / Views",      ["pages", "views", "screens", "routes", "page", "view",
                                "screen", "route", "templates", "template",
                                "controllers", "controller", "handlers", "handler"]),
    (2, "Components",         ["components", "component", "widgets", "widget", "ui",
                                "elements", "blocks", "serializers", "serializer",
                                "forms", "form", "presenters", "presenter"]),
    (3, "Services / Effects", ["services", "service", "effects", "effect", "hooks",
                                "hook", "api", "actions", "action", "behaviors",
                                "behavior", "tasks", "task", "signals", "signal",
                                "managers", "manager", "middleware", "workers", "worker",
                                "jobs", "job", "cmd", "commands", "command"]),
    (4, "Utils / Constants",  ["utils", "util", "helpers", "helper", "lib", "common",
                                "shared", "constants", "constant", "config", "runtime",
                                "types", "decorators", "decorator", "validators",
                                "validator", "pkg", "internal", "core", "base"]),
]

PALETTE = [
    ("#a78bfa","#1a1530"),("#4a9eff","#101a2a"),("#3ecf8e","#0f1f1a"),
    ("#fb923c","#1f1510"),("#f472b6","#1f1020"),("#22d3ee","#0f1a1f"),
    ("#facc15","#1f1a00"),("#94a3b8","#14161f"),("#f87171","#1f1010"),
    ("#a3e635","#131a0a"),
]

def bucket_for(dirname: str) -> int:
    d = dirname.lower()
    for pri, _name, frags in BUCKETS:
        if any(f in d for f in frags):
            return pri
    return 99

def assign_columns(top_dirs: list) -> tuple:
    sorted_dirs = sorted(top_dirs, key=lambda d: (bucket_for(d), d))
    pri_to_col, col_names = {}, []
    for d in sorted_dirs:
        pri = bucket_for(d)
        if pri not in pri_to_col:
            pri_to_col[pri] = len(col_names)
            label = next((nm for p2, nm, _ in BUCKETS if p2 == pri), d.title())
            col_names.append(label)
    col_of = {d: pri_to_col[bucket_for(d)] for d in sorted_dirs}
    layer_meta = []
    for i, name in enumerate(col_names):
        color, bg = PALETTE[i % len(PALETTE)]
        layer_meta.append({"name": name, "color": color, "bg": bg})
    return col_of, layer_meta

# ── File scanning ──────────────────────────────────────────────────────────────

def all_source_files(src_root: Path) -> list:
    files = []
    for p in sorted(src_root.rglob("*")):
        if not p.is_file():                           continue
        if p.suffix.lower() not in SOURCE_EXTS:       continue
        if any(d in p.parts for d in SKIP_DIRS):      continue
        if is_test_file(p):                            continue
        files.append(p)
    return files

# ── Language-specific import regexes ──────────────────────────────────────────

_JS_IMPORT_RE   = re.compile(r"""import\s+.*?from\s+['"]([^'"]+)['"]""", re.DOTALL)
_JS_REQUIRE_RE  = re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)""")

_PY_IMPORT_RE   = re.compile(r'^\s*(?:from\s+(\S+)\s+import|import\s+(\S+))', re.MULTILINE)

_C_INCLUDE_RE   = re.compile(r'#\s*include\s+"([^"]+)"')

_JVM_IMPORT_RE  = re.compile(r'^\s*import\s+(?:static\s+)?([\w.]+)', re.MULTILINE)

_GO_QUOTED_RE   = re.compile(r'"([^"]+)"')

_RUST_MOD_RE    = re.compile(r'^\s*(?:pub\s+)?mod\s+(\w+)\s*;', re.MULTILINE)

_RUBY_REL_RE    = re.compile(r"require_relative\s+['\"]([^'\"]+)['\"]")
_RUBY_REQ_RE    = re.compile(r"(?<!['\w])require\s+['\"]([^'\"]+)['\"]")

_PHP_REQ_RE     = re.compile(r"""(?:require|include)(?:_once)?\s*[(\s]['"]([^'"]+)['"]""")

_CS_USING_RE    = re.compile(r'^\s*using\s+([\w.]+)\s*;', re.MULTILINE)

_DART_IMPORT_RE = re.compile(r"""import\s+['"]([^'"]+)['"]""")

_HS_IMPORT_RE   = re.compile(r'^\s*import\s+(?:qualified\s+)?([\w.]+)', re.MULTILINE)

_LUA_REQ_RE     = re.compile(r"""require\s*\(?\s*['"]([^'"]+)['"]""")

_SHELL_SRC_RE   = re.compile(r'^(?:source|\.) +([^\s;#"\']+|"[^"]+"|\'[^\']+\')', re.MULTILINE)

_R_SOURCE_RE    = re.compile(r"""source\s*\(\s*['"]([^'"]+)['"]""")

_SWIFT_IMPORT_RE = re.compile(r'^\s*import\s+(\w+)', re.MULTILINE)

# ── TS/JS path alias reader ───────────────────────────────────────────────────

def read_ts_aliases(root: Path) -> dict:
    """Read tsconfig.json compilerOptions.paths → {alias_prefix: resolved_dir}."""
    aliases = {}
    # Search root and one level deep (monorepos have tsconfig in sub-packages)
    candidates = [root / "tsconfig.json"]
    for sub in root.iterdir() if root.is_dir() else []:
        if sub.is_dir():
            for deep in [sub / "tsconfig.json", sub / "tsconfig.base.json"]:
                candidates.append(deep)
    candidates.append(root / "tsconfig.base.json")

    for tsconfig in candidates:
        if not tsconfig.is_file():
            continue
        try:
            data = json.loads(tsconfig.read_text(errors="replace"))
            co   = data.get("compilerOptions", {})
            base_url = (tsconfig.parent / co.get("baseUrl", ".")).resolve()
            for alias, targets in co.get("paths", {}).items():
                prefix = alias.rstrip("/*")
                if targets:
                    target_rel = targets[0].rstrip("/*")
                    aliases[prefix] = (base_url / target_rel).resolve()
        except Exception:
            pass

    # Fallback common aliases if not found in tsconfig
    for prefix in ("@", "~", "#"):
        if prefix not in aliases:
            for cand in [root / "src", root / "app", root / "lib", root]:
                if cand.is_dir():
                    aliases[prefix] = cand.resolve()
                    break
    return aliases

# ── Monorepo drill-through: find the first level with meaningful structure ────

def _find_top_dirs(files: list, root_abs: Path, src_parts: tuple):
    """
    Drills down through single-directory levels (e.g. apps/web/src/...)
    until we find a level with multiple dirs OR bucket-matching dir names.
    Returns (top_dirs set, extra_depth int).
    """
    for depth in range(6):
        dirs = set()
        for f in files:
            parts = f.resolve().relative_to(root_abs).parts
            idx = len(src_parts) + depth
            if idx < len(parts) - 1:      # not the file itself
                dirs.add(parts[idx])
        if not dirs:
            return dirs, depth
        if len(dirs) > 1:
            return dirs, depth
        if len(dirs) == 1 and bucket_for(next(iter(dirs))) < 99:
            return dirs, depth
        # single unrecognised dir — drill one level deeper
    return dirs, depth

# ── Per-language resolvers ────────────────────────────────────────────────────

def _resolve_rel_js(src: Path, spec: str, exts: tuple, aliases: dict):
    # Try alias prefixes first (e.g. @/components/foo → src/components/foo)
    for prefix, target_dir in aliases.items():
        if spec == prefix or spec.startswith(prefix + "/"):
            rel  = spec[len(prefix):].lstrip("/")
            base = target_dir / rel if rel else target_dir
            for c in [base] + [base.with_suffix(e) for e in exts] + [base / f"index{e}" for e in exts]:
                r = c.resolve()
                if r.is_file(): return r
    if not spec.startswith("."): return None
    base = src.parent / spec
    for c in [base] + [base.with_suffix(e) for e in exts] + [base / f"index{e}" for e in exts]:
        r = c.resolve()
        if r.is_file(): return r
    return None

def _resolve_py(src: Path, spec: str, src_root: Path, root: Path):
    if spec.startswith("."):
        dots = len(spec) - len(spec.lstrip("."))
        module = spec.lstrip(".")
        base = src.parent
        for _ in range(dots - 1):
            base = base.parent
        parts = module.split(".") if module else []
        candidate = base.joinpath(*parts) if parts else base
        for c in [candidate.with_suffix(".py"), candidate / "__init__.py"]:
            r = c.resolve()
            if r.is_file(): return r
    else:
        parts = spec.split(".")
        for base in [src_root, root]:
            candidate = base.joinpath(*parts)
            for c in [candidate.with_suffix(".py"), candidate / "__init__.py"]:
                r = c.resolve()
                if r.is_file(): return r
    return None

def _jvm_src_roots(root: Path, src_root: Path) -> list:
    roots = []
    for pattern in ["src/main/java", "src/main/kotlin", "src/main/scala", "src"]:
        p = (root / pattern).resolve()
        if p.is_dir():
            roots.append(p)
    sr = src_root.resolve()
    if sr not in roots:
        roots.append(sr)
    return roots

_JVM_SKIP_PREFIXES = (
    "java.", "javax.", "kotlin.", "android.", "androidx.",
    "org.junit", "org.mockito", "org.springframework",
    "com.google", "io.reactivex", "scala.",
)

def _resolve_jvm(spec: str, roots: list):
    if spec.endswith(".*") or any(spec.startswith(p) for p in _JVM_SKIP_PREFIXES):
        return None
    parts = spec.split(".")
    for sr in roots:
        for ext in [".java", ".kt", ".kts", ".scala"]:
            c = sr.joinpath(*parts).with_suffix(ext).resolve()
            if c.is_file(): return c
    return None

_go_mod_cache: dict = {}

def _find_go_module(start: Path) -> tuple:
    key = str(start)
    if key in _go_mod_cache:
        return _go_mod_cache[key]
    result = (None, None)
    for d in [start, *list(start.parents)[:6]]:
        gm = d / "go.mod"
        if gm.is_file():
            m = re.search(r'^module\s+(\S+)', gm.read_text(errors="replace"), re.MULTILINE)
            if m:
                result = (m.group(1), d.resolve())
                break
    _go_mod_cache[key] = result
    return result

def _resolve_go(spec: str, module_name: str, module_root: Path) -> Path | None:
    if not spec.startswith(module_name):
        return None
    rel = spec[len(module_name):].lstrip("/")
    if not rel:
        return None
    pkg_dir = (module_root / rel).resolve()
    if pkg_dir.is_dir():
        for f in sorted(pkg_dir.glob("*.go")):
            if not f.name.endswith("_test.go"):
                return f.resolve()
    return None

def _resolve_rust_mod(mod_name: str, src_file: Path) -> Path | None:
    stem = src_file.stem
    if stem in ("mod", "lib", "main"):
        base = src_file.parent
    else:
        base = src_file.parent / stem
    for candidate in [
        base / f"{mod_name}.rs",
        base / mod_name / "mod.rs",
        src_file.parent / f"{mod_name}.rs",
        src_file.parent / mod_name / "mod.rs",
    ]:
        r = candidate.resolve()
        if r.is_file(): return r
    return None

def _resolve_lua(spec: str, src: Path, src_root: Path, root: Path) -> Path | None:
    path_spec = spec.replace(".", "/")
    for base in [src.parent, src_root, root]:
        for cand in [base / (path_spec + ".lua"), base / path_spec / "init.lua"]:
            r = cand.resolve()
            if r.is_file(): return r
    return None

# ── Main import dispatcher ─────────────────────────────────────────────────────

def get_imports(p: Path, all_exts: tuple, src_root: Path, root: Path,
                aliases: dict = None) -> list:
    try:
        text = p.read_text(errors="replace")
    except Exception:
        return []

    ext = p.suffix.lower()
    results = []
    _aliases = aliases or {}

    # ── JS / TS / Vue / Svelte ───────────────────────────────────────────────
    if ext in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte"}:
        specs = [m.group(1) for m in _JS_IMPORT_RE.finditer(text)]
        specs += [m.group(1) for m in _JS_REQUIRE_RE.finditer(text)]
        return [r for s in specs if (r := _resolve_rel_js(p, s, all_exts, _aliases))]

    # ── Python ───────────────────────────────────────────────────────────────
    elif ext == ".py":
        for m in _PY_IMPORT_RE.finditer(text):
            spec = (m.group(1) or m.group(2) or "").split()[0].strip()
            if not spec: continue
            r = _resolve_py(p, spec, src_root, root)
            if r: results.append(r)

    # ── C / C++ ──────────────────────────────────────────────────────────────
    elif ext in {".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".hh"}:
        for m in _C_INCLUDE_RE.finditer(text):
            spec = m.group(1)
            for base in [p.parent, src_root, root]:
                c = (base / spec).resolve()
                if c.is_file():
                    results.append(c)
                    break

    # ── Java / Kotlin / Scala ────────────────────────────────────────────────
    elif ext in {".java", ".kt", ".kts", ".scala"}:
        roots = _jvm_src_roots(root, src_root)
        for m in _JVM_IMPORT_RE.finditer(text):
            r = _resolve_jvm(m.group(1), roots)
            if r: results.append(r)

    # ── Go ───────────────────────────────────────────────────────────────────
    elif ext == ".go":
        module_name, module_root = _find_go_module(root)
        if module_name and module_root:
            for m in _GO_QUOTED_RE.finditer(text):
                r = _resolve_go(m.group(1), module_name, module_root)
                if r: results.append(r)

    # ── Rust ─────────────────────────────────────────────────────────────────
    elif ext == ".rs":
        for m in _RUST_MOD_RE.finditer(text):
            r = _resolve_rust_mod(m.group(1), p)
            if r: results.append(r)

    # ── Ruby ─────────────────────────────────────────────────────────────────
    elif ext == ".rb":
        for m in _RUBY_REL_RE.finditer(text):
            spec = m.group(1)
            for c in [(p.parent / spec).resolve(), (p.parent / (spec + ".rb")).resolve()]:
                if c.is_file():
                    results.append(c); break
        for m in _RUBY_REQ_RE.finditer(text):
            spec = m.group(1)
            for base in [src_root, root / "lib", root]:
                found = False
                for c in [(base / spec).resolve(), (base / (spec + ".rb")).resolve()]:
                    if c.is_file():
                        results.append(c); found = True; break
                if found: break

    # ── PHP ──────────────────────────────────────────────────────────────────
    elif ext == ".php":
        for m in _PHP_REQ_RE.finditer(text):
            c = (p.parent / m.group(1)).resolve()
            if c.is_file(): results.append(c)

    # ── C# ───────────────────────────────────────────────────────────────────
    elif ext == ".cs":
        skip = ("System", "Microsoft", "NUnit", "Xunit", "Moq")
        for m in _CS_USING_RE.finditer(text):
            spec = m.group(1)
            if any(spec.startswith(s) for s in skip): continue
            parts = spec.split(".")
            for base in [src_root, root]:
                c = base.joinpath(*parts).with_suffix(".cs").resolve()
                if c.is_file():
                    results.append(c); break

    # ── Dart ─────────────────────────────────────────────────────────────────
    elif ext == ".dart":
        for m in _DART_IMPORT_RE.finditer(text):
            spec = m.group(1)
            if spec.startswith("."):
                c = (p.parent / spec).resolve()
                if c.is_file(): results.append(c)
            elif spec.startswith("package:") and "/" in spec:
                pkg_rel = spec.split("/", 1)[1]
                c = (root / "lib" / pkg_rel).resolve()
                if c.is_file(): results.append(c)

    # ── Haskell ──────────────────────────────────────────────────────────────
    elif ext == ".hs":
        skip = ("Prelude", "Data.", "Control.", "System.", "Text.", "Network.",
                "GHC.", "Numeric.", "Foreign.")
        for m in _HS_IMPORT_RE.finditer(text):
            spec = m.group(1)
            if any(spec.startswith(s) for s in skip): continue
            parts = spec.split(".")
            for base in [src_root, root]:
                c = base.joinpath(*parts).with_suffix(".hs").resolve()
                if c.is_file():
                    results.append(c); break

    # ── Lua ──────────────────────────────────────────────────────────────────
    elif ext == ".lua":
        for m in _LUA_REQ_RE.finditer(text):
            r = _resolve_lua(m.group(1), p, src_root, root)
            if r: results.append(r)

    # ── Shell ────────────────────────────────────────────────────────────────
    elif ext in {".sh", ".bash", ".zsh"}:
        for m in _SHELL_SRC_RE.finditer(text):
            spec = m.group(1).strip("\"'")
            c = (p.parent / spec).resolve()
            if c.is_file(): results.append(c)

    # ── R ────────────────────────────────────────────────────────────────────
    elif ext == ".r":
        for m in _R_SOURCE_RE.finditer(text):
            c = (p.parent / m.group(1)).resolve()
            if c.is_file(): results.append(c)

    # ── Swift (module-level, best-effort) ────────────────────────────────────
    elif ext == ".swift":
        for m in _SWIFT_IMPORT_RE.finditer(text):
            mod = m.group(1)
            # Look for a local Swift file with that name
            c = (p.parent / (mod + ".swift")).resolve()
            if c.is_file(): results.append(c)

    return results

# ── Build graph ────────────────────────────────────────────────────────────────

def build(root: Path, src_root: Path):
    all_exts   = tuple(sorted(SOURCE_EXTS))
    files      = all_source_files(src_root)
    if not files:
        print(f"No source files found in {src_root}", file=sys.stderr); sys.exit(1)

    root_abs  = root.resolve()
    src_parts = src_root.resolve().relative_to(root_abs).parts
    abs_to_id = {f.resolve(): str(f.resolve().relative_to(root_abs)) for f in files}
    abs_set   = set(abs_to_id)

    # Read TS/JS path aliases (handles @/, ~/, and tsconfig paths)
    aliases = read_ts_aliases(root_abs)

    # Drill through wrapper dirs (monorepos: apps/web/src/...) to find real structure
    top_dirs, extra_depth = _find_top_dirs(files, root_abs, src_parts)
    col_of, layer_meta = assign_columns(sorted(top_dirs))

    eff_depth = len(src_parts) + extra_depth   # total parts to skip before module dirs

    nodes = {}
    for f in files:
        fabs      = f.resolve()
        fid       = abs_to_id[fabs]
        all_parts = fabs.relative_to(root_abs).parts
        stripped  = all_parts[eff_depth:]
        top_dir   = stripped[0] if stripped else "root"
        layer_idx = col_of.get(top_dir, max(col_of.values(), default=0))
        grp = stripped[1] if len(stripped) > 2 else stripped[0] if stripped else "root"
        nodes[fid] = {"id": fid, "label": f.name, "path": fid, "layer": layer_idx, "group": grp}

    seen, edges = set(), []
    for f in files:
        fid = abs_to_id[f.resolve()]
        for dep in get_imports(f, all_exts, src_root, root_abs, aliases):
            if dep in abs_set and dep != f.resolve():
                dep_id = abs_to_id[dep]
                k = (fid, dep_id)
                if k not in seen:
                    seen.add(k)
                    edges.append({"source": fid, "target": dep_id})

    return list(nodes.values()), edges, layer_meta

# ─── HTML ─────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>__REPO_NAME__ · Dependency Flow</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0c0e16;--surface:#13151f;--border:#252836;
  --text:#dde2f0;--muted:#5a607a;--accent:#7c6af7;
  --up-color:#f87171;--down-color:#4a9eff;
}
body{background:var(--bg);color:var(--text);font-family:'SF Mono','Fira Code',Consolas,monospace;font-size:12px;
  display:flex;flex-direction:column;height:100vh;overflow:hidden}
#topbar{background:var(--surface);border-bottom:1px solid var(--border);
  padding:8px 14px;display:flex;align-items:center;gap:8px;flex-shrink:0;flex-wrap:wrap}
#topbar h1{font-size:13px;font-weight:700;color:var(--accent);white-space:nowrap}
#search{flex:1;min-width:200px;background:var(--bg);border:1px solid var(--border);
  border-radius:5px;padding:5px 10px;color:var(--text);font-family:inherit;font-size:12px;outline:none}
#search:focus{border-color:var(--accent)}
#search::placeholder{color:var(--muted)}
#match-info{font-size:11px;color:var(--muted);white-space:nowrap;min-width:60px}
.tb-btn{background:var(--border);border:none;color:var(--text);padding:4px 9px;border-radius:4px;
  cursor:pointer;font-family:inherit;font-size:11px;white-space:nowrap;transition:background .12s}
.tb-btn:hover{background:var(--accent)}
.zoom-group{display:flex;gap:2px;align-items:center}
.zoom-group .tb-btn{padding:4px 7px;font-size:13px;font-weight:700;min-width:28px;text-align:center}
#zoom-level{font-size:11px;color:var(--muted);min-width:38px;text-align:center}
.legend{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-left:4px}
.leg-item{display:flex;align-items:center;gap:4px;font-size:10px;color:var(--muted)}
.leg-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
#info{background:var(--surface);border-bottom:1px solid var(--border);
  padding:5px 14px;font-size:11px;color:var(--muted);min-height:26px;flex-shrink:0;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#info b{color:var(--text)}
#info .up{color:var(--up-color)}
#info .dn{color:var(--down-color)}
#col-tabs{background:var(--bg);border-bottom:1px solid var(--border);
  padding:4px 14px;display:flex;gap:6px;flex-shrink:0}
.col-tab{background:transparent;border:1px solid var(--border);color:var(--muted);
  padding:3px 10px;border-radius:4px;cursor:pointer;font-family:inherit;font-size:11px;
  transition:all .12s}
.col-tab:hover{border-color:currentColor}
#canvas-wrap{flex:1;overflow:hidden;position:relative;cursor:grab;user-select:none}
#canvas-wrap.panning{cursor:grabbing}
svg#graph{display:block;width:100%;height:100%}
.node-box{stroke-width:1.5px;cursor:pointer}
.node-box:hover{filter:brightness(1.4)}
.node-label{font-size:10.5px;font-family:'SF Mono','Fira Code',Consolas,monospace;
  pointer-events:none;dominant-baseline:middle;fill:#e2e4f0}
.edge{fill:none;stroke:#252836;stroke-width:1px;opacity:0.5;marker-end:url(#arr-def)}
.edge.hi-down{stroke:var(--down-color);stroke-width:2px;opacity:1;marker-end:url(#arr-dn)}
.edge.hi-up  {stroke:var(--up-color);  stroke-width:2px;opacity:1;marker-end:url(#arr-up)}
.edge.dim    {opacity:0.04}
.node-box.search-match{stroke:#facc15!important;stroke-width:2.5px!important}
.node-box.selected    {stroke:#ffffff!important;stroke-width:2.5px!important}
.node-box.hi-connected{stroke:#facc15!important;stroke-width:1.5px!important}
#zoom-hint{position:absolute;bottom:12px;right:14px;font-size:10px;color:var(--muted);
  pointer-events:none;background:var(--surface);padding:4px 8px;border-radius:4px;
  border:1px solid var(--border)}
</style>
</head>
<body>
<div id="topbar">
  <h1>⬡ __REPO_NAME__</h1>
  <input id="search" type="text" placeholder="Search any file… (↑↓ jump, Enter to focus)" autocomplete="off" spellcheck="false"/>
  <span id="match-info"></span>
  <div class="zoom-group">
    <button class="tb-btn" id="btn-zoom-out">−</button>
    <span id="zoom-level">100%</span>
    <button class="tb-btn" id="btn-zoom-in">+</button>
  </div>
  <button class="tb-btn" id="btn-fit">Fit all</button>
  <button class="tb-btn" id="btn-clear">Clear</button>
  <div class="legend" id="legend"></div>
</div>
<div id="col-tabs">
  <span style="font-size:10px;color:var(--muted);line-height:24px;margin-right:4px">Jump to column:</span>
</div>
<div id="info">Click any file box → see its imports <span class="dn">↓ blue</span> and who imports it <span class="up">↑ red</span> &nbsp;·&nbsp; Double-click → zoom in</div>
<div id="canvas-wrap">
  <svg id="graph">
    <defs>
      <marker id="arr-def" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
        <path d="M0,0 L0,6 L6,3 z" fill="#252836"/>
      </marker>
      <marker id="arr-dn" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
        <path d="M0,0 L0,6 L6,3 z" fill="#4a9eff"/>
      </marker>
      <marker id="arr-up" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
        <path d="M0,0 L0,6 L6,3 z" fill="#f87171"/>
      </marker>
    </defs>
    <g id="pan-group">
      <g id="layer-bands"/>
      <g id="group-labels"/>
      <g id="edges-layer"/>
      <g id="nodes-layer"/>
    </g>
  </svg>
  <div id="zoom-hint">Scroll = zoom at cursor &nbsp;·&nbsp; Drag = pan &nbsp;·&nbsp; Double-click node = zoom in</div>
</div>
<script>
const NODES = __NODES__;
const EDGES = __EDGES__;
const LAYER_META = __LAYER_META__;

const NODE_W=192,NODE_H=27,COL_PAD=28,ROW_GAP=6,GRP_GAP=20,COL_GAP=100,TOP_Y=52;

function layoutNodes(){
  const lg={};
  for(const n of NODES){(lg[n.layer]??={})[n.group]??=[];lg[n.layer][n.group].push(n);}
  const colW=NODE_W+COL_PAD*2;let colX=50;
  const positions={},colMeta=[];
  for(let li=0;li<LAYER_META.length;li++){
    const groups=lg[li];
    if(!groups){colX+=colW+COL_GAP;continue;}
    const sortedGrps=Object.keys(groups).sort();
    let y=TOP_Y;const grpMeta=[];
    for(const gName of sortedGrps){
      const ns=groups[gName],grpY=y;
      for(const n of ns){positions[n.id]={x:colX,y,cx:colX+NODE_W/2,cy:y+NODE_H/2};y+=NODE_H+ROW_GAP;}
      grpMeta.push({name:gName,y:grpY,h:y-grpY-ROW_GAP,nodes:ns});y+=GRP_GAP;
    }
    colMeta.push({li,x:colX,colW,colH:y,grpMeta});colX+=colW+COL_GAP;
  }
  return{positions,colMeta,totalW:colX+20};
}

const dnOf={},upOf={};
for(const n of NODES){dnOf[n.id]=[];upOf[n.id]=[];}
for(const e of EDGES){dnOf[e.source].push(e.target);upOf[e.target].push(e.source);}
const nodeById=Object.fromEntries(NODES.map(n=>[n.id,n]));

const NS="http://www.w3.org/2000/svg";
const mkEl=(tag,attrs)=>{const e=document.createElementNS(NS,tag);if(attrs)for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);return e;};
function bezier(x1,y1,x2,y2){const dx=Math.abs(x2-x1)*0.5;return`M${x1},${y1} C${x1+dx},${y1} ${x2-dx},${y2} ${x2},${y2}`;}

const{positions,colMeta,totalW}=layoutNodes();
const totalH=Math.max(...colMeta.map(c=>c.colH))+60;
const svg=document.getElementById('graph');
const panGroup=document.getElementById('pan-group');
const bandLayer=document.getElementById('layer-bands');
const glLayer=document.getElementById('group-labels');
const edgeLayer=document.getElementById('edges-layer');
const nodeLayer=document.getElementById('nodes-layer');

const colTabBar=document.getElementById('col-tabs');
for(const col of colMeta){
  const m=LAYER_META[col.li];
  bandLayer.appendChild(mkEl('rect',{x:col.x-COL_PAD,y:16,width:col.colW,height:totalH-30,fill:m.bg,rx:8}));
  const tg=mkEl('g',{style:'cursor:pointer'});
  tg.appendChild(mkEl('rect',{x:col.x-COL_PAD,y:16,width:col.colW,height:30,fill:'transparent',rx:8}));
  const tt=mkEl('text',{x:col.x+NODE_W/2,y:34,'text-anchor':'middle',fill:m.color,'font-size':'11.5','font-weight':'700','font-family':"'SF Mono','Fira Code',Consolas,monospace"});
  tt.textContent=m.name;tg.appendChild(tt);tg.addEventListener('click',()=>zoomToColumn(col));bandLayer.appendChild(tg);
  for(let gi=0;gi<col.grpMeta.length;gi++){
    const grp=col.grpMeta[gi];if(col.grpMeta.length===1)continue;
    if(gi>0){const div=mkEl('line',{x1:col.x-COL_PAD+10,y1:grp.y-GRP_GAP/2,x2:col.x+NODE_W+COL_PAD-10,y2:grp.y-GRP_GAP/2,stroke:'#1e2235','stroke-width':'1'});glLayer.appendChild(div);}
    const gl=mkEl('text',{x:col.x,y:grp.y-3,fill:'#333855','font-size':'9','font-family':"'SF Mono','Fira Code',Consolas,monospace"});gl.textContent=grp.name+'/';glLayer.appendChild(gl);
  }
  const tab=document.createElement('button');tab.className='col-tab';tab.textContent=m.name;tab.style.color=m.color;tab.style.borderColor=m.color+'55';tab.addEventListener('click',()=>zoomToColumn(col));colTabBar.appendChild(tab);
}

const edgeEls={};
for(const edge of EDGES){
  const sp=positions[edge.source],tp=positions[edge.target];if(!sp||!tp)continue;
  let x1,y1,x2,y2;
  if(sp.cx<=tp.cx){x1=sp.x+NODE_W;y1=sp.cy;x2=tp.x;y2=tp.cy;}else{x1=sp.x;y1=sp.cy;x2=tp.x+NODE_W;y2=tp.cy;}
  const path=mkEl('path',{d:bezier(x1,y1,x2,y2),class:'edge','data-src':edge.source,'data-tgt':edge.target});
  edgeEls[`${edge.source}::${edge.target}`]=path;edgeLayer.appendChild(path);
}

const nodeEls={};
for(const n of NODES){
  const pos=positions[n.id];if(!pos)continue;
  const m=LAYER_META[n.layer];
  const g=mkEl('g',{class:'node-g','data-id':n.id});
  const rect=mkEl('rect',{x:pos.x,y:pos.y,width:NODE_W,height:NODE_H,class:'node-box',fill:m.bg,stroke:m.color,rx:5});
  const maxC=24,lbl=n.label.length>maxC?n.label.slice(0,maxC-1)+'…':n.label;
  const text=mkEl('text',{x:pos.x+8,y:pos.y+NODE_H/2,class:'node-label'});text.textContent=lbl;
  g.appendChild(rect);g.appendChild(text);
  g.addEventListener('click',()=>selectNode(n.id,false));
  g.addEventListener('dblclick',()=>selectNode(n.id,true));
  g.addEventListener('mouseenter',()=>hoverNode(n));
  g.addEventListener('mouseleave',()=>{if(!selected)resetInfo();});
  nodeEls[n.id]={g,rect};nodeLayer.appendChild(g);
}

const legEl=document.getElementById('legend');
for(const m of LAYER_META){const d=document.createElement('div');d.className='leg-item';d.innerHTML=`<div class="leg-dot" style="background:${m.color}"></div>${m.name}`;legEl.appendChild(d);}
for(const[c,l]of[['#4a9eff','↓ imports'],['#f87171','↑ imported by']]){const d=document.createElement('div');d.className='leg-item';d.innerHTML=`<div class="leg-dot" style="background:${c}"></div>${l}`;legEl.appendChild(d);}

let selected=null;
function selectNode(id,doZoom){
  clearSelection();selected=id;
  const n=nodeById[id],dn=dnOf[id]||[],up=upOf[id]||[];
  nodeEls[id]?.rect.classList.add('selected');
  for(const p of edgeLayer.children)p.classList.add('dim');
  for(const t of dn){const k=`${id}::${t}`;edgeEls[k]?.classList.remove('dim');edgeEls[k]?.classList.add('hi-down');nodeEls[t]?.rect.classList.add('hi-connected');}
  for(const s of up){const k=`${s}::${id}`;edgeEls[k]?.classList.remove('dim');edgeEls[k]?.classList.add('hi-up');nodeEls[s]?.rect.classList.add('hi-connected');}
  setInfo(`<b>${esc(n.label)}</b> &nbsp;·&nbsp; ${n.path} &nbsp;·&nbsp; <span class="dn">imports ${dn.length}: ${dn.map(t=>nodeById[t]?.label||t).join(', ')||'—'}</span> &nbsp;·&nbsp; <span class="up">imported by ${up.length}: ${up.map(s=>nodeById[s]?.label||s).join(', ')||'—'}</span>`);
  if(doZoom)zoomToNode(id);
}
function clearSelection(){
  if(selected)nodeEls[selected]?.rect.classList.remove('selected');selected=null;
  for(const p of edgeLayer.children)p.classList.remove('dim','hi-down','hi-up');
  for(const n of NODES)nodeEls[n.id]?.rect.classList.remove('hi-connected');resetInfo();
}
function hoverNode(n){if(!selected){const dn=(dnOf[n.id]||[]).length,up=(upOf[n.id]||[]).length;setInfo(`<b>${esc(n.label)}</b> &nbsp;·&nbsp; ${n.path} &nbsp;·&nbsp; imports ${dn} &nbsp;·&nbsp; imported by ${up} &nbsp;·&nbsp; click to highlight · double-click to zoom`);}}
function resetInfo(){setInfo('Click any file box → imports <span class="dn">↓ blue</span>, imported by <span class="up">↑ red</span> &nbsp;·&nbsp; Double-click to zoom in');}
function setInfo(html){document.getElementById('info').innerHTML=html;}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
document.getElementById('btn-clear').addEventListener('click',clearSelection);
window.addEventListener('keydown',e=>{if(e.key==='Escape')clearSelection();});

let vx=0,vy=0,scale=1,drag=false,dragSx=0,dragSy=0,animRaf=null;
const wrap=document.getElementById('canvas-wrap');
function setTransform(anim){
  if(anim){panGroup.style.transition='transform 0.32s cubic-bezier(0.4,0,0.2,1)';setTimeout(()=>panGroup.style.transition='',360);}
  else panGroup.style.transition='none';
  panGroup.setAttribute('transform',`translate(${vx.toFixed(2)},${vy.toFixed(2)}) scale(${scale.toFixed(4)})`);
  document.getElementById('zoom-level').textContent=Math.round(scale*100)+'%';
}
function zoomAt(mx,my,factor){
  const ns=Math.max(0.15,Math.min(5,scale*factor)),ratio=ns/scale;
  vx=mx-(mx-vx)*ratio;vy=my-(my-vy)*ratio;scale=ns;setTransform(false);
}
function animateTo(tvx,tvy,tsc){
  const sx=vx,sy=vy,ss=scale,t0=performance.now(),dur=340,ease=t=>t<.5?2*t*t:-1+(4-2*t)*t;
  if(animRaf)cancelAnimationFrame(animRaf);
  function step(now){const t=Math.min((now-t0)/dur,1),e=ease(t);vx=sx+(tvx-sx)*e;vy=sy+(tvy-sy)*e;scale=ss+(tsc-ss)*e;setTransform(false);if(t<1)animRaf=requestAnimationFrame(step);}
  animRaf=requestAnimationFrame(step);
}
function zoomToRect(wx,wy,ww,wh,pad=40){
  const r=wrap.getBoundingClientRect(),s=Math.min((r.width-pad*2)/ww,(r.height-pad*2)/wh,2.8);
  animateTo(r.width/2-(wx+ww/2)*s,r.height/2-(wy+wh/2)*s,s);
}
function zoomToNode(id){const pos=positions[id];if(!pos)return;zoomToRect(pos.x,pos.y,NODE_W,NODE_H,80);}
function zoomToColumn(col){const maxH=Math.max(...col.grpMeta.map(g=>g.y+g.h),col.colH);zoomToRect(col.x-COL_PAD,TOP_Y-4,col.colW,maxH-TOP_Y+8);}
function fitAll(){const r=wrap.getBoundingClientRect(),s=Math.min(r.width/totalW,r.height/totalH)*0.92;animateTo((r.width-totalW*s)/2,(r.height-totalH*s)/2,s);}

wrap.addEventListener('wheel',e=>{e.preventDefault();const r=wrap.getBoundingClientRect();zoomAt(e.clientX-r.left,e.clientY-r.top,Math.exp(-(e.ctrlKey?e.deltaY*3:e.deltaY)*0.0012));},{passive:false});
let lastPinch=null;
wrap.addEventListener('touchstart',e=>{if(e.touches.length===2)lastPinch=null;},{passive:true});
wrap.addEventListener('touchmove',e=>{if(e.touches.length!==2){lastPinch=null;return;}e.preventDefault();const a=e.touches[0],b=e.touches[1],dist=Math.hypot(b.clientX-a.clientX,b.clientY-a.clientY);if(lastPinch){const r=wrap.getBoundingClientRect();zoomAt((a.clientX+b.clientX)/2-r.left,(a.clientY+b.clientY)/2-r.top,dist/lastPinch);}lastPinch=dist;},{passive:false});
wrap.addEventListener('mousedown',e=>{if(e.target.closest('.node-g'))return;drag=true;dragSx=e.clientX-vx;dragSy=e.clientY-vy;wrap.classList.add('panning');});
window.addEventListener('mousemove',e=>{if(!drag)return;vx=e.clientX-dragSx;vy=e.clientY-dragSy;setTransform(false);});
window.addEventListener('mouseup',()=>{drag=false;wrap.classList.remove('panning');});
window.addEventListener('keydown',e=>{if(e.target===document.getElementById('search'))return;const r=wrap.getBoundingClientRect(),cx=r.width/2,cy=r.height/2;if(e.key==='+'||e.key==='='){e.preventDefault();zoomAt(cx,cy,1.15);}if(e.key==='-'){e.preventDefault();zoomAt(cx,cy,1/1.15);}if(e.key==='0'){e.preventDefault();fitAll();}});
document.getElementById('btn-zoom-in').addEventListener('click',()=>{const r=wrap.getBoundingClientRect();zoomAt(r.width/2,r.height/2,1.25);});
document.getElementById('btn-zoom-out').addEventListener('click',()=>{const r=wrap.getBoundingClientRect();zoomAt(r.width/2,r.height/2,0.8);});
document.getElementById('btn-fit').addEventListener('click',fitAll);

let matchNodes=[],matchIdx=-1;
document.getElementById('search').addEventListener('input',e=>{
  const q=e.target.value.trim().toLowerCase();matchNodes=[];matchIdx=-1;
  for(const n of NODES){const hit=q&&(n.label.toLowerCase().includes(q)||n.path.toLowerCase().includes(q));nodeEls[n.id]?.rect.classList.toggle('search-match',hit);if(hit)matchNodes.push(n.id);}
  document.getElementById('match-info').textContent=q?`${matchNodes.length} match${matchNodes.length!==1?'es':''}` :'';
  if(matchNodes.length){matchIdx=0;panToNode(matchNodes[0]);}
});
document.getElementById('search').addEventListener('keydown',e=>{
  if(!matchNodes.length)return;
  if(e.key==='ArrowDown'){e.preventDefault();matchIdx=(matchIdx+1)%matchNodes.length;panToNode(matchNodes[matchIdx]);}
  if(e.key==='ArrowUp'){e.preventDefault();matchIdx=(matchIdx-1+matchNodes.length)%matchNodes.length;panToNode(matchNodes[matchIdx]);}
  if(e.key==='Enter'){if(matchNodes[matchIdx])selectNode(matchNodes[matchIdx],true);}
  if(e.key==='Escape'){e.target.value='';for(const n of NODES)nodeEls[n.id]?.rect.classList.remove('search-match');document.getElementById('match-info').textContent='';}
});
function panToNode(id){const pos=positions[id];if(!pos)return;const r=wrap.getBoundingClientRect();animateTo(r.width/2-pos.cx*scale,r.height/2-pos.cy*scale,scale);}
window.addEventListener('load',()=>{setTimeout(fitAll,60);});
</script>
</body>
</html>
"""

def main():
    args     = parse_args()
    root     = Path(args.repo).resolve()
    src_root = Path(args.src).resolve() if args.src else find_src_root(root)
    out      = Path(args.out) if args.out else root / "depflow.html"

    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr); sys.exit(1)

    print(f"Repo:       {root}")
    print(f"Src root:   {src_root}")

    nodes, edges, layer_meta = build(root, src_root)

    repo_name = root.name
    html = (HTML
        .replace("__REPO_NAME__",  repo_name)
        .replace("__NODES__",      json.dumps(nodes,      separators=(',',':')))
        .replace("__EDGES__",      json.dumps(edges,      separators=(',',':')))
        .replace("__LAYER_META__", json.dumps(layer_meta, separators=(',',':')))
    )
    out.write_text(html, encoding="utf-8")
    print(f"\n✓  {out}  ({out.stat().st_size // 1024} KB)")
    print(f"   {len(nodes)} files · {len(edges)} import edges · {len(layer_meta)} columns")
    print(f"   Open: open {out}")

if __name__ == "__main__":
    main()
