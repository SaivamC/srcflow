#!/usr/bin/env python3
"""
srcflow — Dependency Flow Visualizer for JS/TS/JSX/TSX/Python repos.

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
    p = argparse.ArgumentParser(description="Dependency flow visualizer for JS/TS/Python repos")
    p.add_argument("repo", nargs="?", default=".",
                   help="Repo root path (default: current dir)")
    p.add_argument("--out", default=None,
                   help="Output HTML path (default: <repo>/depflow.html)")
    p.add_argument("--src", default=None,
                   help="Source root override (default: auto-detect src/ or repo root)")
    return p.parse_args()

# ── Skip rules ─────────────────────────────────────────────────────────────────

SKIP_DIRS = {
    # JS/TS
    "node_modules","__tests__","tests","test","__mocks__","mocks",
    "coverage","dist","build","out",".next",".nuxt","vendor",
    "fixtures","stubs","e2e","cypress",".cache",".turbo",
    # Python
    "__pycache__","venv",".venv","env",".tox","site-packages",
    ".eggs","egg-info",
}
TEST_SUFFIXES = (
    ".test.js",".spec.js",".test.ts",".spec.ts",
    ".test.jsx",".spec.jsx",".test.tsx",".spec.tsx",
    ".unit.test.js",".unit.test.ts",
)
SOURCE_EXTS = {".js",".jsx",".ts",".tsx",".mjs",".cjs",".py"}

def is_test_file(p: Path) -> bool:
    if any(p.name.endswith(s) for s in TEST_SUFFIXES):
        return True
    if p.suffix == ".py" and (p.name.startswith("test_") or p.name.endswith("_test.py")):
        return True
    return False

# ── Source-root detection ──────────────────────────────────────────────────────

def find_src_root(root: Path) -> Path:
    for cand in ["src","lib","app","source"]:
        p = root / cand
        if p.is_dir():
            return p
    return root

# ── Column assignment — fully dynamic, based on directory names ────────────────

# (priority, display_name, keyword fragments that map to this column)
BUCKETS = [
    (0, "Model / Store",      ["model","models","store","stores","data","entity","entities",
                                "schema","db","database","migrations","migration"]),
    (1, "Pages / Views",      ["pages","views","screens","routes","page","view","screen","route",
                                "templates","template","controllers","controller"]),
    (2, "Components",         ["components","component","widgets","widget","ui","elements","blocks",
                                "serializers","serializer","forms","form"]),
    (3, "Services / Effects", ["services","service","effects","effect","hooks","hook","api",
                                "actions","action","behaviors","behavior","tasks","task",
                                "signals","signal","managers","manager","middleware"]),
    (4, "Utils / Constants",  ["utils","util","helpers","helper","lib","common","shared",
                                "constants","constant","config","runtime","types",
                                "decorators","decorator","validators","validator"]),
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
    """Returns (col_of: {dirname->idx}, layer_meta: list of dicts)."""
    sorted_dirs = sorted(top_dirs, key=lambda d: (bucket_for(d), d))
    pri_to_col  = {}
    col_names   = []

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
        if not p.is_file(): continue
        if p.suffix not in SOURCE_EXTS: continue
        if any(d in p.parts for d in SKIP_DIRS): continue
        if is_test_file(p): continue
        files.append(p)
    return files

# ── Import parsing ─────────────────────────────────────────────────────────────

# JS/TS
IMPORT_RE  = re.compile(r"""import\s+.*?from\s+['"]([^'"]+)['"]""", re.DOTALL)
REQUIRE_RE = re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)""")

# Python: captures the module spec from 'from X import ...' or 'import X'
PY_IMPORT_RE = re.compile(
    r'^\s*(?:from\s+(\S+)\s+import|import\s+(\S+))',
    re.MULTILINE
)

def resolve_rel(src: Path, spec: str, exts: tuple):
    if not spec.startswith("."): return None
    base = src.parent / spec
    for c in [base] + [base.with_suffix(e) for e in exts] + [base / f"index{e}" for e in exts]:
        r = c.resolve()
        if r.is_file(): return r
    return None

