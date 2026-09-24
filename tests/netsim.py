"""An in-memory fork network for exercising network.build without git."""

from __future__ import annotations

import datetime as dt

import support  # noqa: F401
from longfork import chain, config, network
from longfork.network import Inspection, Repo

START = dt.datetime(2026, 9, 24, tzinfo=dt.timezone.utc)


class Net:
    def __init__(self):
        self.clock = START
        self.root = Repo(owner=config.ROOT_OWNER, created_at=network.iso(START))
        self.repos = {self.root.key: self.root}
        self.insp = {self.root.key: Inspection(lines=[config.ROOT_LINE])}
        self.content = {self.root.key: [config.ROOT_LINE]}

    def tick(self, hours=1):
        self.clock += dt.timedelta(hours=hours)
        return network.iso(self.clock)

    def fork(self, owner, parent, add=True, user=None, cell=None, note="", extra=(),
             lines=None, changed=None, message=None, when=None, **repo_fields):
        """Fork `parent` as `owner` and (by default) add one correct line."""
        created = when or self.tick()
        base = list(self.content[parent.lower()])
        if lines is None:
            lines = list(base)
            for other in extra:  # lines for someone else, before our own
                lines.append(chain.next_line(lines, other, created[:10]))
            if add:
                lines.append(chain.next_line(lines, user or owner, created[:10], cell, note))
        if changed is None:
            changed = [config.CHAIN_FILE] if lines != base else []
        depth = len(lines) - 1
        commits = []
        if lines != base or changed:
            commits = [("0" * 40, created, message or "link %d: %s" % (depth, owner))]
        repo = Repo(owner=owner, parent=parent, created_at=created, pushed_at=created, **repo_fields)
        self.repos[repo.key] = repo
        self.insp[repo.key] = Inspection(lines=lines, base_lines=base, changed=changed,
                                         commits=commits, link_date=created)
        self.content[repo.key] = lines
        return repo

    def delete(self, owner):
        del self.repos[owner.lower()]
        del self.insp[owner.lower()]

    def reparent(self, owner, new_parent):
        """What GitHub does to a fork whose parent was deleted."""
        self.repos[owner.lower()].parent = new_parent
        insp = self.insp[owner.lower()]
        insp.base_lines = list(self.content[new_parent.lower()])

    def build(self, prior=None, excluded=None, now=None):
        return network.build(self.root, self.repos, self.insp, prior or {}, excluded or {},
                             now or self.tick(0))


def prior_of(tree):
    """The part of data.json that the next run reads back."""
    return {
        n.id: {"id": n.id, "parent": n.parent, "depth": n.depth, "line": n.text,
               "status": n.status, "owner": n.owner, "repo": n.repo_name,
               "forked_at": n.forked_at, "linked_at": n.linked_at, "first_seen": n.first_seen}
        for n in tree.nodes.values()
    }


def by_owner(tree, owner):
    matches = [n for n in tree.nodes.values() if n.repo and n.repo.owner == owner]
    assert len(matches) == 1, (owner, matches)
    return matches[0]
