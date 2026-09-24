"""Everything the tracker writes: STATUS.txt, tree.txt, badge.json, CANVAS.txt,
the README blocks, and docs/data.json (the Pages site's data, and the next
run's memory of which links existed)."""

from __future__ import annotations

import json
import re
from typing import Dict, List, Tuple

from . import chain, config
from .network import Node, Tree, parse_time

TIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} UTC")
GENERATED_RE = re.compile(r'"generated_at": "[^"]*"')
STALL_TEXT = "CHAIN STALLED - start a side branch and overtake."
TAGS = {"root": "", "valid": "", "invalid": "INVALID", "excluded": "EXCLUDED",
        "lost": "LOST", "unverified": "UNVERIFIED"}


def count(n: int, one: str, many: str) -> str:
    return "%d %s" % (n, one if n == 1 else many)


def summary_line(tree: Tree) -> str:
    return ", ".join([
        "depth %d" % tree.depth,
        count(tree.total_links, "link", "links"),
        count(tree.side_branches, "side branch", "side branches"),
        count(len(tree.pending), "pending fork", "pending forks"),
    ])


def stamp(now: str) -> str:
    return parse_time(now).strftime("%Y-%m-%d %H:%M UTC")


def mask(text: str) -> str:
    """Text with its check timestamps blanked, for spotting real changes."""
    return GENERATED_RE.sub('"generated_at": ""', TIME_RE.sub("<time>", text))


def next_prefix(tree: Tree) -> str:
    tip = tree.tip_node
    return "%d | <your-username> | <YYYY-MM-DD> | %s |" % (tip.depth + 1, chain.line_hash(tip.text))


def status_txt(tree: Tree, stalled: bool, now: str) -> str:
    rows = [
        "depth:         %d" % tree.depth,
        "tip:           %s" % tree.tip_node.repo.url,
        "total links:   %d" % tree.total_links,
        "side branches: %d" % tree.side_branches,
        "last checked:  %s" % stamp(now),
    ]
    if stalled:
        rows.append(STALL_TEXT)
    rows += ["", "next line:     %s <x,y=c or -> | <note>" % next_prefix(tree)]
    return "\n".join(rows) + "\n"


def badge_json(tree: Tree, stalled: bool) -> str:
    badge = {"schemaVersion": 1, "label": "chain depth",
             "message": "%d%s" % (tree.depth, " (stalled)" if stalled else ""),
             "color": "orange" if stalled else "blue"}
    return json.dumps(badge) + "\n"


def canvas_rows(tree: Tree) -> List[str]:
    return chain.canvas_rows(tree.cells_to(tree.tip))


def canvas_txt(tree: Tree) -> str:
    return "\n".join(canvas_rows(tree)) + "\n"


# tree.txt

def tree_txt(tree: Tree, now: str) -> str:
    nodes = tree.nodes
    out = [
        "the-long-fork: chain tree",
        "last checked: " + stamp(now),
        "",
        summary_line(tree),
        "* marks the main chain. Links that don't count say why (-); notes on style start with ~.",
        "",
        "MAIN CHAIN",
    ]
    for node_id in tree.main:
        out += _rows(tree, nodes[node_id], "*")
    for number, path in enumerate(tree.branches, 1):
        start = nodes[nodes[path[0]].parent]
        out += ["", "SIDE BRANCH %d, off depth %d (%s)" % (number, start.depth, chain.safe_user(start.owner))]
        for node_id in path:
            out += _rows(tree, nodes[node_id], " ")
    if tree.pending:
        out += ["", "PENDING (forked, no line yet)"]
        for repo, node_id in tree.pending:
            at = nodes[node_id]
            out.append("        %-20s forked off depth %d (%s) on %s"
                       % (repo.owner, at.depth, chain.safe_user(at.owner), repo.created_at[:10]))
    if tree.problems:
        out += ["", "NOT LINKS"]
        for repo, why in tree.problems:
            out.append("        %-20s %s" % (repo.owner, chain.safe_text(why)))
    return "\n".join(out) + "\n"


def _rows(tree: Tree, node: Node, mark: str) -> List[str]:
    tag = TAGS[node.status]
    if node.id == tree.tip:
        tag = (tag + " <== TIP").strip()
    note = '"%s"' % chain.safe_text(node.line.note) if node.line.note else ""
    row = "%s %6d  %-20s %-10s  %-8s %s" % (
        mark, node.depth, chain.safe_user(node.owner), chain.safe_text(node.line.date)[:10] or "?",
        node.line.cell or "-", " ".join(x for x in (note, tag) if x))
    rows = [row.rstrip()]
    if node.status not in ("valid", "root"):
        rows += ["           - " + chain.safe_text(r) for r in node.reasons]
    rows += ["           ~ " + chain.safe_text(w) for w in node.warnings]
    return rows


