"""The tools that run in a fork: link.py and selfcheck.py."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
import link
import selfcheck
from gitfarm import FakeAPI, GitFarm, rmtree, run
from longfork import chain, config

GIT_ID = ["-c", "user.name=test", "-c", "user.email=test@example.com"]


def quiet(fn, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = fn(*args)
    return code, out.getvalue()


class ForkToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.farm = GitFarm(self.tmp / "farm")
        self.farm.base.mkdir()
        self.farm.create_root()
        self.farm.fork("alice", "Londopy")
        self.api = FakeAPI(self.farm)
        self.work = self.farm.clone("alice", self.tmp / "alice")

    def tearDown(self):
        self.api.close()
        rmtree(self.tmp)

    def link(self, *args):
        return quiet(link.main, ["--file", str(self.work / "CHAIN.txt"), "--date", "2026-09-24"] + list(args))

    def selfcheck(self, repo="alice/the-long-fork", *extra):
        return quiet(selfcheck.main, ["--dir", str(self.work), "--repo", repo, "--api", self.api.url,
                                      "--git-url", self.farm.url_template()] + list(extra))

    def commit(self, message):
        run(["git"] + GIT_ID + ["commit", "-qam", message], cwd=self.work)

    def test_link_writes_a_correct_line(self):
        code, out = self.link("--user", "alice", "--cell", "3,4=*", "--note", "hi", "--write")
        self.assertEqual(code, 0, out)
        self.assertIn('git commit -am "link 1: alice"', out)
        lines = chain.read_lines((self.work / "CHAIN.txt").read_text())
        self.assertEqual(lines[1], "1 | alice | 2026-09-24 | 766aac5a6391 | 3,4=* | hi")
        self.assertEqual((self.work / "CHAIN.txt").read_bytes().count(b"\r"), 0)
        code, out = self.link("--user", "alice", "--write")
        self.assertEqual(code, 1)
        self.assertIn("already ends with your line", out)

    def test_link_refuses_bad_input(self):
        for args, fragment in ((["--cell", "64,0=#"], "off the canvas"),
                               (["--note", "see www.example.com"], "can't contain links"),
                               (["--note", "x" * 81], "limit is 80")):
            code, out = self.link("--user", "alice", *args)
            self.assertEqual(code, 1, out)
            self.assertIn(fragment, out)

    def test_selfcheck_before_and_after_committing(self):
        self.link("--user", "alice", "--cell", "3,4=*", "--write")
        code, out = self.selfcheck()
        self.assertEqual(code, 0, out)
        self.assertIn("checked your uncommitted CHAIN.txt", out)
        self.assertIn("PASS: this is a correct link at depth 1.", out)
        self.commit("link 1: alice")
        code, out = self.selfcheck()
        self.assertEqual(code, 0, out)
        self.assertNotIn("uncommitted", out)
        self.assertNotIn("style:", out)

    def test_selfcheck_catches_mistakes(self):
        code, out = self.selfcheck()
        self.assertEqual(code, 1)
        self.assertIn("haven't added your line", out)

        self.link("--user", "alicia", "--write")  # not the fork's owner
        (self.work / "README.md").write_text("changed\n")
        self.commit("Update CHAIN.txt")
        code, out = self.selfcheck()
        self.assertEqual(code, 1, out)
        self.assertIn("the username should be alice", out)
        self.assertIn("changed files other than CHAIN.txt: README.md", out)
        self.assertIn("style: the commit message should be 'link 1: alice'", out)

    def test_selfcheck_in_the_root(self):
        self.work = self.farm.clone("Londopy", self.tmp / "root")
        code, out = self.selfcheck("Londopy/the-long-fork")
        self.assertEqual(code, 0, out)
        self.assertIn("the root's CHAIN.txt is intact", out)

    def test_selfcheck_offline_checks_the_line_alone(self):
        self.link("--user", "alice", "--write")
        code, out = quiet(selfcheck.main, ["--dir", str(self.work), "--repo", "alice/the-long-fork",
                                           "--api", "http://127.0.0.1:9"])
        self.assertEqual(code, 0, out)
        self.assertIn("only the line itself was checked", out)
        lines = (self.work / "CHAIN.txt").read_text().replace("766aac5a6391", "000000000000")
        (self.work / "CHAIN.txt").write_text(lines)
        code, out = quiet(selfcheck.main, ["--dir", str(self.work), "--repo", "alice/the-long-fork",
                                           "--api", "http://127.0.0.1:9"])
        self.assertEqual(code, 1)
        self.assertIn("prev-hash should be 766aac5a6391", out)


if __name__ == "__main__":
    unittest.main()
