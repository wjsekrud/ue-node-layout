#!/usr/bin/env python3
"""
ue_layout.py - auto-layout for Unreal Engine node graphs (Blueprint, Material, ...).

Subcommands
  json  IN.json  [-o OUT.json]   generic graph JSON  -> positions JSON   (MCP / pre-planning)
  t3d   IN.txt   [-o OUT.txt]    copied-node text (Ctrl+C in the editor) -> same text, re-laid-out
  clip                           read the clipboard, re-layout, write it back (backup kept)

Common options
  --comments      wrap each event/root chain in a comment box (t3d/clip: adds comment nodes)
  --svg FILE      write an SVG preview of the result (for checking before applying)
  --origin X Y    top-left of the result (default: keep the original top-left)
  --spacing F     scale all gaps (default 1.0; 1.3 = roomier)

Layout rules (what "readable" means here)
  * Execution flow runs left -> right on one row.
  * A node's first exec output continues on the same row; other outputs
    (Else, Completed, Then 1, ...) start new rows below, never overlapping.
  * Pure/data nodes sit to the LEFT of the node that consumes them, below the
    exec wire, as a right-to-left tree in pin order.
  * Each event (root) chain is stacked vertically with a larger gap.
  * Graphs without exec pins (Material etc.) are laid out right-to-left from
    their sink nodes (e.g. the material output).
  * Everything snaps to the 16px grid.
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import tempfile

GRID = 16

# Base gaps (scaled by --spacing)
GAPS = dict(
    exec_x=80,      # between consecutive exec nodes
    data_x=48,      # between a data node and the node it feeds
    data_y=24,      # between stacked data subtrees
    row=64,         # between a branch row and what is above it
    root=128,       # between event chains
    data_top=72,    # data block starts this far below its consumer's top (keeps exec wire clear)
)


def snap(v):
    return int(round(v / GRID)) * GRID


# ----------------------------------------------------------------------------
# Graph model
# ----------------------------------------------------------------------------
class Pin:
    __slots__ = ("key", "name", "dir", "kind", "hidden", "linked", "order")

    def __init__(self, key, name, direction, kind, hidden=False, order=0):
        self.key = key
        self.name = name or ""
        self.dir = direction      # 'in' | 'out'
        self.kind = kind          # 'exec' | 'data'
        self.hidden = hidden
        self.linked = False
        self.order = order


class Node:
    def __init__(self, nid, title="", x=0, y=0, w=None, h=None, compact=False, comment=False):
        self.id = nid
        self.title = title or ""
        self.ox, self.oy = x or 0, y or 0      # original position
        self.x, self.y = None, None            # new position
        self.w, self.h = w, h
        self.compact = compact
        self.comment = comment
        self.pins = []

    @property
    def is_exec(self):
        return any(p.kind == "exec" for p in self.pins)


class Graph:
    def __init__(self):
        self.nodes = {}       # id -> Node
        self.order = []       # insertion order
        self.edges = set()    # (src_id, src_pin_key, dst_id, dst_pin_key, kind)

    def add_node(self, n):
        self.nodes[n.id] = n
        self.order.append(n.id)

    def add_edge(self, src, sp, dst, dp, kind):
        if src in self.nodes and dst in self.nodes and src != dst:
            self.edges.add((src, sp, dst, dp, kind))

    def finalize(self):
        """Mark linked pins and build ordered adjacency."""
        pin_index = {}
        for n in self.nodes.values():
            for p in n.pins:
                pin_index[(n.id, p.key)] = p
        for (s, sp, d, dp, k) in self.edges:
            for key in ((s, sp), (d, dp)):
                if key in pin_index:
                    pin_index[key].linked = True

        def pin_order(nid, key):
            p = pin_index.get((nid, key))
            return p.order if p else 10_000

        self.exec_out = {nid: [] for nid in self.nodes}
        self.exec_in = {nid: [] for nid in self.nodes}
        self.data_in = {nid: [] for nid in self.nodes}
        self.data_out = {nid: [] for nid in self.nodes}
        for (s, sp, d, dp, k) in sorted(
            self.edges, key=lambda e: (pin_order(e[0], e[1]), pin_order(e[2], e[3]), e[2])
        ):
            if k == "exec":
                self.exec_out[s].append((pin_order(s, sp), d))
                self.exec_in[d].append(s)
            else:
                self.data_in[d].append((pin_order(d, dp), s))
                self.data_out[s].append(d)
        for nid in self.nodes:
            self.exec_out[nid] = _uniq([d for _, d in sorted(self.exec_out[nid], key=lambda t: t[0])])
            self.data_in[nid] = _uniq([s for _, s in sorted(self.data_in[nid], key=lambda t: t[0])])


def _uniq(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ----------------------------------------------------------------------------
# Size estimation (UE does not store node sizes; overestimate a little)
# ----------------------------------------------------------------------------
_SILENT_EXEC = {"execute", "then", "exec", ""}


def _label(p):
    if p.kind == "exec" and p.name.lower() in _SILENT_EXEC:
        return ""
    return p.name


def estimate_size(n):
    if n.w and n.h:
        return
    vis = [p for p in n.pins if not p.hidden]
    ins = [p for p in vis if p.dir == "in"]
    outs = [p for p in vis if p.dir == "out"]
    rows = max(len(ins), len(outs), 1)

    def side_w(pins, is_input):
        best = 0
        for p in pins:
            w = len(_label(p)) * 7 + 36
            if is_input and p.kind == "data" and not p.linked:
                w += 56  # inline default-value box
            best = max(best, w)
        return best

    body_w = side_w(ins, True) + side_w(outs, False) + 32
    if n.compact:
        w = max(body_w, len(n.title) * 7 + 48, 96)
        h = rows * 24 + 16
    else:
        title_w = len(n.title) * 8 + 72
        w = max(title_w, body_w, 160)
        h = 40 + rows * 26 + 12
    n.w = n.w or snap(w + GRID / 2)
    n.h = n.h or snap(h + GRID / 2)


# ----------------------------------------------------------------------------
# Layout
# ----------------------------------------------------------------------------
class _Tree:
    __slots__ = ("node", "kids", "W", "H")


def layout(g, gaps=None, comments=False):
    gp = dict(GAPS)
    if gaps:
        gp.update(gaps)
    if comments:
        gp["root"] = max(gp["root"], 192)
    g.finalize()
    nodes = [g.nodes[i] for i in g.order if not g.nodes[i].comment]
    for n in nodes:
        estimate_size(n)
    ids = {n.id for n in nodes}

    claimed = set()   # data nodes already assigned a place
    visited = set()   # exec nodes already placed
    groups = []       # (title, [node ids]) per root chain

    def is_pure(nid):
        return nid in ids and not g.nodes[nid].is_exec

    def build_tree(nid):
        t = _Tree()
        t.node = g.nodes[nid]
        t.kids = []
        for s in g.data_in[nid]:
            if is_pure(s) and s not in claimed:
                claimed.add(s)
                t.kids.append(build_tree(s))
        kid_w = max((k.W for k in t.kids), default=0)
        kid_h = sum(k.H for k in t.kids) + gp["data_y"] * max(len(t.kids) - 1, 0)
        t.W = t.node.w + (gp["data_x"] + kid_w if t.kids else 0)
        t.H = max(t.node.h, kid_h)
        return t

    def place_tree(t, right, top, bucket):
        n = t.node
        n.x, n.y = right - n.w, top
        bucket.append(n.id)
        cy = top
        for k in t.kids:
            place_tree(k, n.x - gp["data_x"], cy, bucket)
            cy += k.H + gp["data_y"]

    def input_trees(nid):
        trees = []
        for s in g.data_in[nid]:
            if is_pure(s) and s not in claimed:
                claimed.add(s)
                trees.append(build_tree(s))
        return trees

    def place_exec(nid, x, y, bucket):
        visited.add(nid)
        n = g.nodes[nid]
        trees = input_trees(nid)
        dw = max((t.W for t in trees), default=0)
        nx = x + (dw + gp["data_x"] if trees else 0)
        n.x, n.y = nx, y
        bucket.append(nid)
        bottom = y + n.h
        cy = y + gp["data_top"]
        for t in trees:
            place_tree(t, nx - gp["data_x"], cy, bucket)
            cy += t.H + gp["data_y"]
            bottom = max(bottom, cy - gp["data_y"])
        next_x = nx + n.w + gp["exec_x"]
        first = True
        for s in g.exec_out[nid]:
            if s in visited or s not in ids:
                continue
            if first:
                b = place_exec(s, next_x, y, bucket)
                first = False
            else:
                b = place_exec(s, next_x, bottom + gp["row"], bucket)
            bottom = max(bottom, b)
        return bottom

    by_orig = lambda n: (n.oy, n.ox)
    cursor_y = 0

    # 1) exec chains, one per root (events, custom events, function entry, ...)
    exec_nodes = sorted([n for n in nodes if n.is_exec], key=by_orig)
    roots = [n for n in exec_nodes if not any(p in ids for p in g.exec_in[n.id])]
    pending = roots + [n for n in exec_nodes if n not in roots]
    for r in pending:
        if r.id in visited:
            continue
        bucket = []
        bottom = place_exec(r.id, 0, cursor_y, bucket)
        _normalize_left(g, bucket)
        groups.append((r.title, bucket))
        cursor_y = bottom + gp["root"]

    # 2) data-only graphs / leftovers: trees from sinks, right-aligned
    rest = [n for n in nodes if not n.is_exec and n.id not in claimed]
    while rest:
        rest_ids = {n.id for n in rest}
        sinks = [n for n in rest if not any(d in rest_ids for d in g.data_out[n.id])]
        if not sinks:              # cycle: just take the first
            sinks = rest[:1]
        sinks.sort(key=by_orig)
        bucket = []
        for s in sinks:
            if s.id in claimed:
                continue
            claimed.add(s.id)
            t = build_tree(s.id)
            place_tree(t, 0, cursor_y, bucket)
            cursor_y += t.H + gp["row"]
        _normalize_left(g, bucket)
        groups.append(("", bucket))
        cursor_y += gp["root"] - gp["row"]
        rest = [n for n in nodes if not n.is_exec and n.id not in claimed]

    # 3) snap + move to requested origin
    for n in nodes:
        n.x, n.y = snap(n.x), snap(n.y)
    return groups


def _normalize_left(g, bucket):
    """Shift a chain so its left-most node sits at x=0."""
    if not bucket:
        return
    mn = min(g.nodes[i].x for i in bucket)
    for i in bucket:
        g.nodes[i].x -= mn


def apply_origin(g, origin):
    ns = [n for n in g.nodes.values() if not n.comment and n.x is not None]
    if not ns:
        return
    if origin is None:
        origin = (min(n.ox for n in ns), min(n.oy for n in ns))
    dx = origin[0] - min(n.x for n in ns)
    dy = origin[1] - min(n.y for n in ns)
    for n in ns:
        n.x, n.y = snap(n.x + dx), snap(n.y + dy)


def comment_boxes(g, groups):
    boxes = []
    for title, bucket in groups:
        if not bucket:
            continue
        ns = [g.nodes[i] for i in bucket]
        x0 = min(n.x for n in ns) - 32
        y0 = min(n.y for n in ns) - 64
        x1 = max(n.x + n.w for n in ns) + 32
        y1 = max(n.y + n.h for n in ns) + 32
        boxes.append(dict(title=title or "Group", x=snap(x0), y=snap(y0),
                          w=snap(x1 - x0), h=snap(y1 - y0)))
    return boxes


# ----------------------------------------------------------------------------
# JSON adapter
# ----------------------------------------------------------------------------
def graph_from_json(data):
    """
    {"nodes":[{"id","title","x","y","w"?,"h"?,"compact"?,"comment"?,
               "pins":[{"name","dir":"in|out","kind":"exec|data","hidden"?,
                        "links":[{"node","pin"}]?}]}],
     "edges":[{"from","fromPin","to","toPin","kind":"exec|data"}]   # optional
    }
    """
    g = Graph()
    for nd in data.get("nodes", []):
        n = Node(str(nd["id"]), nd.get("title", ""), nd.get("x", 0), nd.get("y", 0),
                 nd.get("w"), nd.get("h"), nd.get("compact", False), nd.get("comment", False))
        for i, pd in enumerate(nd.get("pins", [])):
            kind = "exec" if str(pd.get("kind", "data")).lower() == "exec" else "data"
            d = "out" if str(pd.get("dir", "in")).lower().startswith("o") else "in"
            n.pins.append(Pin(pd.get("name", str(i)), pd.get("name", ""), d, kind,
                              pd.get("hidden", False), i))
        g.add_node(n)
    pin_kind = {(n.id, p.key): p.kind for n in g.nodes.values() for p in n.pins}
    pin_dir = {(n.id, p.key): p.dir for n in g.nodes.values() for p in n.pins}
    for nd in data.get("nodes", []):
        nid = str(nd["id"])
        for pd in nd.get("pins", []):
            for ln in pd.get("links", []) or []:
                other, op = str(ln["node"]), ln.get("pin", "")
                k = "exec" if "exec" in (pin_kind.get((nid, pd.get("name"))),
                                         pin_kind.get((other, op))) else "data"
                if pin_dir.get((nid, pd.get("name"))) == "out":
                    g.add_edge(nid, pd.get("name"), other, op, k)
                else:
                    g.add_edge(other, op, nid, pd.get("name"), k)
    for e in data.get("edges", []) or []:
        s, sp, d, dp = str(e["from"]), e.get("fromPin", ""), str(e["to"]), e.get("toPin", "")
        k = e.get("kind")
        if not k:
            k = "exec" if "exec" in (pin_kind.get((s, sp)), pin_kind.get((d, dp))) else "data"
        # Undeclared pins are created in edge order, so pin order (= vertical order) is kept.
        for nid, key, dr in ((s, sp, "out"), (d, dp, "in")):
            if nid in g.nodes and (nid, key) not in pin_kind:
                g.nodes[nid].pins.append(Pin(key, key, dr, k, False, len(g.nodes[nid].pins)))
                pin_kind[(nid, key)] = k
        g.add_edge(s, sp, d, dp, k)
    return g


# ----------------------------------------------------------------------------
# T3D (clipboard text) adapter
# ----------------------------------------------------------------------------
_RE_CLASS = re.compile(r'Class=([^\s]+)')
_RE_NAME = re.compile(r'Name="([^"]+)"')
_RE_POS = re.compile(r'^(\s*)(NodePosX|NodePosY)=(-?\d+)')
_RE_MPOS = re.compile(r'^(\s*)(MaterialExpressionEditorX|MaterialExpressionEditorY)=(-?\d+)')
_PIN_FIELDS = {
    "id": re.compile(r'PinId=([0-9A-Fa-f]+)'),
    "name": re.compile(r'PinName="((?:[^"\\]|\\.)*)"'),
    "friendly": re.compile(r'PinFriendlyName=(?:NSLOCTEXT\([^,]*,\s*[^,]*,\s*"((?:[^"\\]|\\.)*)"\)|INVTEXT\("((?:[^"\\]|\\.)*)"\)|"((?:[^"\\]|\\.)*)")'),
    "cat": re.compile(r'PinType\.PinCategory="(\w*)"'),
    "dir": re.compile(r'Direction="(\w+)"'),
    "linked": re.compile(r'LinkedTo=\(([^)]*)\)'),
    "hidden": re.compile(r'bHidden=True'),
}
_TITLE_KEYS = [re.compile(r'\bMemberName="([^"]+)"'),
               re.compile(r'\bCustomFunctionName="([^"]+)"'),
               re.compile(r'\bFunctionDisplayName="([^"]+)"'),
               re.compile(r'\bInputName="([^"]+)"'),
               re.compile(r'\bParameterName="([^"]+)"')]
_COMPACT = ("K2Node_VariableGet", "K2Node_Self", "K2Node_Literal")


class T3DObject:
    def __init__(self, start):
        self.start = start
        self.end = None
        self.cls = ""
        self.name = ""
        self.inner_cls = ""
        self.body = []   # (line_index, depth) for lines inside the object


def parse_t3d(text):
    lines = text.splitlines()
    objs, depth, cur = [], 0, None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("Begin Object"):
            depth += 1
            if depth == 1:
                cur = T3DObject(i)
                m = _RE_CLASS.search(s)
                cur.cls = m.group(1) if m else ""
                m = _RE_NAME.search(s)
                cur.name = m.group(1) if m else f"obj{i}"
            elif cur is not None:
                cur.body.append((i, depth))
                m = _RE_CLASS.search(s)
                if m and not cur.inner_cls:
                    cur.inner_cls = m.group(1)
        elif s.startswith("End Object"):
            if depth == 1 and cur is not None:
                cur.end = i
                objs.append(cur)
                cur = None
            elif cur is not None:
                cur.body.append((i, depth))
            depth = max(depth - 1, 0)
        elif cur is not None:
            cur.body.append((i, depth))
    return lines, objs


def _short(cls):
    c = cls.split(".")[-1].split("'")[0]
    for pre in ("K2Node_", "MaterialExpression", "MaterialGraphNode_", "EdGraphNode_"):
        if c.startswith(pre):
            c = c[len(pre):]
    return c


def graph_from_t3d(lines, objs):
    g = Graph()
    pending_links = []
    for o in objs:
        body = [lines[i] for i, d in o.body if d == 1]
        all_body = [lines[i] for i, _ in o.body]
        ox = oy = 0
        for l in body:
            m = _RE_POS.match(l)
            if m:
                if m.group(2) == "NodePosX":
                    ox = int(m.group(3))
                else:
                    oy = int(m.group(3))
        title = ""
        joined = "\n".join(all_body)
        for rx in _TITLE_KEYS:
            m = rx.search(joined)
            if m:
                title = m.group(1)
                break
        if not title:
            title = _short(o.inner_cls or o.cls)
        is_comment = "Comment" in o.cls
        compact = any(k in o.cls for k in _COMPACT)
        n = Node(o.name, title, ox, oy, compact=compact, comment=is_comment)
        if "K2Node_Knot" in o.cls:
            n.w, n.h = 48, 32
        order = 0
        for l in body:
            s = l.strip()
            if not s.startswith("CustomProperties Pin"):
                continue
            f = {k: rx.search(s) for k, rx in _PIN_FIELDS.items()}
            if not f["id"]:
                continue
            pid = f["id"].group(1)
            name = f["name"].group(1) if f["name"] else ""
            if f["friendly"]:
                fr = next((x for x in f["friendly"].groups() if x), "")
                name = fr or name
            cat = f["cat"].group(1) if f["cat"] else ""
            d = "out" if (f["dir"] and f["dir"].group(1) == "EGPD_Output") else "in"
            kind = "exec" if cat == "exec" else "data"
            n.pins.append(Pin(pid, name, d, kind, bool(f["hidden"]), order))
            order += 1
            if f["linked"]:
                for ent in f["linked"].group(1).split(","):
                    ent = ent.strip()
                    if not ent:
                        continue
                    parts = ent.split()
                    if len(parts) >= 2:
                        pending_links.append((o.name, pid, d, kind, parts[0], parts[1]))
        g.add_node(n)
    pin_kind = {(n.id, p.key): p.kind for n in g.nodes.values() for p in n.pins}
    for (nid, pid, d, kind, other, opid) in pending_links:
        k = "exec" if "exec" in (kind, pin_kind.get((other, opid))) else "data"
        if d == "out":
            g.add_edge(nid, pid, other, opid, k)
        else:
            g.add_edge(other, opid, nid, pid, k)
    return g


def _guid():
    return "%032X" % random.getrandbits(128)


def write_t3d(lines, objs, g, boxes=None, newline="\r\n"):
    by_name = {o.name: o for o in objs}
    out = list(lines)
    inserts = {}     # line index -> list of lines to insert before it
    removes = set()
    is_material = any("MaterialGraphNode" in o.cls for o in objs)
    for o in objs:
        n = g.nodes.get(o.name)
        if n is None or n.comment or n.x is None:
            continue
        ind = None
        for i, d in o.body:
            if d == 1:
                m = _RE_POS.match(lines[i])
                if m:
                    removes.add(i)
                    ind = m.group(1)
                elif ind is None and lines[i].strip():
                    ind = re.match(r"^(\s*)", lines[i]).group(1)
        ind = ind if ind is not None else "   "
        inserts.setdefault(o.end, []).extend([f"{ind}NodePosX={n.x}", f"{ind}NodePosY={n.y}"])
        if is_material:
            # Material expressions keep their own editor position; update it too.
            sub_start, sub_has = None, False
            for i, d in o.body:
                s = lines[i].strip()
                if d == 2 and s.startswith("Begin Object") and "Class=" not in s:
                    sub_start, sub_has = i, False
                elif d == 2 and s.startswith("End Object") and sub_start is not None:
                    sind = ind + "   "
                    inserts.setdefault(i, []).extend(
                        [f"{sind}MaterialExpressionEditorX={n.x}", f"{sind}MaterialExpressionEditorY={n.y}"])
                    sub_start = None
                elif sub_start is not None and _RE_MPOS.match(lines[i]):
                    removes.add(i)
    result = []
    for i, l in enumerate(out):
        if i in inserts:
            result.extend(inserts[i])
        if i not in removes:
            result.append(l)
    while result and not result[-1].strip():
        result.pop()
    if boxes and not is_material:
        used = {o.name for o in objs}
        k = 0
        for b in boxes:
            while f"EdGraphNode_Comment_{k}" in used:
                k += 1
            name = f"EdGraphNode_Comment_{k}"
            used.add(name)
            title = b["title"].replace("\\", "\\\\").replace('"', '\\"')
            result += [
                f'Begin Object Class=/Script/UnrealEd.EdGraphNode_Comment Name="{name}"',
                f"   NodePosX={b['x']}",
                f"   NodePosY={b['y']}",
                f"   NodeWidth={b['w']}",
                f"   NodeHeight={b['h']}",
                f'   NodeComment="{title}"',
                f"   NodeGuid={_guid()}",
                "End Object",
            ]
    return newline.join(result) + newline


# ----------------------------------------------------------------------------
# SVG preview
# ----------------------------------------------------------------------------
def write_svg(g, path, boxes=None):
    ns = [n for n in g.nodes.values() if not n.comment and n.x is not None]
    if not ns:
        return
    x0 = min(n.x for n in ns) - 80
    y0 = min(n.y for n in ns) - 100
    x1 = max(n.x + n.w for n in ns) + 80
    y1 = max(n.y + n.h for n in ns) + 80
    pin_pos = {}
    for n in ns:
        for side in ("in", "out"):
            vis = [p for p in n.pins if p.dir == side and not p.hidden]
            for j, p in enumerate(vis):
                px = n.x if side == "in" else n.x + n.w
                py = n.y + (12 if n.compact else 40) + j * (24 if n.compact else 26) + 12
                pin_pos[(n.id, p.key)] = (px, py)
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0} {y0} {x1-x0} {y1-y0}" '
             f'width="{(x1-x0)//2}" height="{(y1-y0)//2}" font-family="Segoe UI,Arial" font-size="13">',
             f'<rect x="{x0}" y="{y0}" width="{x1-x0}" height="{y1-y0}" fill="#262626"/>']
    for b in boxes or []:
        parts.append(f'<rect x="{b["x"]}" y="{b["y"]}" width="{b["w"]}" height="{b["h"]}" '
                     f'fill="#ffffff10" stroke="#888" rx="4"/>'
                     f'<text x="{b["x"]+12}" y="{b["y"]+28}" fill="#ddd" font-size="20">{esc(b["title"])}</text>')
    for (s, sp, d, dp, k) in g.edges:
        if (s, sp) not in pin_pos or (d, dp) not in pin_pos:
            continue
        (ax, ay), (bx, by) = pin_pos[(s, sp)], pin_pos[(d, dp)]
        c = max(abs(bx - ax) / 2, 60)
        col = "#ffffff" if k == "exec" else "#5fb3ff"
        wdt = 3 if k == "exec" else 2
        parts.append(f'<path d="M{ax},{ay} C{ax+c},{ay} {bx-c},{by} {bx},{by}" '
                     f'stroke="{col}" stroke-width="{wdt}" fill="none" opacity="0.85"/>')
    for n in ns:
        head = "#4a2d6b" if n.is_exec else "#2d5a3a"
        parts.append(f'<rect x="{n.x}" y="{n.y}" width="{n.w}" height="{n.h}" rx="6" '
                     f'fill="#151515" stroke="#000" stroke-width="2"/>')
        if not n.compact:
            parts.append(f'<rect x="{n.x}" y="{n.y}" width="{n.w}" height="32" rx="6" fill="{head}"/>')
        parts.append(f'<text x="{n.x+10}" y="{n.y+21}" fill="#fff" font-weight="bold">{esc(n.title)}</text>')
    parts.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


# ----------------------------------------------------------------------------
# Clipboard
# ----------------------------------------------------------------------------
def clip_read():
    if sys.platform == "win32":
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw"],
            capture_output=True)
        return r.stdout.decode("utf-8", "replace")
    if sys.platform == "darwin":
        return subprocess.run(["pbpaste"], capture_output=True).stdout.decode("utf-8", "replace")
    return subprocess.run(["xclip", "-o", "-selection", "clipboard"], capture_output=True).stdout.decode()


def clip_write(text):
    if sys.platform == "win32":
        fd, tmp = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Set-Clipboard -Value (Get-Content -Raw -Encoding UTF8 -LiteralPath '{tmp}')"],
                       check=True)
        os.remove(tmp)
    elif sys.platform == "darwin":
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
    else:
        subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode("utf-8"), check=True)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def _gaps(scale):
    return {k: snap(v * scale) for k, v in GAPS.items()}


def _summary(g, groups):
    ns = [n for n in g.nodes.values() if not n.comment]
    ex = sum(1 for n in ns if n.is_exec)
    return (f"nodes={len(ns)} (exec={ex}, data={len(ns)-ex}) edges={len(g.edges)} "
            f"chains={len(groups)}")


def run_t3d(text, args):
    newline = "\r\n" if "\r\n" in text else "\n"
    lines, objs = parse_t3d(text)
    if not objs:
        raise SystemExit("No 'Begin Object' blocks found - is this copied node text?")
    g = graph_from_t3d(lines, objs)
    groups = layout(g, _gaps(args.spacing), args.comments)
    apply_origin(g, tuple(args.origin) if args.origin else None)
    boxes = comment_boxes(g, groups) if args.comments else None
    if args.svg:
        write_svg(g, args.svg, boxes)
    return write_t3d(lines, objs, g, boxes, newline), g, groups


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["json", "t3d", "clip"])
    ap.add_argument("input", nargs="?")
    ap.add_argument("-o", "--output")
    ap.add_argument("--comments", action="store_true")
    ap.add_argument("--svg")
    ap.add_argument("--origin", nargs=2, type=int)
    ap.add_argument("--spacing", type=float, default=1.0)
    args = ap.parse_args(argv)

    if args.mode == "json":
        with open(args.input, encoding="utf-8-sig") as f:
            data = json.load(f)
        g = graph_from_json(data)
        groups = layout(g, _gaps(args.spacing), args.comments)
        apply_origin(g, tuple(args.origin) if args.origin else None)
        boxes = comment_boxes(g, groups) if args.comments else []
        if args.svg:
            write_svg(g, args.svg, boxes)
        res = {
            "positions": {n.id: {"x": n.x, "y": n.y, "w": n.w, "h": n.h}
                          for n in g.nodes.values() if not n.comment and n.x is not None},
            "comments": boxes,
            "summary": _summary(g, groups),
        }
        s = json.dumps(res, ensure_ascii=False, indent=1)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(s)
            print(res["summary"])
        else:
            print(s)
        return

    if args.mode == "t3d":
        with open(args.input, encoding="utf-8-sig", newline="") as f:
            text = f.read()
        out, g, groups = run_t3d(text, args)
        if args.output:
            with open(args.output, "w", encoding="utf-8", newline="") as f:
                f.write(out)
        else:
            sys.stdout.write(out)
        print(_summary(g, groups), file=sys.stderr)
        return

    # clip
    text = clip_read()
    if "Begin Object" not in text:
        raise SystemExit("Clipboard has no copied nodes. Select nodes in the editor and press Ctrl+C (or Ctrl+X) first.")
    backup = os.path.join(tempfile.gettempdir(), "ue_layout_clipboard_backup.txt")
    with open(backup, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    out, g, groups = run_t3d(text, args)
    clip_write(out)
    print(_summary(g, groups))
    print(f"backup of original clipboard: {backup}")
    print("Clipboard updated. In the editor: Ctrl+V to paste the organized nodes.")


if __name__ == "__main__":
    main()