# README blocks

def replace_block(text: str, name: str, body: str) -> str:
    start, end = "<!-- %s START -->" % name, "<!-- %s END -->" % name
    i, j = text.find(start), text.find(end)
    if i < 0 or j < i:
        return text
    return text[: i + len(start)] + "\n" + body.strip("\n") + "\n" + text[j:]


def readme(text: str, tree: Tree, stalled: bool, milestones: List[dict]) -> str:
    text = replace_block(text, "TIP", tip_block(tree, stalled))
    text = replace_block(text, "CANVAS", canvas_block(tree))
    text = replace_block(text, "TREE", mermaid(tree))
    return replace_block(text, "MILESTONES", milestones_block(milestones))


def tip_block(tree: Tree, stalled: bool) -> str:
    tip = tree.tip_node
    out = []
    if stalled:
        out += ["> [!WARNING]",
                "> **CHAIN STALLED** - start a side branch and overtake. No new link since %s."
                % tip.linked_at[:10], ""]
    if tip.status == "root":
        out.append("**No links yet.** Fork this repo to be link 1.")
    else:
        out.append("**Current tip:** [@%s](%s) at depth **%d** · %s · %s"
                   % (tip.owner, tip.repo.url, tree.depth,
                      count(tree.total_links, "link", "links"),
                      count(tree.side_branches, "side branch", "side branches")))
    out += ["", "**Fork the tip:** <%s/fork>" % tip.repo.url, "",
            "Your line starts with `%s`" % next_prefix(tree)]
    return "\n".join(out)


def canvas_block(tree: Tree) -> str:
    rows = canvas_rows(tree)
    runs = [len(r) for row in rows for r in re.findall(r"`+", row)]
    fence = "`" * max(3, max(runs, default=0) + 1)
    filled = sum(1 for row in rows for ch in row if ch != config.CANVAS_BLANK)
    return "\n".join(["%d of %d cells set on the main chain." % (filled, config.CANVAS_W * config.CANVAS_H),
                      "", fence + "text"] + rows + [fence])


def milestones_block(milestones: List[dict]) -> str:
    if not milestones:
        return "None yet. The first one is depth %d." % config.MILESTONES[0]
    out = ["| Depth | Reached | Link | Snapshot |", "| --- | --- | --- | --- |"]
    for m in sorted(milestones, key=lambda m: m["depth"]):
        out.append("| %d | %s | [@%s](https://github.com/%s/%s) | [depth-%d](https://github.com/%s/releases/tag/depth-%d) |"
                   % (m["depth"], m["reached_at"][:10], m["user"], m["user"], config.REPO_NAME,
                      m["depth"], config.ROOT_REPO, m["depth"]))
    return "\n".join(out)


# Mermaid

CLASS_DEFS = [
    "  classDef root fill:#1f2937,color:#ffffff,stroke:#111827",
    "  classDef main fill:#dbeafe,color:#1e3a8a,stroke:#2563eb",
    "  classDef tip fill:#2563eb,color:#ffffff,stroke:#1e3a8a,stroke-width:3px",
    "  classDef side fill:#f3f4f6,color:#111827,stroke:#9ca3af",
    "  classDef invalid fill:#fee2e2,color:#7f1d1d,stroke:#dc2626,stroke-dasharray:4 2",
    "  classDef excluded fill:#e5e7eb,color:#374151,stroke:#6b7280,stroke-dasharray:4 2",
    "  classDef lost fill:#fef3c7,color:#78350f,stroke:#d97706,stroke-dasharray:4 2",
    "  classDef more fill:#ffffff,color:#6b7280,stroke:#9ca3af,stroke-dasharray:2 2",
]


