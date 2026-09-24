"""A fake GitHub for end-to-end tests: real git repos plus a local REST API.

Forks are `git clone --bare` of their parent, so they share history exactly
like repos in a GitHub fork network. The API serves just what the tracker
reads (repos and fork listings, with pagination and ETags) and records what
it writes (releases, assets, discussion comments).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import support  # noqa: F401
from longfork import chain, config, network

START = dt.datetime(2026, 9, 24, 12, 0, tzinfo=dt.timezone.utc)
PAGE = 2  # tiny pages, so pagination gets exercised


def rmtree(path):
    """shutil.rmtree that also deletes git's read-only object files on Windows."""
    def fix(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=fix)
    else:
        shutil.rmtree(path, onerror=fix)


def run(args, cwd=None, env=None):
    done = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError("%s failed: %s" % (" ".join(args), done.stderr))
    return done.stdout


class GitFarm:
    def __init__(self, base: Path):
        self.base = Path(base)
        self.meta = {}
        self.clock = START

    def tick(self, hours=1.0):
        self.clock += dt.timedelta(hours=hours)
        return network.iso(self.clock)

    def path(self, owner):
        m = self.meta[owner.lower()]
        return self.base / m["owner"] / (m["name"] + ".git")

    def url_template(self):
        return self.base.as_posix() + "/{full_name}.git"

    def create_root(self, files=None):
        when = network.iso(self.clock)
        work = Path(tempfile.mkdtemp(dir=self.base))
        run(["git", "init", "-q", "-b", "main", str(work)])
        (work / config.CHAIN_FILE).write_bytes((config.ROOT_LINE + "\n").encode())
        (work / "README.md").write_bytes(b"# the-long-fork\n")
        (work / ".gitattributes").write_bytes(b"* text=auto eol=lf\n")  # as in the real repo
        for name, text in (files or {}).items():
            (work / name).write_bytes(text.encode())
        self._commit(work, "root", when)
        self.meta[config.ROOT_OWNER.lower()] = dict(
            owner=config.ROOT_OWNER, name=config.REPO_NAME, parent=None, created_at=when,
            pushed_at=when, default_branch="main", forks=[], type="User")
        dest = self.path(config.ROOT_OWNER)
        dest.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "-q", "--bare", str(work), str(dest)])
        rmtree(work)
        return config.ROOT_OWNER

    def fork(self, owner, parent, owner_type="User"):
        when = self.tick()
        parent_meta = self.meta[parent.lower()]
        self.meta[owner.lower()] = dict(
            owner=owner, name=config.REPO_NAME, parent=parent_meta["owner"], created_at=when,
            pushed_at=parent_meta["pushed_at"], default_branch="main", forks=[], type=owner_type)
        parent_meta["forks"].append(owner.lower())
        dest = self.path(owner)
        dest.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "-q", "--bare", str(self.path(parent)), str(dest)])
        return owner

    def edit(self, owner, change, message, hours=0.25, raw=()):
        """Clone `owner`'s fork, let `change(workdir)` edit it, commit and push.
        Files named in `raw` are committed byte for byte, skipping .gitattributes."""
        when = self.tick(hours)
        work = Path(tempfile.mkdtemp(dir=self.base))
        run(["git", "clone", "-q", str(self.path(owner)), str(work)])
        change(work)
        self._commit(work, message, when, raw)
        run(["git", "push", "-q", "origin", "HEAD:main"], cwd=work)
        rmtree(work)
        self.meta[owner.lower()]["pushed_at"] = when

    def add_link(self, owner, user=None, cell=None, note="", files=None, message=None, lines=None,
                 crlf=False):
        """Append the correct next line (or `lines`, verbatim) to the fork's CHAIN.txt."""
        def change(work):
            path = work / config.CHAIN_FILE
            current = chain.read_lines(path.read_bytes().decode("utf-8"))
            new = lines if lines is not None else [
                chain.next_line(current, user or owner, self.clock.strftime("%Y-%m-%d"), cell, note)]
            eol = "\r\n" if crlf else "\n"
            path.write_bytes((eol.join(current + new) + eol).encode("utf-8"))
            for name, text in (files or {}).items():
                (work / name).write_bytes(text.encode("utf-8"))
        depth = len(chain.read_lines(self.read(owner, config.CHAIN_FILE)))
        self.edit(owner, change, message or "link %d: %s" % (depth, owner),
                  raw=[config.CHAIN_FILE] if crlf else [])

    def read(self, owner, name):
        return self.read_bytes(owner, name).decode("utf-8")

    def read_bytes(self, owner, name):
        return subprocess.run(["git", "--git-dir", str(self.path(owner)), "show", "main:" + name],
                              capture_output=True, check=True).stdout

    def delete(self, owner):
        """Delete a fork; GitHub hands its forks to its parent."""
        m = self.meta.pop(owner.lower())
        parent = self.meta[m["parent"].lower()]
        parent["forks"].remove(owner.lower())
        for child in m["forks"]:
            self.meta[child]["parent"] = parent["owner"]
            parent["forks"].append(child)
        rmtree(self.base / m["owner"])

    def _commit(self, work, message, when, raw=()):
        env = dict(os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        run(["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "add", "-A"], cwd=work)
        for name in raw:
            blob = run(["git", "hash-object", "-w", "--no-filters", name], cwd=work).strip()
            run(["git", "update-index", "--cacheinfo", "100644,%s,%s" % (blob, name)], cwd=work)
        run(["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-q",
             "--allow-empty", "-m", message], cwd=work, env=env)

    # What the REST API returns for a repo.
    def api_repo(self, key):
        m = self.meta[key]
        item = {
            "name": m["name"], "full_name": m["owner"] + "/" + m["name"],
            "owner": {"login": m["owner"], "type": m["type"]}, "fork": m["parent"] is not None,
            "created_at": m["created_at"], "pushed_at": m["pushed_at"],
            "default_branch": m["default_branch"], "forks_count": len(m["forks"]), "size": 12,
            "disabled": False,
        }
        if m["parent"]:
            p = self.meta[m["parent"].lower()]
            item["parent"] = {"full_name": p["owner"] + "/" + p["name"], "default_branch": p["default_branch"]}
        return item

    def clone(self, owner, dest):
        """A participant's local clone of their fork."""
        run(["git", "clone", "-q", str(self.path(owner)), str(dest)])
        return Path(dest)


class FakeAPI:
    """Serves the farm over HTTP on 127.0.0.1 and records writes."""

    def __init__(self, farm: GitFarm):
        self.farm = farm
        self.releases = []
        self.assets = {}
        self.comments = []
        self.hits = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def _handler(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, code, body=None, headers=None):
                raw = json.dumps(body).encode() if body is not None else b""
                etag = '"%s"' % hashlib.sha1(raw).hexdigest()
                if self.command == "GET" and self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.end_headers()
                    api.hits.append((self.command, self.path, 304))
                    return
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                if self.command == "GET":
                    self.send_header("ETag", etag)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(raw)
                api.hits.append((self.command, self.path, code))

            def body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(url.query)
                m = re.match(r"^/repos/([^/]+)/([^/]+)(/forks|/releases/tags/(.+))?$", url.path)
                if not m or m.group(1).lower() not in api.farm.meta:
                    return self.send(404, {"message": "Not Found"})
                key = m.group(1).lower()
                if m.group(3) is None:
                    return self.send(200, api.farm.api_repo(key))
                if m.group(3) == "/forks":
                    forks = sorted(api.farm.meta[key]["forks"], key=lambda k: api.farm.meta[k]["created_at"])
                    page = int(q.get("page", ["1"])[0])
                    chunk = forks[(page - 1) * PAGE: page * PAGE]
                    headers = {}
                    if page * PAGE < len(forks):
                        nxt = dict((k, v[0]) for k, v in q.items())
                        nxt["page"] = str(page + 1)
                        headers["Link"] = '<%s%s?%s>; rel="next"' % (api.url, url.path, urllib.parse.urlencode(nxt))
                    return self.send(200, [api.farm.api_repo(k) for k in chunk], headers)
                tag = m.group(4)
                for rel in api.releases:
                    if rel["tag_name"] == tag:
                        return self.send(200, rel)
                return self.send(404, {"message": "Not Found"})

            def do_POST(self):
                url = urllib.parse.urlparse(self.path)
                if url.path == "/graphql":
                    payload = json.loads(self.body())
                    if "addDiscussionComment" in payload["query"]:
                        api.comments.append(payload["variables"]["body"])
                        return self.send(200, {"data": {"addDiscussionComment": {"comment": {"url": "x"}}}})
                    return self.send(200, {"data": {"repository": {"discussion": {"id": "D_1"}}}})
                m = re.match(r"^/repos/[^/]+/[^/]+/releases$", url.path)
                if m:
                    payload = json.loads(self.body())
                    rel = dict(payload, id=len(api.releases) + 1,
                               html_url="https://example.test/releases/" + payload["tag_name"],
                               upload_url=api.url + "/uploads/%d/assets{?name,label}" % (len(api.releases) + 1))
                    api.releases.append(rel)
                    return self.send(201, rel)
                m = re.match(r"^/uploads/(\d+)/assets$", url.path)
                if m:
                    name = urllib.parse.parse_qs(url.query)["name"][0]
                    api.assets[(int(m.group(1)), name)] = self.body().decode("utf-8")
                    return self.send(201, {"name": name})
                return self.send(404, {"message": "Not Found"})

        return Handler