def resolve_py(src: Path, spec: str, src_root: Path, root: Path):
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

def get_imports(p: Path, exts: tuple, src_root: Path, root: Path) -> list:
    try: text = p.read_text(errors="replace")
    except Exception: return []

    if p.suffix == ".py":
        results = []
        for m in PY_IMPORT_RE.finditer(text):
            spec = (m.group(1) or m.group(2) or "").split()[0].strip()
            if not spec: continue
            r = resolve_py(p, spec, src_root, root)
            if r: results.append(r)
        return results
    else:
        specs = [m.group(1) for m in IMPORT_RE.finditer(text)]
        specs += [m.group(1) for m in REQUIRE_RE.finditer(text)]
        return [r for s in specs if (r := resolve_rel(p, s, exts))]

# ── Build graph ────────────────────────────────────────────────────────────────

def build(root: Path, src_root: Path):
    exts       = tuple(sorted(SOURCE_EXTS))
    files      = all_source_files(src_root)
    if not files:
        print(f"No source files found in {src_root}", file=sys.stderr); sys.exit(1)

    root_abs   = root.resolve()
    src_parts  = src_root.resolve().relative_to(root_abs).parts
    abs_to_id  = {f.resolve(): str(f.resolve().relative_to(root_abs)) for f in files}
    abs_set    = set(abs_to_id)

    # Discover top-level dirs relative to src root
    top_dirs = set()
    for f in files:
        rel = f.resolve().relative_to(root_abs)
        stripped = rel.parts[len(src_parts):]
        if stripped: top_dirs.add(stripped[0])
    col_of, layer_meta = assign_columns(sorted(top_dirs))

    nodes = {}
    for f in files:
        fabs      = f.resolve()
        fid       = abs_to_id[fabs]
        rel_parts = Path(fid).parts
        stripped  = rel_parts[len(src_parts):]
        top_dir   = stripped[0] if stripped else "root"
        layer_idx = col_of.get(top_dir, max(col_of.values(), default=0))
        grp = stripped[1] if len(stripped) > 2 else stripped[0] if stripped else "root"
        nodes[fid] = {"id":fid,"label":f.name,"path":fid,"layer":layer_idx,"group":grp}

    seen, edges = set(), []
    for f in files:
        fid = abs_to_id[f.resolve()]
        for dep in get_imports(f, exts, src_root, root_abs):
            if dep in abs_set and dep != f.resolve():
                dep_id = abs_to_id[dep]
                k = (fid, dep_id)
                if k not in seen:
                    seen.add(k)
                    edges.append({"source":fid,"target":dep_id})

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

/* ── top bar ─────────────────────────────────────────────────── */
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

/* ── info panel ─────────────────────────────────────────────── */
#info{background:var(--surface);border-bottom:1px solid var(--border);
  padding:5px 14px;font-size:11px;color:var(--muted);min-height:26px;flex-shrink:0;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#info b{color:var(--text)}
#info .up{color:var(--up-color)}
#info .dn{color:var(--down-color)}

/* ── column focus buttons (below topbar, above canvas) ──────── */
#col-tabs{background:var(--bg);border-bottom:1px solid var(--border);
  padding:4px 14px;display:flex;gap:6px;flex-shrink:0}
.col-tab{background:transparent;border:1px solid var(--border);color:var(--muted);
  padding:3px 10px;border-radius:4px;cursor:pointer;font-family:inherit;font-size:11px;
  transition:all .12s}
.col-tab:hover{border-color:currentColor}

/* ── canvas wrapper ──────────────────────────────────────────── */
#canvas-wrap{flex:1;overflow:hidden;position:relative;cursor:grab;user-select:none}
#canvas-wrap.panning{cursor:grabbing}
svg#graph{display:block;width:100%;height:100%}

