"""A small GitHub REST client and the fork-network walk.

GET responses are cached with their ETag, and a 304 Not Modified doesn't count
against the rate limit, so re-walking an unchanged network is nearly free.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

from .network import Repo

API = "https://api.github.com"
SAFE_LOGIN = re.compile(r"^[A-Za-z0-9-]+$")
SAFE_REPO = re.compile(r"^[A-Za-z0-9._-]+$")


class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__("HTTP %s: %s" % (status, message))
        self.status = status


class RateLimited(GitHubError):
    pass


class GitHub:
    def __init__(self, token: Optional[str] = None, api: str = API,
                 cache_dir: Optional[Path] = None, sleep: Callable[[float], None] = time.sleep,
                 max_wait: float = 900):
        self.token = token
        self.api = api.rstrip("/")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.sleep = sleep
        self.max_wait = max_wait
        self.calls = 0  # responses that counted against the rate limit
        self.cached = 0  # 304s served from the cache

    def request(self, method: str, url: str, body: Any = None,
                headers: Optional[Dict[str, str]] = None, cache: bool = False) -> Tuple[Any, Dict[str, str]]:
        if not url.startswith("http"):
            url = self.api + url
        entry = self._cache_get(url) if cache else None
        hdrs = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "the-long-fork-tracker"}
        if self.token:
            hdrs["Authorization"] = "Bearer " + self.token
        if entry and entry.get("etag"):
            hdrs["If-None-Match"] = entry["etag"]
        hdrs.update(headers or {})
        data = None
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")

        for attempt in range(5):
            req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    self.calls += 1
                    raw = resp.read()
                    rh = {k.lower(): v for k, v in resp.headers.items()}
            except urllib.error.HTTPError as e:
                rh = {k.lower(): v for k, v in e.headers.items()}
                if e.code == 304 and entry:
                    self.cached += 1
                    return entry["body"], {"link": entry.get("link", "")}
                self.calls += 1
                wait = self._retry_wait(e.code, rh, attempt)
                if wait is None:
                    msg = e.read().decode("utf-8", "replace")[:300]
                    if e.code in (403, 429) and rh.get("x-ratelimit-remaining") == "0":
                        raise RateLimited(e.code, msg)
                    raise GitHubError(e.code, msg)
                self.sleep(wait)
                continue
            except urllib.error.URLError as e:
                if attempt == 4:
                    raise GitHubError(0, str(e.reason))
                self.sleep(2 ** attempt)
                continue
            payload = json.loads(raw.decode("utf-8")) if raw.strip() else None
            if cache and method == "GET" and rh.get("etag"):
                self._cache_put(url, {"etag": rh["etag"], "body": payload, "link": rh.get("link", "")})
            return payload, rh
        raise GitHubError(0, "gave up on %s after retries" % url)

    def get(self, path: str, params: Optional[Dict[str, Any]] = None, cache: bool = True) -> Any:
        url = path + ("?" + urllib.parse.urlencode(params) if params else "")
        return self.request("GET", url, cache=cache)[0]

    def paginate(self, path: str, params: Dict[str, Any]) -> Iterator[Any]:
        url: Optional[str] = path + "?" + urllib.parse.urlencode(params)
        while url:
            body, headers = self.request("GET", url, cache=True)
            for item in body or []:
                yield item
            url = next_link(headers.get("link", ""))

    def post(self, path: str, body: Any) -> Any:
        return self.request("POST", path, body=body)[0]

    def graphql(self, query: str, variables: Dict[str, Any]) -> Any:
        result = self.post("/graphql", {"query": query, "variables": variables})
        if result and result.get("errors"):
            raise GitHubError(200, json.dumps(result["errors"])[:300])
        return (result or {}).get("data")

    def _retry_wait(self, code: int, headers: Dict[str, str], attempt: int) -> Optional[float]:
        if attempt >= 4:
            return None
        if code in (403, 429):
            if "retry-after" in headers:
                return min(float(headers["retry-after"]), self.max_wait)
            if headers.get("x-ratelimit-remaining") == "0":
                wait = float(headers.get("x-ratelimit-reset", "0")) - time.time() + 1
                return max(wait, 1) if wait <= self.max_wait else None
            return None
        if code >= 500:
            return 2.0 ** attempt
        return None

    def _cache_path(self, url: str) -> Optional[Path]:
        if not self.cache_dir:
            return None
        return self.cache_dir / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".json")

    def _cache_get(self, url: str) -> Optional[dict]:
        path = self._cache_path(url)
        try:
            return json.loads(path.read_text("utf-8")) if path else None
        except (OSError, ValueError):
            return None

    def _cache_put(self, url: str, entry: dict) -> None:
        path = self._cache_path(url)
        if path:
            path.write_text(json.dumps(entry), "utf-8")


def next_link(header: str) -> Optional[str]:
    for part in header.split(","):
        m = re.match(r'\s*<([^>]+)>\s*;\s*rel="next"', part)
        if m:
            return m.group(1)
    return None


def repo_from_api(item: dict, parent: Optional[str]) -> Optional[Repo]:
    owner = (item.get("owner") or {}).get("login", "")
    name = item.get("name", "")
    if not (SAFE_LOGIN.match(owner) and SAFE_REPO.match(name)):
        return None
    return Repo(owner=owner, name=name, parent=parent,
                created_at=item.get("created_at") or "",
                pushed_at=item.get("pushed_at") or item.get("created_at") or "",
                default_branch=item.get("default_branch") or "main",
                owner_type=(item.get("owner") or {}).get("type", "User"),
                forks_count=int(item.get("forks_count") or 0),
                size_kb=int(item.get("size") or 0),
                disabled=bool(item.get("disabled")))


def walk(gh: GitHub, root_full_name: str) -> Tuple[Repo, Dict[str, Repo]]:
    """Every repo in the fork network, found by listing forks of forks."""
    root = repo_from_api(gh.get("/repos/" + root_full_name), None)
    if root is None:
        raise GitHubError(0, "the root repo has an unexpected name")
    repos = {root.key: root}
    queue = [root]
    for repo in queue:
        if repo.forks_count == 0:
            continue
        for item in gh.paginate("/repos/%s/forks" % repo.full_name, {"per_page": 100, "sort": "oldest"}):
            fork = repo_from_api(item, repo.owner)
            if fork is not None and fork.key not in repos:
                repos[fork.key] = fork
                queue.append(fork)
    return root, repos
