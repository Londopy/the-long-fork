"""The repo's own files agree with tools/longfork/config.py."""

import re
import unittest

import support
from longfork import chain, config

WORKFLOWS = support.REPO_ROOT / ".github" / "workflows"
ROOT_ONLY = ("tracker.yml", "tests.yml", "pr-guide.yml")


def jobs_and_guards(text):
    body = text.split("\njobs:\n", 1)[1]
    jobs = re.findall(r"(?m)^  ([A-Za-z0-9_-]+):\s*$", body)
    guards = re.findall(r"(?m)^    if: github\.repository == '([^']+)'\s*$", body)
    return jobs, guards


class ConfigTest(unittest.TestCase):
    def test_every_root_only_job_is_guarded(self):
        for name in ROOT_ONLY:
            with self.subTest(workflow=name):
                jobs, guards = jobs_and_guards((WORKFLOWS / name).read_text("utf-8"))
                self.assertTrue(jobs)
                self.assertEqual(guards, [config.ROOT_REPO] * len(jobs))

    def test_self_check_runs_in_forks(self):
        text = (WORKFLOWS / "self-check.yml").read_text("utf-8")
        self.assertNotIn("github.repository ==", text)
        self.assertIn("fetch-depth: 0", text)

    def test_root_chain_file(self):
        self.assertEqual((support.REPO_ROOT / "CHAIN.txt").read_bytes(), (config.ROOT_LINE + "\n").encode())

    def test_readme_markers(self):
        text = (support.REPO_ROOT / "README.md").read_text("utf-8")
        for name in ("TIP", "CANVAS", "TREE", "MILESTONES"):
            self.assertEqual(text.count("<!-- %s START -->" % name), 1, name)
            self.assertEqual(text.count("<!-- %s END -->" % name), 1, name)
            self.assertLess(text.index("<!-- %s START -->" % name), text.index("<!-- %s END -->" % name))

    def test_rules_example_is_a_valid_chain(self):
        text = (support.REPO_ROOT / "RULES.txt").read_text("utf-8")
        lines = [l.strip() for l in text.split("Example CHAIN.txt:")[1].splitlines() if l.strip()]
        self.assertEqual(lines[0], config.ROOT_LINE)
        for i in range(1, len(lines)):
            parsed = chain.parse_line(lines[i])
            self.assertEqual(chain.check_link(parsed, i, lines[i - 1], parsed.user), [], lines[i])

    def test_readme_hash_commands_hash_the_root_line(self):
        # The README promises this value to the first person to join.
        self.assertEqual(chain.line_hash(config.ROOT_LINE), "766aac5a6391")


if __name__ == "__main__":
    unittest.main()
