"""The chain tree: which forks are links, which of them count, and the tip.

Every fork's CHAIN.txt goes into one trie, keyed by content, so a line's place
in the tree comes from the file itself and not from GitHub's "forked from"
label (which changes when a link in the middle is deleted). Each fork then
claims the node of the line it added, found by diffing its CHAIN.txt against
the merge base with its GitHub parent.

Only three kinds of line may sit above a counted link: the root line, a line
added by a live fork (valid or not), or a line the tracker saw on an earlier
run whose fork has since been deleted (LOST). Anything else is UNVERIFIED and
invalidates everything below it. That stops depth inflation: adding lines for
other people, copying someone's line instead of forking them, or rewriting
the lines above you.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import chain, config

TRUSTED = frozenset({"root", "valid", "invalid", "excluded", "lost"})


@dataclass
class Repo:
    """A repository in the fork network, as the GitHub API lists it."""

    owner: str
    name: str = config.REPO_NAME
    parent: Optional[str] = None  # owner login of the GitHub parent; None for the root
    created_at: str = ""
    pushed_at: str = ""
    default_branch: str = "main"
    owner_type: str = "User"
    forks_count: int = 0
    size_kb: int = 0
    disabled: bool = False

    @property
    def key(self) -> str:
        return self.owner.lower()

    @property
    def full_name(self) -> str:
        return self.owner + "/" + self.name

    @property
    def url(self) -> str:
        return "https://github.com/" + self.full_name


@dataclass
class Inspection:
    """What git says about one fork, relative to its GitHub parent."""

    lines: Optional[List[str]] = None  # CHAIN.txt at the fork's head
    base_lines: Optional[List[str]] = None  # CHAIN.txt at the merge base with the parent
    changed: List[str] = field(default_factory=list)  # files changed since the merge base
    commits: List[Tuple[str, str, str]] = field(default_factory=list)  # (sha, date, subject), oldest first
    link_date: Optional[str] = None  # committer date of the newest commit touching CHAIN.txt
    error: Optional[str] = None


@dataclass
class Node:
    id: str
    parent: Optional[str]
    depth: int
    text: str
    line: chain.Line
    children: List[str] = field(default_factory=list)
    repo: Optional[Repo] = None  # the live fork that added this line
    owner: str = ""  # display name: the fork owner, or the name the line claims
    repo_name: str = ""  # owner/name, kept for LOST links
    status: str = "unverified"
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    rewrote: bool = False  # its fork changed lines above its own
    forked_at: str = ""
    linked_at: str = ""
    first_seen: str = ""
    main: bool = False
    pending: List[Repo] = field(default_factory=list)


@dataclass
class Tree:
    nodes: Dict[str, Node]
    root: str
    tip: str
    main: List[str]
    branches: List[List[str]]
    pending: List[Tuple[Repo, str]]
    problems: List[Tuple[Repo, str]]
    now: str

    @property
    def tip_node(self) -> Node:
        return self.nodes[self.tip]

    @property
    def depth(self) -> int:
        return self.nodes[self.tip].depth

    @property
    def total_links(self) -> int:
        return sum(1 for n in self.nodes.values() if n.status == "valid")

    @property
    def side_branches(self) -> int:
        """Side branches that someone could still continue: ones with a valid link."""
        return sum(1 for b in self.branches if any(self.nodes[i].status == "valid" for i in b))

    def path_to(self, node_id: str) -> List[str]:
        path = []
        cur: Optional[str] = node_id
        while cur is not None:
            path.append(cur)
            cur = self.nodes[cur].parent
        return path[::-1]

    def cells_to(self, node_id: str) -> List[Optional[chain.Cell]]:
        """The canvas cells along the path to a node, from links that count."""
        return [self.nodes[i].line.cell for i in self.path_to(node_id)
                if self.nodes[i].status in ("valid", "lost")]

    def stalled(self, stall_days: int) -> bool:
        tip = self.tip_node
        if tip.status == "root" or not tip.linked_at:
            return False
        age = parse_time(self.now) - parse_time(tip.linked_at)
        return age > _dt.timedelta(days=stall_days)


def parse_time(text: str) -> _dt.datetime:
    """ISO 8601 from GitHub (…Z) or git (…-07:00), as an aware datetime."""
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    value = _dt.datetime.fromisoformat(text)
    if value.tzinfo is None:
        value = value.replace(tzinfo=_dt.timezone.utc)
    return value


def iso(value: _dt.datetime) -> str:
    return value.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def node_ids(lines: List[str]) -> List[str]:
    """One id per prefix of the file, so equal ids mean equal history."""
    ids, h = [], ""
    for line in lines:
        h = hashlib.sha256((h + "\n" + line).encode("utf-8", "surrogateescape")).hexdigest()[:16]
        ids.append(h)
    return ids


class RootError(Exception):
    """The root repo itself is broken; nothing downstream can be trusted."""


def build(root: Repo, repos: Dict[str, Repo], inspections: Dict[str, Inspection],
          prior: Dict[str, dict], excluded: Dict[str, str], now: str) -> Tree:
    """Build the chain tree.

    repos        every repo in the network by owner key, the root included
    inspections  what git found for each of them
    prior        nodes from the previous run's data.json, by id
    excluded     owner key -> reason, from EXCLUDED.txt
    now          ISO time of this run
    """
    root_insp = inspections.get(root.key)
    root_lines = root_insp.lines if root_insp else None
    problems_at_root = chain.check_root(root_lines or [])
    if root_lines and len(root_lines) > 1:
        problems_at_root.append("the root's CHAIN.txt must hold only line 0")
    if root_insp is None or root_insp.error or problems_at_root:
        raise RootError((root_insp and root_insp.error) or problems_at_root[0])

    nodes: Dict[str, Node] = {}

    def insert(lines: List[str]) -> List[str]:
        ids = node_ids(lines)
        for depth, (node_id, text) in enumerate(zip(ids, lines)):
            if node_id not in nodes:
                parent = ids[depth - 1] if depth else None
                nodes[node_id] = Node(id=node_id, parent=parent, depth=depth, text=text,
                                      line=chain.parse_line(text))
                if parent is not None:
                    nodes[parent].children.append(node_id)
        return ids

    root_id = insert(root_lines[:1])[0]
    root_node = nodes[root_id]
    root_node.repo, root_node.owner, root_node.repo_name = root, root.owner, root.full_name
    root_node.status = "root"
    root_node.forked_at = root_node.linked_at = root.created_at or now

    claims: Dict[str, Tuple[Repo, Inspection, List[str], List[int], Optional[int]]] = {}
    pending: List[Tuple[Repo, str]] = []
    problems: List[Tuple[Repo, str]] = []

    forks = sorted((r for r in repos.values() if r.key != root.key),
                   key=lambda r: (r.created_at, r.key))
    for repo in forks:
        insp = inspections.get(repo.key)
        if insp is None or insp.error:
            problems.append((repo, (insp and insp.error) or "not checked"))
            continue
        lines, base = insp.lines or [], insp.base_lines or []
        if not lines or lines[0] != config.ROOT_LINE:
            problems.append((repo, "CHAIN.txt doesn't start with the root line"))
            continue
        ids = insert(lines)
        rewrote_at = _first_difference(base, lines)
        if rewrote_at is None:
            if len(lines) == len(base):
                others = sorted(set(insp.changed) - config.ALLOWED_FILES)
                if others:
                    problems.append((repo, "changed %s without adding a line" % ", ".join(others)))
                else:
                    pending.append((repo, ids[-1]))
                    nodes[ids[-1]].pending.append(repo)
                continue
            gap = list(range(max(len(base), 1), len(lines) - 1))
        else:
            if chain.parse_line(lines[-1]).user.lower() != repo.key:
                problems.append((repo, "rewrote line %d of CHAIN.txt" % rewrote_at))
                continue
            gap = []
        if ids[-1] in claims:
            problems.append((repo, "has the same CHAIN.txt as %s" % claims[ids[-1]][0].full_name))
            continue
        claims[ids[-1]] = (repo, insp, ids, gap, rewrote_at)

    # Add links seen on earlier runs that are gone now; they become LOST.
    for old in sorted(prior.values(), key=lambda p: p.get("depth", 0)):
        node_id, parent = old.get("id"), old.get("parent")
        if old.get("status") in (None, "unverified", "root") or node_id in nodes:
            continue
        if parent not in nodes:
            continue
        nodes[node_id] = Node(id=node_id, parent=parent, depth=nodes[parent].depth + 1,
                              text=old.get("line", ""), line=chain.parse_line(old.get("line", "")))
        nodes[parent].children.append(node_id)

    for node_id, (repo, insp, ids, gap, rewrote_at) in claims.items():
        _judge(nodes[node_id], repo, insp, ids, gap, rewrote_at, nodes, claims, prior, excluded, now)

    for node in nodes.values():
        if node.status == "unverified" and node.repo is None:
            _settle_unclaimed(node, prior, excluded)
        if not node.first_seen:
            node.first_seen = (prior.get(node.id) or {}).get("first_seen") or now

    _propagate(nodes, root_id)

    valid = [n for n in nodes.values() if n.status == "valid"]
    tip = min(valid, key=lambda n: (-n.depth, n.forked_at, n.owner.lower())) if valid else root_node
    tree = Tree(nodes=nodes, root=root_id, tip=tip.id, main=[], branches=[],
                pending=pending, problems=problems, now=now)
    tree.main = tree.path_to(tip.id)
    for node_id in tree.main:
        nodes[node_id].main = True
    tree.branches = _side_branches(tree)
    return tree


def _first_difference(base: List[str], lines: List[str]) -> Optional[int]:
    """Where `lines` stops extending `base`, or None if it only appends."""
    for i, line in enumerate(base):
        if i >= len(lines) or lines[i] != line:
            return i
    return None


def _judge(node, repo, insp, ids, gap, rewrote_at, nodes, claims, prior, excluded, now):
    node.repo, node.owner, node.repo_name = repo, repo.owner, repo.full_name
    node.forked_at = repo.created_at or now
    # The line existed by the time the tracker first saw it, which also keeps
    # a future-dated commit from shifting on every run.
    node.first_seen = (prior.get(node.id) or {}).get("first_seen") or now
    node.linked_at = _clamp(insp.link_date or node.forked_at, node.forked_at, node.first_seen)
    prev_text = nodes[node.parent].text if node.parent else None
    errors = chain.check_link(node.line, node.depth, prev_text, repo.owner)

    if rewrote_at is not None:
        node.rewrote = True
        errors.insert(0, "rewrote line %d of CHAIN.txt, which was already in the chain when this "
                         "fork was made" % rewrote_at)
    for pos in gap:
        other = nodes[ids[pos]]
        if ids[pos] in claims or not _was_seen(prior.get(ids[pos])):
            errors.append("line %d (%s) wasn't in the parent fork's CHAIN.txt; add only your own "
                          "line to the fork you forked" % (pos, chain.safe_user(other.line.user)))
    others = sorted(set(insp.changed) - config.ALLOWED_FILES)
    if others:
        errors.append("changed files other than %s: %s" % (config.CHAIN_FILE, ", ".join(others)))

    if len(insp.commits) > 1 and not gap:
        node.warnings.append("made %d commits; the rules ask for one" % len(insp.commits))
    want = "link %d: %s" % (node.depth, repo.owner)
    if insp.commits and not any(s.strip().lower() == want.lower() for _, _, s in insp.commits):
        node.warnings.append("the commit message should be '%s'" % want)
    if repo.name != config.REPO_NAME:
        node.warnings.append("the repo was renamed to %s (rule 2)" % repo.name)
    if repo.owner_type == "Organization":
        node.warnings.append("the fork belongs to an organization")
    date_warning = _date_warning(node.line.date, node.forked_at, node.linked_at)
    if date_warning:
        node.warnings.append(date_warning)

    node.reasons = errors
    node.status = "invalid" if errors else "valid"
    if repo.key in excluded:
        node.status = "excluded"
        node.reasons = ["excluded by the maintainer: " + (excluded[repo.key] or "rule 9")] + errors


def _was_seen(old: Optional[dict]) -> bool:
    return bool(old) and old.get("status") in TRUSTED


def _settle_unclaimed(node, prior, excluded):
    """A line no live fork owns: a deleted link we saw before, or unverifiable."""
    old = prior.get(node.id)
    node.owner = chain.safe_user(node.line.user)
    if _was_seen(old):
        node.status = "lost"
        node.owner = old.get("owner") or node.owner
        node.repo_name = old.get("repo", "")
        node.forked_at = old.get("forked_at", "")
        node.linked_at = old.get("linked_at", "")
        node.reasons = ["the fork is gone (deleted, renamed away, or its account closed)"]
        if node.owner.lower() in excluded:
            node.status = "excluded"
            node.reasons = ["excluded by the maintainer: " + (excluded[node.owner.lower()] or "rule 9")]
    else:
        node.status = "unverified"
        node.reasons = ["no fork in the network added this line"]


def _propagate(nodes: Dict[str, Node], root_id: str) -> None:
    """A link only counts if every line above it can be trusted."""
    blame: Dict[str, Optional[str]] = {root_id: None}  # nearest untrusted ancestor
    order = [root_id]
    for node_id in order:
        node = nodes[node_id]
        for child in node.children:
            if node.status not in TRUSTED or node.rewrote:
                blame[child] = node_id
            else:
                blame[child] = blame[node_id]
            order.append(child)
    for node_id in order:
        culprit = blame.get(node_id)
        node = nodes[node_id]
        if culprit is None or node.repo is None:
            continue
        bad = nodes[culprit]
        why = ("the fork at depth %d (%s) rewrote the lines above it" % (bad.depth, bad.owner)
               if bad.rewrote and bad.status in TRUSTED else
               "line %d (%s) above this one can't be verified" % (bad.depth, bad.owner))
        node.reasons.append(why)
        if node.status == "valid":
            node.status = "invalid"


def _side_branches(tree: Tree) -> List[List[str]]:
    """Split everything off the main chain into paths, one per leaf."""
    nodes = tree.nodes
    best: Dict[str, Tuple[int, int]] = {}

    def score(node_id: str) -> Tuple[int, int]:
        if node_id not in best:
            node = nodes[node_id]
            own = (node.depth if node.status == "valid" else -1, node.depth)
            kids = [score(c) for c in node.children]
            best[node_id] = max([own] + kids)
        return best[node_id]

    for node_id in sorted(nodes, key=lambda i: -nodes[i].depth):
        score(node_id)  # deepest first keeps the recursion shallow

    def when(node_id: str) -> Tuple[str, str]:
        node = nodes[node_id]
        return (node.forked_at or node.first_seen, node.owner.lower())

    listed = set(tree.main)
    queue = list(tree.main)
    branches: List[List[str]] = []
    for node_id in queue:
        for child in sorted(nodes[node_id].children, key=when):
            if child in listed:
                continue
            path, cur = [child], child
            while nodes[cur].children:
                # Follow the child with the deepest valid link, earliest on a tie.
                cur = sorted(nodes[cur].children,
                             key=lambda c: (tuple(-v for v in score(c)), when(c)))[0]
                path.append(cur)
            listed.update(path)
            branches.append(path)
            queue.extend(path)
    return branches


def _clamp(value: str, low: str, high: str) -> str:
    try:
        t, lo, hi = parse_time(value), parse_time(low), parse_time(high)
    except ValueError:
        return low
    return iso(min(max(t, lo), hi))


def _date_warning(line_date: str, forked_at: str, linked_at: str) -> Optional[str]:
    try:
        day = _dt.date(int(line_date[:4]), int(line_date[5:7]), int(line_date[8:10]))
        lo = parse_time(forked_at).date() - _dt.timedelta(days=1)
        hi = parse_time(linked_at).date() + _dt.timedelta(days=1)
    except (ValueError, IndexError):
        return None
    if lo <= day <= hi:
        return None
    return "the line is dated %s but the fork was made %s" % (line_date, forked_at[:10])