def mermaid(tree: Tree) -> str:
    """A Mermaid graph for the README. Big trees keep the root, the last 20
    main-chain links and what hangs off them; the rest collapses."""
    nodes = tree.nodes
    shown = [i for i in nodes if nodes[i].status != "unverified"]
    if len(shown) <= config.MERMAID_MAX_NODES:
        visible = set(shown)
    else:
        tail = tree.main[-20:]
        visible = {tree.root} | set(tail)
        for m in reversed(tail):
            for c in sorted(nodes[m].children, key=lambda c: nodes[c].forked_at, reverse=True):
                if len(visible) >= config.MERMAID_MAX_NODES:
                    break
                if nodes[c].status != "unverified":
                    visible.add(c)

    between = set()  # hidden nodes on the way to a visible one
    for v in visible:
        a = nodes[v].parent
        while a is not None and a not in visible and a not in between:
            between.add(a)
            a = nodes[a].parent

    def mid(node_id: str) -> str:
        return "n" + node_id

    order = sorted(visible, key=lambda i: (nodes[i].depth, nodes[i].forked_at, i))
    out = ["```mermaid", "graph TD"]
    for i in order:
        out.append('  %s["%s"]:::%s' % (mid(i), _label(tree, nodes[i]), _cls(tree, nodes[i])))
    for i in order:
        a, hidden = nodes[i].parent, 0
        if a is None:
            continue
        while a not in visible:
            hidden += 1
            a = nodes[a].parent
        out.append("  %s -.->|%d hidden| %s" % (mid(a), hidden, mid(i)) if hidden
                   else "  %s --> %s" % (mid(a), mid(i)))
    extra: Dict[str, int] = {}
    for i, n in nodes.items():
        if i in visible or i in between or n.status == "unverified":
            continue
        a = n.parent
        while a not in visible:
            a = nodes[a].parent
        extra[a] = extra.get(a, 0) + 1
    for a in sorted(extra, key=lambda i: (nodes[i].depth, i)):
        out.append('  %s_more(["+%d more"]):::more' % (mid(a), extra[a]))
        out.append("  %s -.- %s_more" % (mid(a), mid(a)))
    return "\n".join(out + CLASS_DEFS + ["```"])


def _label(tree: Tree, node: Node) -> str:
    label = "%d · %s" % (node.depth, chain.safe_user(node.owner))
    return label + " (tip)" if node.id == tree.tip else label


def _cls(tree: Tree, node: Node) -> str:
    if node.id == tree.tip and node.status != "root":
        return "tip"
    if node.status == "valid":
        return "main" if node.main else "side"
    return node.status


# docs/data.json

def data_json(tree: Tree, milestones: List[dict], stall_days: int, stalled: bool, now: str) -> str:
    tip = tree.tip_node
    nodes = sorted(tree.nodes.values(), key=lambda n: (n.depth, n.forked_at or n.first_seen, n.id))
    data = {
        "schema": 1,
        "generated_at": now,
        "root": config.ROOT_REPO,
        "repo_name": config.REPO_NAME,
        "pages": config.PAGES_URL,
        "canvas": {"width": config.CANVAS_W, "height": config.CANVAS_H, "blank": config.CANVAS_BLANK},
        "stall_days": stall_days,
        "milestone_depths": list(config.MILESTONES),
        "summary": {
            "depth": tree.depth, "tip": tip.owner, "tip_id": tree.tip, "tip_url": tip.repo.url,
            "tip_branch": tip.repo.default_branch,
            "total_links": tree.total_links, "side_branches": tree.side_branches,
            "pending": len(tree.pending), "stalled": stalled, "last_link_at": tip.linked_at,
            "next_depth": tip.depth + 1, "next_prev_hash": chain.line_hash(tip.text),
        },
        "nodes": [_node_json(tree, n) for n in nodes],
        "pending": [{"owner": r.owner, "repo": r.full_name, "node": node_id, "forked_at": r.created_at}
                    for r, node_id in tree.pending],
        "problems": [{"owner": r.owner, "repo": r.full_name, "reason": why} for r, why in tree.problems],
        "milestones": sorted(milestones, key=lambda m: m["depth"]),
    }
    return json.dumps(data, indent=1) + "\n"


def _node_json(tree: Tree, n: Node) -> dict:
    return {
        "id": n.id, "parent": n.parent, "depth": n.depth, "line": n.text,
        "owner": n.owner, "repo": n.repo_name, "status": n.status,
        "reasons": n.reasons, "warnings": n.warnings,
        "cell": str(n.line.cell) if n.line.cell else None, "note": n.line.note, "date": n.line.date,
        "forked_at": n.forked_at, "linked_at": n.linked_at, "first_seen": n.first_seen,
        "main": n.main, "tip": n.id == tree.tip,
    }


def load_data(text: str) -> Tuple[Dict[str, dict], List[dict], dict]:
    """Read a previous data.json: (nodes by id, milestones, summary)."""
    try:
        data = json.loads(text)
    except ValueError:
        return {}, [], {}
    if not isinstance(data, dict):
        return {}, [], {}
    nodes = {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict) and n.get("id")}
    return nodes, list(data.get("milestones", [])), dict(data.get("summary", {}))
