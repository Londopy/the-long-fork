"""Milestone releases and new-tip announcements.

Announcements go to a pinned Discussion in the root repo (which also notifies
the new tip through the @mention), and optionally to a Discord webhook and a
Mastodon account. Each channel is skipped quietly when it isn't configured,
and a failure in one never stops the others.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Mapping, Tuple

from . import chain, config
from .github import GitHub, GitHubError

FIND_DISCUSSION = """query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) { discussion(number: $number) { id } }
}"""
ADD_COMMENT = """mutation($id: ID!, $body: String!) {
  addDiscussionComment(input: {discussionId: $id, body: $body}) { comment { url } }
}"""


def snapshot(data: dict, node_id: str) -> Tuple[str, str]:
    """CHAIN.txt and CANVAS.txt as they stood at one node, from data.json."""
    nodes = {n["id"]: n for n in data["nodes"]}
    path = []
    cur = node_id
    while cur:
        path.append(nodes[cur])
        cur = nodes[cur]["parent"]
    path.reverse()
    cells = [chain.parse_cell(n["cell"])[0] for n in path
             if n.get("cell") and n["status"] in ("valid", "lost")]
    return ("\n".join(n["line"] for n in path) + "\n",
            "\n".join(chain.canvas_rows(cells)) + "\n")


def ensure_releases(gh: GitHub, data: dict, tree_text: str, log: Callable[[str], None] = print) -> List[str]:
    """Make sure every recorded milestone has its depth-<n> release."""
    made = []
    for m in data.get("milestones", []):
        tag = "depth-%d" % m["depth"]
        try:
            gh.get("/repos/%s/releases/tags/%s" % (config.ROOT_REPO, tag), cache=False)
            continue
        except GitHubError as e:
            if e.status != 404:
                raise
        chain_text, canvas_text = snapshot(data, m["node"])
        body = ("The main chain reached depth %d on %s. Link %d is [@%s](https://github.com/%s/%s).\n\n"
                "Attached: CHAIN.txt up to that link, the canvas at that link, and the full tree "
                "when this release was made." % (m["depth"], m["reached_at"][:10], m["depth"],
                                                 m["user"], m["user"], config.REPO_NAME))
        release = gh.post("/repos/%s/releases" % config.ROOT_REPO,
                          {"tag_name": tag, "name": "Depth %d" % m["depth"], "body": body})
        upload = release["upload_url"].split("{")[0]
        for name, text in (("CHAIN.txt", chain_text), ("CANVAS.txt", canvas_text), ("tree.txt", tree_text)):
            gh.request("POST", upload + "?name=" + name, body=text.encode("utf-8"),
                       headers={"Content-Type": "text/plain; charset=utf-8"})
        log("released %s" % tag)
        made.append(tag)
    return made


def message(info: dict, milestones: List[dict], mention: bool) -> str:
    who = ("@" if mention else "") + info["user"]
    text = "Link %d: %s is the new tip - %s" % (info["depth"], who, info["url"])
    for m in milestones:
        text += "\nMilestone: the chain reached depth %d." % m["depth"]
    return text


def announce(gh: GitHub, info: dict, milestones: List[dict], env: Mapping[str, str],
             log: Callable[[str], None] = print, post: Callable[..., None] = None) -> List[str]:
    """Post the new tip everywhere that's configured. Returns where it went."""
    post = post or _post_json
    sent = []
    number = (env.get("ANNOUNCE_DISCUSSION") or "").strip()
    if number.isdigit():
        try:
            owner, name = config.ROOT_REPO.split("/")
            found = gh.graphql(FIND_DISCUSSION, {"owner": owner, "name": name, "number": int(number)})
            discussion = ((found or {}).get("repository") or {}).get("discussion")
            if not discussion:
                raise GitHubError(404, "no discussion #%s" % number)
            gh.graphql(ADD_COMMENT, {"id": discussion["id"], "body": message(info, milestones, True)})
            sent.append("discussion")
        except GitHubError as e:
            log("discussion announcement failed: %s" % e)
    webhook = (env.get("DISCORD_WEBHOOK") or "").strip()
    if webhook:
        try:
            post(webhook, {"content": message(info, milestones, False), "allowed_mentions": {"parse": []}})
            sent.append("discord")
        except (urllib.error.URLError, OSError, ValueError) as e:
            log("discord announcement failed: %s" % e)
    token = (env.get("MASTODON_TOKEN") or "").strip()
    instance = (env.get("MASTODON_INSTANCE") or "").strip().rstrip("/")
    if token and instance:
        try:
            post(instance + "/api/v1/statuses",
                 {"status": message(info, milestones, False), "visibility": "public"},
                 {"Authorization": "Bearer " + token})
            sent.append("mastodon")
        except (urllib.error.URLError, OSError, ValueError) as e:
            log("mastodon announcement failed: %s" % e)
    return sent


def _post_json(url: str, payload: dict, headers: Dict[str, str] = None) -> None:
    """A plain POST, so the GitHub token never goes to another host."""
    if not url.startswith("https://"):
        raise ValueError("refusing to post to a non-https URL")
    hdrs = {"Content-Type": "application/json", "User-Agent": "the-long-fork-tracker"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()
