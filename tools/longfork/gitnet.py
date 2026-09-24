"""The git side of the tracker.

Every repo in the network is fetched into one bare repository. Forks share
history, so each fetch moves only that fork's new commits, and none of it
touches the API rate limit. The merge base, changed files and commits then
come from plain git, the same way the self-check in a fork gets them.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import chain, config
from .network import Inspection, Repo

SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
SAFE_BRANCH = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]*$")
MAX_CHAIN_BYTES = 8 * 1024 * 1024
MAX_COMMITS = 50

GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", LC_ALL="C")


class GitError(Exception):
    pass


def git(git_dir: Optional[Path], *args: str, timeout: int = 120) -> bytes:
    cmd = ["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false", "-c", "credential.helper="]
    if git_dir is not None:
        cmd += ["--git-dir", str(git_dir)]
    try:
        done = subprocess.run(cmd + list(args), stdin=subprocess.DEVNULL, capture_output=True,
                              env=GIT_ENV, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise GitError("git %s timed out" % args[0])
    if done.returncode != 0:
        raise GitError(done.stderr.decode("utf-8", "replace").strip() or "git %s failed" % args[0])
    return done.stdout


class Inspector:
    """Reads CHAIN.txt and history out of any git dir: the tracker's bare repo
    of the whole network, or a fork's own checkout for the self-check."""

    def __init__(self, git_dir: Path):
        self.git_dir = git_dir

    def run(self, *args: str) -> str:
        return git(self.git_dir, *args).decode("utf-8", "replace")

    def resolve(self, rev: str) -> Optional[str]:
        try:
            return self.run("rev-parse", "--verify", "--quiet", rev + "^{commit}").strip() or None
        except GitError:
            return None

    def blob(self, rev: str, path: str) -> Optional[bytes]:
        spec = "%s:%s" % (rev, path)
        try:
            size = int(self.run("cat-file", "-s", spec).strip())
        except (GitError, ValueError):
            return None
        if size > MAX_CHAIN_BYTES:
            raise GitError("%s is %d bytes, too big to check" % (path, size))
        return git(self.git_dir, "cat-file", "blob", spec)

    def chain_lines(self, rev: str) -> Optional[List[str]]:
        raw = self.blob(rev, config.CHAIN_FILE)
        return None if raw is None else chain.read_lines(chain.decode(raw))

    def merge_base(self, a: str, b: str) -> Optional[str]:
        try:
            return self.run("merge-base", a, b).strip() or None
        except GitError:
            return None

    def changed_files(self, base: str, head: str) -> List[str]:
        out = git(self.git_dir, "diff", "--no-renames", "--name-only", "-z", base, head)
        return sorted(p for p in out.decode("utf-8", "replace").split("\0") if p)

    def commits(self, base: str, head: str) -> List[Tuple[str, str, str]]:
        out = self.run("log", "--reverse", "--max-count=%d" % MAX_COMMITS,
                       "--format=%H%x1f%cI%x1f%s%x1e", "%s..%s" % (base, head))
        rows = []
        for record in out.split("\x1e"):
            parts = record.strip("\n").split("\x1f")
            if len(parts) == 3:
                rows.append((parts[0], parts[1], parts[2]))
        return rows

    def link_date(self, base: str, head: str) -> Optional[str]:
        out = self.run("log", "-1", "--format=%cI", "%s..%s" % (base, head), "--", config.CHAIN_FILE)
        return out.strip() or None

    def inspect(self, head_rev: str, parent_rev: Optional[str]) -> Inspection:
        """Compare a fork's head with its parent's head (None for the root)."""
        head = self.resolve(head_rev)
        if head is None:
            return Inspection(error="couldn't fetch this fork")
        try:
            lines = self.chain_lines(head)
            if lines is None:
                return Inspection(error="%s is missing" % config.CHAIN_FILE)
            if parent_rev is None:
                return Inspection(lines=lines)
            parent = self.resolve(parent_rev)
            if parent is None:
                return Inspection(lines=lines, error="couldn't fetch the fork it came from")
            base = self.merge_base(parent, head)
            if base is None:
                return Inspection(lines=lines, error="shares no history with the fork it came from")
            base_lines = self.chain_lines(base)
            if base_lines is None:
                return Inspection(lines=lines, error="%s is missing where it was forked" % config.CHAIN_FILE)
            return Inspection(lines=lines, base_lines=base_lines,
                              changed=self.changed_files(base, head),
                              commits=self.commits(base, head),
                              link_date=self.link_date(base, head))
        except GitError as e:
            return Inspection(error=str(e))


class NetworkRepo:
    """The tracker's bare repo holding every fork under refs/net/<owner>."""

    def __init__(self, path: Path, url_template: str = "https://github.com/{full_name}.git"):
        self.path = path
        self.url_template = url_template
        if not (path / "HEAD").exists():
            path.mkdir(parents=True, exist_ok=True)
            git(None, "init", "--bare", "--quiet", str(path))
        self.inspector = Inspector(path)
        self.state_file = path / "longfork-fetch.json"
        try:
            self.state: Dict[str, str] = json.loads(self.state_file.read_text("utf-8"))
        except (OSError, ValueError):
            self.state = {}

    @staticmethod
    def ref(repo: Repo) -> str:
        return "refs/net/" + repo.key

    def fetch_all(self, repos: List[Repo], jobs: int = 8) -> Dict[str, str]:
        """Fetch every repo whose last push we haven't seen. Returns errors by key."""
        have = set(self.inspector.run("for-each-ref", "--format=%(refname)", "refs/net/").split())
        wanted = {self.ref(r) for r in repos}
        for gone in sorted(have - wanted):
            git(self.path, "update-ref", "-d", gone)
            self.state.pop(gone[len("refs/net/"):], None)
        todo = [r for r in repos
                if self.ref(r) not in have or self.state.get(r.key) != self._stamp(r)]
        errors: Dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            first = list(zip(todo, pool.map(self._fetch, todo)))
        for repo, error in first:
            if error:
                # Parallel fetches of forks that share new objects can collide
                # (git on Windows can't replace an object file another process
                # has open). One quiet retry settles it.
                error = self._fetch(repo)
            if error:
                errors[repo.key] = error
            else:
                self.state[repo.key] = self._stamp(repo)
        self.state_file.write_text(json.dumps(self.state, indent=0, sort_keys=True), "utf-8")
        return errors

    @staticmethod
    def _stamp(repo: Repo) -> str:
        return "%s|%s" % (repo.pushed_at, repo.default_branch)

    def _fetch(self, repo: Repo) -> Optional[str]:
        if not (SAFE_NAME.match(repo.owner) and SAFE_NAME.match(repo.name)
                and SAFE_BRANCH.match(repo.default_branch)):
            return "the repo or branch name has characters the tracker won't fetch"
        if repo.disabled:
            return "the repo is disabled by GitHub"
        if repo.size_kb > config.MAX_REPO_KB:
            return "the repo is %d MB, too big to fetch" % (repo.size_kb // 1024)
        url = self.url_template.format(full_name=repo.full_name, owner=repo.owner, name=repo.name)
        refspec = "+refs/heads/%s:%s" % (repo.default_branch, self.ref(repo))
        try:
            git(self.path, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", url, refspec,
                timeout=300)
        except GitError as e:
            return "couldn't fetch: " + str(e).splitlines()[-1][:200]
        return None

    def inspect(self, repo: Repo, parent: Optional[Repo]) -> Inspection:
        return self.inspector.inspect(self.ref(repo), self.ref(parent) if parent else None)
