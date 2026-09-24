#!/usr/bin/env python3
"""The tracker. It runs in the root repo only (.github/workflows/tracker.yml).

    python tools/tracker.py            walk the fork network, update the status files
    python tools/tracker.py publish    cut milestone releases, announce a new tip

To see what it would write without writing anything:

    GITHUB_TOKEN=$(gh auth token) python tools/tracker.py --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from longfork import announce, config, network, render  # noqa: E402
from longfork.github import GitHub, GitHubError, RateLimited, walk  # noqa: E402
from longfork.gitnet import NetworkRepo  # noqa: E402

DATA = "docs/data.json"


def read(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except OSError:
        return ""


def write(path: Path, text: str) -> None:
    """Always LF, always UTF-8, whatever the OS."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def load_excluded(text: str) -> dict:
    excluded = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            user, _, reason = line.partition("|")
            if user.strip():
                excluded[user.strip().lower()] = reason.strip()
    return excluded


def heartbeat_due(status_text: str, now: str) -> bool:
    """True when STATUS.txt's "last checked" is a day old (or missing)."""
    for line in status_text.splitlines():
        if line.startswith("last checked:"):
            try:
                last = dt.datetime.strptime(line.split(":", 1)[1].strip(), "%Y-%m-%d %H:%M UTC")
            except ValueError:
                return True
            age = network.parse_time(now) - last.replace(tzinfo=dt.timezone.utc)
            return age >= dt.timedelta(hours=config.HEARTBEAT_HOURS)
    return True


def step_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def run(args: argparse.Namespace, token: str) -> int:
    repo_dir, cache = Path(args.repo_dir), Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    now = args.now or network.iso(dt.datetime.now(dt.timezone.utc))
    prior, milestones, old = render.load_data(read(repo_dir / DATA))
    excluded = load_excluded(read(repo_dir / "EXCLUDED.txt"))

    gh = GitHub(token, args.api, cache / "http")
    try:
        root, repos = walk(gh, config.ROOT_REPO)
    except RateLimited as e:
        print("::warning::rate limited while walking the network, keeping the last status (%s)" % e)
        return 0
    except GitHubError as e:
        print("::warning::couldn't walk the network, keeping the last status (%s)" % e)
        return 0

    net = NetworkRepo(cache / "net.git", args.git_url)
    fetch_errors = net.fetch_all(list(repos.values()), jobs=args.jobs)
    inspections = {}
    for key, repo in repos.items():
        parent = repos.get(repo.parent.lower()) if repo.parent else None
        insp = net.inspect(repo, parent)
        if insp.error and key in fetch_errors:
            insp.error = fetch_errors[key]
        inspections[key] = insp
    try:
        tree = network.build(root, repos, inspections, prior, excluded, now)
    except network.RootError as e:
        print("::error::the root repo's CHAIN.txt is broken: %s" % e)
        return 1
    stalled = tree.stalled(args.stall_days)

    reached = {m["depth"] for m in milestones}
    new_milestones = []
    for depth in config.MILESTONES:
        if depth <= tree.depth and depth not in reached:
            node = tree.nodes[tree.main[depth]]
            new_milestones.append({"depth": depth, "node": node.id, "user": node.owner, "reached_at": now})
    milestones = milestones + new_milestones

    outputs = {
        "STATUS.txt": render.status_txt(tree, stalled, now),
        "tree.txt": render.tree_txt(tree, now),
        "badge.json": render.badge_json(tree, stalled),
        "CANVAS.txt": render.canvas_txt(tree),
        DATA: render.data_json(tree, milestones, args.stall_days, stalled, now),
    }
    readme_text = read(repo_dir / "README.md")
    if readme_text:
        outputs["README.md"] = render.readme(readme_text, tree, stalled, milestones)
    changed = sorted(name for name, text in outputs.items()
                     if render.mask(read(repo_dir / name)) != render.mask(text))
    heartbeat = heartbeat_due(read(repo_dir / "STATUS.txt"), now)

    tip = tree.tip_node
    moved = tree.tip != old.get("tip_id") or tree.depth != old.get("depth")
    news = None
    if tree.tip != old.get("tip_id") and tree.depth > int(old.get("depth") or 0):
        news = {"depth": tree.depth, "user": tip.owner, "url": tip.repo.url,
                "previous_depth": int(old.get("depth") or 0)}
    if moved:
        message = "tracker: depth %d, tip @%s" % (tree.depth, tip.owner)
    elif changed:
        message = "tracker: update the chain tree"
    else:
        message = "tracker: heartbeat"

    report = ("%s, tip @%s, %s; API calls %d (+%d cached), %s"
              % (render.summary_line(tree), tip.owner,
                 render.count(len(tree.problems), "fork that isn't a link", "forks that aren't links"),
                 gh.calls, gh.cached, render.count(len(fetch_errors), "fetch error", "fetch errors")))
    print(report)
    if args.dry_run:
        print()
        print(outputs["STATUS.txt"])
        print(outputs["tree.txt"])
        return 0

    write_now = bool(changed) or heartbeat or args.force
    if write_now:
        for name, text in outputs.items():
            write(repo_dir / name, text)
    actions = {"write": write_now, "changed": changed, "message": message,
               "announce": news, "new_milestones": new_milestones}
    write(cache / "actions.json", json.dumps(actions, indent=1) + "\n")
    write(cache / "commit-message.txt", message + "\n")
    step_summary("### the-long-fork tracker\n\n%s\n\nchanged: %s" % (report, ", ".join(changed) or "nothing"))
    return 0


def publish(args: argparse.Namespace, token: str) -> int:
    repo_dir, cache = Path(args.repo_dir), Path(args.cache)
    try:
        actions = json.loads(read(cache / "actions.json") or "{}")
        data = json.loads(read(repo_dir / DATA) or "{}")
    except ValueError as e:
        print("::warning::nothing to publish (%s)" % e)
        return 0
    gh = GitHub(token, args.api)
    try:
        announce.ensure_releases(gh, data, read(repo_dir / "tree.txt"))
    except GitHubError as e:
        print("::warning::milestone release failed, will retry next run (%s)" % e)
    if actions.get("announce"):
        sent = announce.announce(gh, actions["announce"], actions.get("new_milestones", []), os.environ)
        print("announced to: %s" % (", ".join(sent) or "nowhere (nothing configured)"))
        actions["announce"] = None  # a re-run of this step mustn't post twice
        write(cache / "actions.json", json.dumps(actions, indent=1) + "\n")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Track the-long-fork's fork chain.")
    p.add_argument("command", nargs="?", default="run", choices=["run", "publish"])
    p.add_argument("--repo-dir", default=".", help="the root repo checkout to update")
    p.add_argument("--cache", default=".tracker-cache", help="where the HTTP cache and network repo live")
    p.add_argument("--api", default="https://api.github.com")
    p.add_argument("--git-url", default="https://github.com/{full_name}.git",
                   help="where to fetch forks from; {full_name}, {owner} and {name} are filled in")
    p.add_argument("--now", help="pretend it's this ISO 8601 time")
    p.add_argument("--stall-days", type=int, default=int(os.environ.get("STALL_DAYS") or config.STALL_DAYS))
    p.add_argument("--jobs", type=int, default=8, help="parallel git fetches")
    p.add_argument("--dry-run", action="store_true", help="print the results, write nothing")
    p.add_argument("--force", action="store_true", help="write the outputs even if nothing changed")
    args = p.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    return publish(args, token) if args.command == "publish" else run(args, token)


if __name__ == "__main__":
    sys.exit(main())