/* ── node styling ────────────────────────────────────────────── */
.node-box{stroke-width:1.5px;cursor:pointer}
.node-box:hover{filter:brightness(1.4)}
.node-label{font-size:10.5px;font-family:'SF Mono','Fira Code',Consolas,monospace;
  pointer-events:none;dominant-baseline:middle;fill:#e2e4f0}

/* ── edge styling ────────────────────────────────────────────── */
.edge{fill:none;stroke:#252836;stroke-width:1px;opacity:0.5;marker-end:url(#arr-def)}
.edge.hi-down{stroke:var(--down-color);stroke-width:2px;opacity:1;marker-end:url(#arr-dn)}
.edge.hi-up  {stroke:var(--up-color);  stroke-width:2px;opacity:1;marker-end:url(#arr-up)}
.edge.dim    {opacity:0.04}

/* ── node states ─────────────────────────────────────────────── */
.node-box.search-match{stroke:#facc15!important;stroke-width:2.5px!important}
.node-box.selected    {stroke:#ffffff!important;stroke-width:2.5px!important}
.node-box.hi-connected{stroke:#facc15!important;stroke-width:1.5px!important}

/* ── zoom hint ───────────────────────────────────────────────── */
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
    <button class="tb-btn" id="btn-zoom-out" title="Zoom out (−)">−</button>
    <span id="zoom-level">100%</span>
    <button class="tb-btn" id="btn-zoom-in"  title="Zoom in (+)">+</button>
  </div>
  <button class="tb-btn" id="btn-fit"   title="Fit all columns in view">Fit all</button>
  <button class="tb-btn" id="btn-clear" title="Clear selection (Esc)">Clear</button>
  <div class="legend" id="legend"></div>
</div>

<!-- Column focus tabs — click to zoom into that architectural layer -->
<div id="col-tabs">
  <span style="font-size:10px;color:var(--muted);line-height:24px;margin-right:4px">Jump to column:</span>
</div>

<div id="info">Click any file box → see its imports <span class="dn">↓ blue</span> and who imports it <span class="up">↑ red</span> &nbsp;·&nbsp; Double-click → zoom in on it</div>

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

// ── Layout ─────────────────────────────────────────────────────────────────────
const NODE_W  = 192;
const NODE_H  = 27;
const COL_PAD = 28;
const ROW_GAP = 6;
const GRP_GAP = 20;
const COL_GAP = 100;
const TOP_Y   = 52;

function layoutNodes() {
  const layerGroups = {};
  for (const n of NODES) {
    (layerGroups[n.layer] ??= {})[n.group] ??= [];
    layerGroups[n.layer][n.group].push(n);
  }
  const colW = NODE_W + COL_PAD * 2;
  let colX = 50;
  const positions = {};
  const colMeta = [];

  for (let li = 0; li < LAYER_META.length; li++) {
    const groups = layerGroups[li];
    if (!groups) { colX += colW + COL_GAP; continue; }
    const sortedGrps = Object.keys(groups).sort();
    let y = TOP_Y;
    const grpMeta = [];
    for (const gName of sortedGrps) {
      const ns = groups[gName];
      const grpY = y;
      for (const n of ns) {
        positions[n.id] = { x: colX, y, cx: colX + NODE_W/2, cy: y + NODE_H/2 };
        y += NODE_H + ROW_GAP;
      }
      grpMeta.push({ name: gName, y: grpY, h: y - grpY - ROW_GAP, nodes: ns });
      y += GRP_GAP;
    }
    const colH = y;
    colMeta.push({ li, x: colX, colW, colH, grpMeta });
    colX += colW + COL_GAP;
  }
  return { positions, colMeta, totalW: colX + 20 };
}

// ── Adjacency ─────────────────────────────────────────────────────────────────
const dnOf = {}, upOf = {};
for (const n of NODES) { dnOf[n.id] = []; upOf[n.id] = []; }
for (const e of EDGES) { dnOf[e.source].push(e.target); upOf[e.target].push(e.source); }

const nodeById = Object.fromEntries(NODES.map(n => [n.id, n]));

// ── SVG helpers ───────────────────────────────────────────────────────────────
const NS = "http://www.w3.org/2000/svg";
const mkEl = (tag, attrs) => {
  const e = document.createElementNS(NS, tag);
  if (attrs) for (const [k,v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
};

function bezier(x1,y1,x2,y2) {
  const dx = Math.abs(x2-x1)*0.5;
  return `M${x1},${y1} C${x1+dx},${y1} ${x2-dx},${y2} ${x2},${y2}`;
}

// ── Build DOM ─────────────────────────────────────────────────────────────────
const { positions, colMeta, totalW } = layoutNodes();
const totalH = Math.max(...colMeta.map(c => c.colH)) + 60;

const svg       = document.getElementById('graph');
const panGroup  = document.getElementById('pan-group');
const bandLayer = document.getElementById('layer-bands');
const glLayer   = document.getElementById('group-labels');
const edgeLayer = document.getElementById('edges-layer');
const nodeLayer = document.getElementById('nodes-layer');

// Column bands + titles
const colTabBar = document.getElementById('col-tabs');
for (const col of colMeta) {
  const m = LAYER_META[col.li];

  // background band
  bandLayer.appendChild(mkEl('rect', {
    x: col.x - COL_PAD, y: 16, width: col.colW, height: totalH - 30,
    fill: m.bg, rx: 8
  }));

  // clickable column title
  const titleG = mkEl('g', { style: 'cursor:pointer' });
  titleG.appendChild(mkEl('rect', {
    x: col.x - COL_PAD, y: 16, width: col.colW, height: 30,
    fill: 'transparent', rx: 8
  }));
  const titleT = mkEl('text', {
    x: col.x + NODE_W/2, y: 34, 'text-anchor':'middle',
    fill: m.color, 'font-size':'11.5', 'font-weight':'700',
    'font-family':"'SF Mono','Fira Code',Consolas,monospace"
  });
  titleT.textContent = m.name;
  titleG.appendChild(titleT);
  titleG.addEventListener('click', () => zoomToColumn(col));
  bandLayer.appendChild(titleG);

  // group dividers
  for (let gi = 0; gi < col.grpMeta.length; gi++) {
    const grp = col.grpMeta[gi];
    if (col.grpMeta.length === 1) continue;
    if (gi > 0) {
      const div = mkEl('line', {
        x1: col.x - COL_PAD + 10, y1: grp.y - GRP_GAP/2,
        x2: col.x + NODE_W + COL_PAD - 10, y2: grp.y - GRP_GAP/2,
        stroke: '#1e2235', 'stroke-width': '1'
      });
      glLayer.appendChild(div);
    }
    const gl = mkEl('text', {
      x: col.x, y: grp.y - 3,
      fill: '#333855', 'font-size': '9',
      'font-family': "'SF Mono','Fira Code',Consolas,monospace"
    });
    gl.textContent = grp.name + '/';
    glLayer.appendChild(gl);
  }

  // column focus tab
  const tab = document.createElement('button');
  tab.className = 'col-tab';
  tab.textContent = m.name;
  tab.style.color = m.color;
  tab.style.borderColor = m.color + '55';
  tab.addEventListener('click', () => zoomToColumn(col));
  colTabBar.appendChild(tab);
}

// Edges
const edgeEls = {};
for (const edge of EDGES) {
  const sp = positions[edge.source], tp = positions[edge.target];
  if (!sp || !tp) continue;
  let x1,y1,x2,y2;
  if (sp.cx <= tp.cx) { x1=sp.x+NODE_W; y1=sp.cy; x2=tp.x;       y2=tp.cy; }
  else                { x1=sp.x;        y1=sp.cy; x2=tp.x+NODE_W; y2=tp.cy; }
  const path = mkEl('path', { d: bezier(x1,y1,x2,y2), class:'edge',
    'data-src':edge.source, 'data-tgt':edge.target });
  edgeEls[`${edge.source}::${edge.target}`] = path;
  edgeLayer.appendChild(path);
}

// Nodes
const nodeEls = {};
for (const n of NODES) {
  const pos = positions[n.id];
  if (!pos) continue;
  const m = LAYER_META[n.layer];
  const g = mkEl('g', { class:'node-g', 'data-id':n.id });

  const rect = mkEl('rect', {
    x:pos.x, y:pos.y, width:NODE_W, height:NODE_H,
    class:'node-box', fill:m.bg, stroke:m.color, rx:5
  });
  const maxC = 24;
  const lbl  = n.label.length > maxC ? n.label.slice(0, maxC-1)+'…' : n.label;
  const text = mkEl('text', { x:pos.x+8, y:pos.y+NODE_H/2, class:'node-label' });
  text.textContent = lbl;

  g.appendChild(rect); g.appendChild(text);
  g.addEventListener('click',    () => selectNode(n.id, false));
  g.addEventListener('dblclick', () => selectNode(n.id, true));
  g.addEventListener('mouseenter', () => hoverNode(n));
  g.addEventListener('mouseleave', () => { if (!selected) resetInfo(); });
  nodeEls[n.id] = { g, rect };
  nodeLayer.appendChild(g);
}

// Legend
const legEl = document.getElementById('legend');
for (const m of LAYER_META) {
  const d = document.createElement('div'); d.className='leg-item';
  d.innerHTML=`<div class="leg-dot" style="background:${m.color}"></div>${m.name}`;
  legEl.appendChild(d);
}
for (const [c,l] of [['#4a9eff','↓ imports'],['#f87171','↑ imported by']]) {
  const d=document.createElement('div'); d.className='leg-item';
  d.innerHTML=`<div class="leg-dot" style="background:${c}"></div>${l}`;
  legEl.appendChild(d);
}

// ── Selection + highlighting ───────────────────────────────────────────────────
let selected = null;

function selectNode(id, doZoom) {
  clearSelection();
  selected = id;
  const n  = nodeById[id];
  const dn = dnOf[id]||[], up = upOf[id]||[];

  nodeEls[id]?.rect.classList.add('selected');

  for (const p of edgeLayer.children) p.classList.add('dim');
  for (const t of dn) {
    const k=`${id}::${t}`;
    edgeEls[k]?.classList.remove('dim');
    edgeEls[k]?.classList.add('hi-down');
    nodeEls[t]?.rect.classList.add('hi-connected');
  }
  for (const s of up) {
    const k=`${s}::${id}`;
    edgeEls[k]?.classList.remove('dim');
    edgeEls[k]?.classList.add('hi-up');
    nodeEls[s]?.rect.classList.add('hi-connected');
  }

  setInfo(
    `<b>${esc(n.label)}</b> &nbsp;·&nbsp; ${n.path}` +
    ` &nbsp;·&nbsp; <span class="dn">imports ${dn.length}: ${dn.map(t=>nodeById[t]?.label||t).join(', ')||'—'}</span>` +
    ` &nbsp;·&nbsp; <span class="up">imported by ${up.length}: ${up.map(s=>nodeById[s]?.label||s).join(', ')||'—'}</span>`
  );

  if (doZoom) zoomToNode(id);
}

function clearSelection() {
  if (selected) nodeEls[selected]?.rect.classList.remove('selected');
  selected = null;
  for (const p of edgeLayer.children) p.classList.remove('dim','hi-down','hi-up');
  for (const n of NODES) nodeEls[n.id]?.rect.classList.remove('hi-connected');
  resetInfo();
}

function hoverNode(n) {
  if (!selected) {
    const dn=(dnOf[n.id]||[]).length, up=(upOf[n.id]||[]).length;
    setInfo(`<b>${esc(n.label)}</b> &nbsp;·&nbsp; ${n.path} &nbsp;·&nbsp; imports ${dn} &nbsp;·&nbsp; imported by ${up} &nbsp;·&nbsp; click to highlight · double-click to zoom`);
  }
}

function resetInfo() {
  setInfo('Click any file box → imports <span class="dn">↓ blue</span>, imported by <span class="up">↑ red</span> &nbsp;·&nbsp; Double-click a box to zoom in on it');
}

function setInfo(html) { document.getElementById('info').innerHTML = html; }
function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

document.getElementById('btn-clear').addEventListener('click', clearSelection);
window.addEventListener('keydown', e => { if (e.key==='Escape') clearSelection(); });

// ── Zoom / pan engine ─────────────────────────────────────────────────────────
let vx=0, vy=0, scale=1;
let drag=false, dragSx=0, dragSy=0;
let animRaf=null;

const wrap = document.getElementById('canvas-wrap');

function setTransform(animated) {
  if (animated) {
    panGroup.style.transition = 'transform 0.32s cubic-bezier(0.4,0,0.2,1)';
    setTimeout(() => panGroup.style.transition='', 360);
  } else {
    panGroup.style.transition = 'none';
  }
  panGroup.setAttribute('transform',`translate(${vx.toFixed(2)},${vy.toFixed(2)}) scale(${scale.toFixed(4)})`);
  document.getElementById('zoom-level').textContent = Math.round(scale*100)+'%';
}

function zoomAt(mx, my, factor) {
  const newScale = Math.max(0.15, Math.min(5, scale * factor));
  const ratio    = newScale / scale;
  vx = mx - (mx - vx) * ratio;
  vy = my - (my - vy) * ratio;
  scale = newScale;
  setTransform(false);
}

function animateTo(tvx, tvy, tsc) {
  const sx=vx, sy=vy, ss=scale;
  const t0=performance.now(), dur=340;
  const ease=t=>t<.5?2*t*t:-1+(4-2*t)*t;
  if (animRaf) cancelAnimationFrame(animRaf);
  function step(now) {
    const t=Math.min((now-t0)/dur,1), e=ease(t);
    vx=sx+(tvx-sx)*e; vy=sy+(tvy-sy)*e; scale=ss+(tsc-ss)*e;
    setTransform(false);
    if(t<1) animRaf=requestAnimationFrame(step);
  }
  animRaf=requestAnimationFrame(step);
}

function zoomToRect(wx,wy,ww,wh, pad=40) {
  const r=wrap.getBoundingClientRect();
  const s=Math.min((r.width-pad*2)/ww,(r.height-pad*2)/wh, 2.8);
  const tvx=r.width/2  - (wx+ww/2)*s;
  const tvy=r.height/2 - (wy+wh/2)*s;
  animateTo(tvx,tvy,s);
}

function zoomToNode(id) {
  const pos=positions[id];
  if(!pos) return;
  zoomToRect(pos.x, pos.y, NODE_W, NODE_H, 80);
}

function zoomToColumn(col) {
  const maxH=Math.max(...col.grpMeta.map(g=>g.y+g.h), col.colH);
  zoomToRect(col.x - COL_PAD, TOP_Y-4, col.colW, maxH - TOP_Y + 8);
}

function fitAll() {
  const r=wrap.getBoundingClientRect();
  const s=Math.min(r.width/totalW, r.height/totalH)*0.92;
  const tvx=(r.width -totalW*s)/2;
  const tvy=(r.height-totalH*s)/2;
  animateTo(tvx,tvy,s);
}

// ── Input events ──────────────────────────────────────────────────────────────

wrap.addEventListener('wheel', e => {
  e.preventDefault();
  const r=wrap.getBoundingClientRect();
  const mx=e.clientX-r.left, my=e.clientY-r.top;
  const delta = e.ctrlKey ? e.deltaY * 3 : e.deltaY;
  const factor = Math.exp(-delta * 0.0012);
  zoomAt(mx, my, factor);
}, { passive:false });

let lastPinchDist=null;
wrap.addEventListener('touchstart', e => { if(e.touches.length===2) lastPinchDist=null; },{passive:true});
wrap.addEventListener('touchmove', e => {
  if(e.touches.length!==2){lastPinchDist=null;return;}
  e.preventDefault();
  const a=e.touches[0],b=e.touches[1];
  const dist=Math.hypot(b.clientX-a.clientX,b.clientY-a.clientY);
  if(lastPinchDist){
    const r=wrap.getBoundingClientRect();
    const mx=(a.clientX+b.clientX)/2-r.left, my=(a.clientY+b.clientY)/2-r.top;
    zoomAt(mx,my,dist/lastPinchDist);
  }
  lastPinchDist=dist;
},{passive:false});

wrap.addEventListener('mousedown', e => {
  if(e.target.closest('.node-g')) return;
  drag=true; dragSx=e.clientX-vx; dragSy=e.clientY-vy;
  wrap.classList.add('panning');
});
window.addEventListener('mousemove', e => {
  if(!drag) return;
  vx=e.clientX-dragSx; vy=e.clientY-dragSy;
  setTransform(false);
});
window.addEventListener('mouseup', ()=>{ drag=false; wrap.classList.remove('panning'); });

window.addEventListener('keydown', e => {
  if(e.target===document.getElementById('search')) return;
  const r=wrap.getBoundingClientRect();
  const cx=r.width/2, cy=r.height/2;
  if(e.key==='+'||e.key==='=') { e.preventDefault(); zoomAt(cx,cy,1.15); }
  if(e.key==='-')              { e.preventDefault(); zoomAt(cx,cy,1/1.15); }
  if(e.key==='0')              { e.preventDefault(); fitAll(); }
});

document.getElementById('btn-zoom-in') .addEventListener('click', ()=>{ const r=wrap.getBoundingClientRect(); zoomAt(r.width/2,r.height/2,1.25); });
document.getElementById('btn-zoom-out').addEventListener('click', ()=>{ const r=wrap.getBoundingClientRect(); zoomAt(r.width/2,r.height/2,0.8); });
document.getElementById('btn-fit')     .addEventListener('click', fitAll);

// ── Search ────────────────────────────────────────────────────────────────────
let matchNodes=[], matchIdx=-1;

document.getElementById('search').addEventListener('input', e => {
  const q=e.target.value.trim().toLowerCase();
  matchNodes=[]; matchIdx=-1;
  for(const n of NODES){
    const hit=q&&(n.label.toLowerCase().includes(q)||n.path.toLowerCase().includes(q));
    nodeEls[n.id]?.rect.classList.toggle('search-match',hit);
    if(hit) matchNodes.push(n.id);
  }
  document.getElementById('match-info').textContent=
    q?`${matchNodes.length} match${matchNodes.length!==1?'es':''}` :'';
  if(matchNodes.length){ matchIdx=0; panToNode(matchNodes[0]); }
});

document.getElementById('search').addEventListener('keydown', e => {
  if(!matchNodes.length) return;
  if(e.key==='ArrowDown'){e.preventDefault();matchIdx=(matchIdx+1)%matchNodes.length;panToNode(matchNodes[matchIdx]);}
  if(e.key==='ArrowUp')  {e.preventDefault();matchIdx=(matchIdx-1+matchNodes.length)%matchNodes.length;panToNode(matchNodes[matchIdx]);}
  if(e.key==='Enter')    {if(matchNodes[matchIdx]) selectNode(matchNodes[matchIdx], true);}
  if(e.key==='Escape')   {e.target.value='';for(const n of NODES) nodeEls[n.id]?.rect.classList.remove('search-match');document.getElementById('match-info').textContent='';}
});

function panToNode(id) {
  const pos=positions[id]; if(!pos) return;
  const r=wrap.getBoundingClientRect();
  animateTo(r.width/2 - pos.cx*scale, r.height/2 - pos.cy*scale, scale);
}

// ── Boot: fit all on load ─────────────────────────────────────────────────────
window.addEventListener('load', ()=>{ setTimeout(fitAll, 60); });
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
