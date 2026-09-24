import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401
import tracker
from gitfarm import FakeAPI, GitFarm, rmtree
from longfork import config
from longfork.chain import Cell

README = """# the-long-fork
<!-- TIP START -->
<!-- TIP END -->
<!-- CANVAS START -->
<!-- CANVAS END -->
<!-- TREE START -->
<!-- TREE END -->
<!-- MILESTONES START -->
<!-- MILESTONES END -->
"""


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.farm = GitFarm(self.tmp / "farm")
        self.farm.base.mkdir()
        self.farm.create_root()
        self.api = FakeAPI(self.farm)
        self.repo = self.tmp / "checkout"
        (self.repo / "docs").mkdir(parents=True)
        (self.repo / "README.md").write_bytes(README.encode())
        (self.repo / "EXCLUDED.txt").write_bytes(b"# nobody yet\n")
        self.cache = self.tmp / "cache"

    def tearDown(self):
        self.api.close()
        rmtree(self.tmp)

    def track(self, now, *extra):
        code = tracker.main(["--repo-dir", str(self.repo), "--cache", str(self.cache),
                             "--api", self.api.url, "--git-url", self.farm.url_template(),
                             "--now", now, "--jobs", "2"] + list(extra))
        self.assertEqual(code, 0)
        path = self.cache / "actions.json"
        return json.loads(path.read_text()) if path.exists() else None

    def publish(self, env=None):
        with mock.patch.dict("os.environ", env or {}):
            self.assertEqual(tracker.main(["publish", "--repo-dir", str(self.repo),
                                           "--cache", str(self.cache), "--api", self.api.url]), 0)

    def read(self, name):
        return (self.repo / name).read_bytes().decode()

    def chain_of(self, n, parent="Londopy", prefix="u"):
        for i in range(n):
            owner = "%s%d" % (prefix, i)
            self.farm.fork(owner, parent)
            self.farm.add_link(owner, cell=Cell(i % 64, 0, "#"))
            parent = owner
        return parent

    def test_first_run_with_no_links(self):
        actions = self.track("2026-09-24T13:00:00Z")
        self.assertTrue(actions["write"])
        self.assertIsNone(actions["announce"])
        status = self.read("STATUS.txt")
        self.assertIn("depth:         0\n", status)
        self.assertIn("tip:           https://github.com/Londopy/the-long-fork\n", status)
        self.assertIn("next line:     1 | <your-username> | <YYYY-MM-DD> | 766aac5a6391 |", status)
        self.assertIn("No links yet", self.read("README.md"))
        self.assertEqual(json.loads(self.read("badge.json"))["message"], "0")
        self.assertEqual(self.read("CANVAS.txt"), ("." * 64 + "\n") * 32)
        self.assertEqual(self.read("docs/data.json").count('"status": "root"'), 1)

    def test_links_side_branch_and_quiet_reruns(self):
        self.farm.fork("alice", "Londopy")
        self.farm.add_link("alice", cell=Cell(31, 15, "@"), note="hi from SLO")
        self.farm.fork("bob", "alice")
        self.farm.add_link("bob", message="Update CHAIN.txt")
        self.farm.fork("carol", "bob")
        self.farm.add_link("carol", cell=Cell(32, 15, "#"))
        self.farm.fork("xavier", "Londopy")
        self.farm.add_link("xavier")
        self.farm.fork("pat", "carol")  # pending

        actions = self.track("2026-09-25T00:00:00Z")
        self.assertEqual(actions["announce"], {"depth": 3, "user": "carol", "previous_depth": 0,
                                               "url": "https://github.com/carol/the-long-fork"})
        self.assertEqual(actions["message"], "tracker: depth 3, tip @carol")
        status = self.read("STATUS.txt")
        for fragment in ("depth:         3", "total links:   4", "side branches: 1",
                         "tip:           https://github.com/carol/the-long-fork"):
            self.assertIn(fragment, status)
        tree = self.read("tree.txt")
        self.assertIn("SIDE BRANCH 1, off depth 0 (Londopy)", tree)
        self.assertIn("~ the commit message should be 'link 2: bob'", tree)
        self.assertRegex(tree, r"pat\s+forked off depth 3 \(carol\)")
        readme = self.read("README.md")
        self.assertIn("[@carol](https://github.com/carol/the-long-fork) at depth **3**", readme)
        self.assertIn("```mermaid", readme)
        canvas = self.read("CANVAS.txt").splitlines()
        self.assertEqual(canvas[15][31:33], "@#")

        again = self.track("2026-09-25T12:00:00Z")
        self.assertEqual((again["write"], again["changed"], again["announce"]), (False, [], None))
        self.assertIn("2026-09-25 00:00 UTC", self.read("STATUS.txt"))

        beat = self.track("2026-09-26T00:30:00Z")
        self.assertEqual((beat["write"], beat["message"]), (True, "tracker: heartbeat"))
        self.assertIn("2026-09-26 00:30 UTC", self.read("STATUS.txt"))

    def test_milestone_release_and_announcement(self):
        self.chain_of(10)
        actions = self.track("2026-09-26T00:00:00Z")
        self.assertEqual([m["depth"] for m in actions["new_milestones"]], [10])
        self.assertIn("| 10 | 2026-09-26 | [@u9]", self.read("README.md"))
        self.publish({"ANNOUNCE_DISCUSSION": "1"})
        self.assertEqual([r["tag_name"] for r in self.api.releases], ["depth-10"])
        assets = {name: text for (_, name), text in self.api.assets.items()}
        self.assertEqual(sorted(assets), ["CANVAS.txt", "CHAIN.txt", "tree.txt"])
        self.assertEqual(len(assets["CHAIN.txt"].splitlines()), 11)
        self.assertEqual(assets["CANVAS.txt"].splitlines()[0][:10], "#" * 10)
        self.assertEqual(self.api.comments, [
            "Link 10: @u9 is the new tip - https://github.com/u9/the-long-fork\n"
            "Milestone: the chain reached depth 10."])
        self.publish({"ANNOUNCE_DISCUSSION": "1"})  # a re-run posts nothing twice
        self.assertEqual(len(self.api.releases), 1)
        self.assertEqual(len(self.api.comments), 1)

    def test_deleted_middle_link_keeps_the_chain(self):
        self.farm.fork("alice", "Londopy")
        self.farm.add_link("alice")
        self.farm.fork("bob", "alice")
        self.farm.add_link("bob")
        self.farm.fork("carol", "bob")
        self.farm.add_link("carol")
        self.track("2026-09-25T00:00:00Z")
        self.farm.delete("bob")
        self.track("2026-09-25T12:00:00Z")
        self.assertIn("depth:         3", self.read("STATUS.txt"))
        tree = self.read("tree.txt")
        self.assertRegex(tree, r"\*\s+2  bob\s.*LOST")

    def test_excluded_link(self):
        self.farm.fork("alice", "Londopy")
        self.farm.add_link("alice", note="buy stuff")
        self.farm.fork("bob", "alice")
        self.farm.add_link("bob")
        (self.repo / "EXCLUDED.txt").write_bytes(b"alice | ad in the note\n")
        self.track("2026-09-25T00:00:00Z")
        self.assertIn("total links:   1", self.read("STATUS.txt"))
        self.assertIn("excluded by the maintainer: ad in the note", self.read("tree.txt"))

    def test_dry_run_writes_nothing(self):
        self.track("2026-09-25T00:00:00Z", "--dry-run")
        self.assertFalse((self.repo / "STATUS.txt").exists())
        self.assertEqual(self.read("README.md"), README)


if __name__ == "__main__":
    unittest.main()
