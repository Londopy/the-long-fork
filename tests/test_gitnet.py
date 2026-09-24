import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
from gitfarm import FakeAPI, GitFarm, rmtree
from longfork import config, gitnet, github
from longfork.chain import Cell


class GitNetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.farm = GitFarm(self.tmp / "farm")
        self.farm.base.mkdir()
        self.farm.create_root()
        self.api = FakeAPI(self.farm)
        self.gh = github.GitHub(api=self.api.url, cache_dir=self.tmp / "http")
        self.net = gitnet.NetworkRepo(self.tmp / "net.git", self.farm.url_template())

    def tearDown(self):
        self.api.close()
        rmtree(self.tmp)

    def sync(self):
        root, repos = github.walk(self.gh, config.ROOT_REPO)
        errors = self.net.fetch_all(list(repos.values()), jobs=4)
        return root, repos, errors

    def inspect(self, repos, owner):
        repo = repos[owner.lower()]
        return self.net.inspect(repo, repos[repo.parent.lower()] if repo.parent else None)

    def test_walk_finds_forks_of_forks_across_pages(self):
        for name in ("a1", "a2", "a3"):
            self.farm.fork(name, "Londopy")
        self.farm.fork("b1", "a2")
        self.farm.fork("c1", "b1")
        root, repos, errors = self.sync()
        self.assertEqual(errors, {})
        self.assertEqual(sorted(repos), ["a1", "a2", "a3", "b1", "c1", "londopy"])
        self.assertEqual((repos["c1"].parent, repos["b1"].parent), ("b1", "a2"))

    def test_second_walk_is_served_from_the_etag_cache(self):
        self.farm.fork("a1", "Londopy")
        self.farm.fork("a2", "Londopy")
        self.farm.fork("a3", "Londopy")
        self.sync()
        calls = self.gh.calls
        self.sync()
        self.assertEqual(self.gh.calls, calls)
        self.assertGreaterEqual(self.gh.cached, 3)

    def test_inspect_a_link(self):
        self.farm.fork("alice", "Londopy")
        self.farm.add_link("alice", cell=Cell(1, 2, "#"), note="hi")
        _, repos, _ = self.sync()
        insp = self.inspect(repos, "alice")
        self.assertIsNone(insp.error)
        self.assertEqual(insp.base_lines, [config.ROOT_LINE])
        self.assertEqual(len(insp.lines), 2)
        self.assertTrue(insp.lines[1].endswith("| 1,2=# | hi"))
        self.assertEqual(insp.changed, ["CHAIN.txt"])
        self.assertEqual([c[2] for c in insp.commits], ["link 1: alice"])
        self.assertTrue(insp.link_date.startswith("2026-09-24T13:15:00"))

    def test_inspect_extra_file_and_pending_fork(self):
        self.farm.fork("alice", "Londopy")
        self.farm.add_link("alice", files={"README.md": "hacked\n"})
        self.farm.fork("bob", "alice")
        _, repos, _ = self.sync()
        self.assertEqual(self.inspect(repos, "alice").changed, ["CHAIN.txt", "README.md"])
        bob = self.inspect(repos, "bob")
        self.assertEqual((bob.lines, bob.changed, bob.commits), (bob.base_lines, [], []))

    def test_merge_base_survives_a_deleted_parent(self):
        self.farm.fork("alice", "Londopy")
        self.farm.add_link("alice")
        self.farm.fork("bob", "alice")
        self.farm.add_link("bob")
        self.farm.fork("carol", "bob")
        self.farm.add_link("carol")
        self.farm.delete("bob")
        _, repos, _ = self.sync()
        self.assertEqual(repos["carol"].parent, "alice")
        carol = self.inspect(repos, "carol")
        self.assertEqual(len(carol.base_lines), 2)  # alice's content
        self.assertEqual(len(carol.lines), 4)  # plus bob's and carol's lines

    def test_parent_pushing_later_does_not_move_the_base(self):
        self.farm.fork("alice", "Londopy")
        self.farm.fork("bob", "alice")  # forks alice before she adds her line
        self.farm.add_link("bob")
        self.farm.add_link("alice")
        _, repos, _ = self.sync()
        bob = self.inspect(repos, "bob")
        self.assertEqual(bob.base_lines, [config.ROOT_LINE])
        self.assertEqual(bob.changed, ["CHAIN.txt"])

    def test_fetch_skips_unchanged_and_refetches_after_a_push(self):
        self.farm.fork("alice", "Londopy")
        self.sync()
        stamp = self.net.state["alice"]
        self.farm.add_link("alice")
        _, repos, errors = self.sync()
        self.assertEqual(errors, {})
        self.assertNotEqual(self.net.state["alice"], stamp)
        self.assertEqual(len(self.inspect(repos, "alice").lines), 2)

    def test_fetch_error_is_reported_not_raised(self):
        self.farm.fork("alice", "Londopy")
        rmtree(self.farm.path("alice"))
        _, repos, errors = self.sync()
        self.assertIn("alice", errors)
        self.assertEqual(self.inspect(repos, "alice").error, "couldn't fetch this fork")

    def test_missing_chain_file(self):
        self.farm.fork("alice", "Londopy")
        self.farm.edit("alice", lambda w: (w / "CHAIN.txt").unlink(), "oops")
        _, repos, _ = self.sync()
        self.assertEqual(self.inspect(repos, "alice").error, "CHAIN.txt is missing")


if __name__ == "__main__":
    unittest.main()
