#!/usr/bin/env python3
"""Check your link, using the same rules as the tracker.

In a fork with Actions turned on, .github/workflows/self-check.yml runs this on
every push. Locally, run it in a clone of your fork, before or after you
commit:

    python tools/selfcheck.py

It never changes anything. Exit status: 0 the link is correct, 1 it isn't,
2 it couldn't be checked.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from longfork import chain, config, network  # noqa: E402
from longfork.github import API, GitHub, GitHubError  # noqa: E402
from longfork.gitnet import GitError, Inspector, git  # noqa: E402

PARENT_REF = "refs/longfork/parent"
REMOTE_RE = re.compile(r"github\.com[:/]+([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$")
IN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"


def sh(work: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(work)] + list(args), capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else ""


def fork_name(work: Path) -> Optional[str]:
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    m = REMOTE_RE.search(sh(work, "remote", "get-url", "origin"))
    return "%s/%s" % (m.group(1), m.group(2)) if m else None


class Result:
    def __init__(self, raw: str):
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.notes: List[str] = []
        # File line numbers of the non-blank lines, for annotations.
        self.line_numbers = [i + 1 for i, t in enumerate(raw.split("\n")) if t.strip()]

    def annotate(self, message: str, index: Optional[int], level: str = "error") -> None:
        if IN_ACTIONS:
            where = ""
            if index is not None and 0 <= index < len(self.line_numbers):
                where = " file=%s,line=%d" % (config.CHAIN_FILE, self.line_numbers[index])
            print("::%s%s::%s" % (level, where, message.replace("\n", " ")))


def check(work: Path, repo: Optional[str], parent: Optional[str], branch: Optional[str],
          api: str, git_url: str, user: Optional[str]) -> int:
    path = work / config.CHAIN_FILE
    try:
        raw = chain.decode(path.read_bytes())
    except OSError:
        print("FAIL: there's no %s here. Run this in a clone of your fork." % config.CHAIN_FILE)
        return 1
    lines = chain.read_lines(raw)
    result = Result(raw)
    print("the-long-fork self-check")

    info = None
    if repo and not parent:
        try:
            info = GitHub(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"), api).get(
                "/repos/" + repo, cache=False)
        except GitHubError as e:
            result.notes.append("couldn't ask GitHub about %s (%s), so only the line itself was checked"
                                % (repo, e))
    owner = user or (info["owner"]["login"] if info else (repo.split("/")[0] if repo else None))
    if info and not info.get("fork"):
        print("repo:    %s (the root)" % repo)
        problems = chain.check_root(lines)
        if len(lines) > 1:
            problems.append("the root's CHAIN.txt must hold only line 0")
        return finish(result, problems, None, "the root's CHAIN.txt is intact.")
    if info:
        parent = info["parent"]["full_name"]
        branch = branch or info["parent"]["default_branch"]
    print("fork:    %s" % (repo or "(unknown)"))
    print("parent:  %s" % (parent or "(unknown)"))

    base: Optional[List[str]] = None
    changed: List[str] = []
    commits = []
    if parent:
        git_dir = Path(sh(work, "rev-parse", "--absolute-git-dir"))
        try:
            git(git_dir, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head",
                git_url.format(full_name=parent, owner=parent.split("/")[0], name=parent.split("/")[1]),
                "+refs/heads/%s:%s" % (branch or "main", PARENT_REF), timeout=300)
            insp = Inspector(git_dir).inspect("HEAD", PARENT_REF)
            if insp.error:
                raise GitError(insp.error)
            merge_base = sh(work, "merge-base", "HEAD", PARENT_REF)
            base = insp.base_lines
            commits = insp.commits
            # Check what's on disk, so a line can be checked before it's committed.
            changed = sorted(p for p in sh(work, "diff", "--no-renames", "--name-only", merge_base).splitlines() if p)
            if lines != insp.lines:
                result.notes.append("checked your uncommitted %s" % config.CHAIN_FILE)
        except GitError as e:
            result.notes.append("couldn't compare with %s (%s), so only the line itself was checked"
                                % (parent, str(e).splitlines()[-1]))
            base = None

    root_problems = chain.check_root(lines)
    if root_problems:
        return finish(result, root_problems, 0, "")
    if base is not None:
        rewrote_at = network.first_difference(base, lines)
        if rewrote_at is None and len(lines) == len(base):
            others = sorted(set(changed) - config.ALLOWED_FILES)
            problems = ["you haven't added your line to %s yet" % config.CHAIN_FILE]
            if others:
                problems.append("you changed %s, which you shouldn't" % ", ".join(others))
            return finish(result, problems, None, "")
        gap = list(range(max(len(base), 1), len(lines) - 1)) if rewrote_at is None else []
        if gap:
            result.warnings.append(
                "lines %d-%d aren't in your parent's %s. That's only OK if they belong to a link "
                "that was deleted; the tracker decides." % (gap[0], gap[-1], config.CHAIN_FILE))
    else:
        rewrote_at, gap = None, []
    if len(lines) < 2:
        return finish(result, ["add your line after the root line"], None, "")

    own = chain.parse_line(lines[-1])
    print("line:    %s" % chain.safe_text(lines[-1]))
    problems = network.link_errors(own, len(lines) - 1, lines[-2], owner, changed, rewrote_at)
    if owner:
        result.warnings += network.link_warnings(
            len(lines) - 1, owner, commits, not gap,
            info["name"] if info else config.REPO_NAME,
            info["owner"].get("type", "User") if info else "User")
    if own.cell:
        result.notes.append("your cell: %s sets column %d, row %d to %s" % (own.cell, own.cell.x, own.cell.y, own.cell.ch))
    return finish(result, problems, len(lines) - 1,
                  "this is a correct link at depth %d." % (len(lines) - 1))


def finish(result: Result, problems: List[str], index: Optional[int], ok: str) -> int:
    print()
    for note in result.notes:
        print("note: " + note)
    for warning in result.warnings:
        print("style: " + warning)
        result.annotate(warning, index, "warning")
    if problems:
        print("FAIL: this link won't count yet:")
        for problem in problems:
            print("  - " + chain.safe_text(problem))
            result.annotate(problem, index)
        print("Fix %s, commit, and push again." % config.CHAIN_FILE)
        return 1
    print("PASS: " + ok)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Check your the-long-fork link.")
    p.add_argument("--dir", default=".", help="your clone of the fork (default: here)")
    p.add_argument("--repo", help="your fork as owner/name (default: from Actions or the origin remote)")
    p.add_argument("--parent", help="the fork you forked, as owner/name (default: ask GitHub)")
    p.add_argument("--branch", help="the parent's branch (default: its default branch)")
    p.add_argument("--user", help="your GitHub username (default: the fork's owner)")
    p.add_argument("--api", default=API)
    p.add_argument("--git-url", default="https://github.com/{full_name}.git")
    args = p.parse_args(argv)
    work = Path(args.dir)
    try:
        return check(work, args.repo or fork_name(work), args.parent, args.branch, args.api,
                     args.git_url, args.user)
    except (OSError, GitError) as e:
        print("couldn't check: %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
