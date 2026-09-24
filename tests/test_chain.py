import hashlib
import unittest

import support  # noqa: F401  (puts tools/ on sys.path)
from longfork import chain, config
from longfork.chain import Cell


def link(lines, user, cell=None, note="", date="2026-09-25"):
    return chain.next_line(lines, user, date, cell, note)


class LineFormatTest(unittest.TestCase):
    def test_root_line_parses_clean(self):
        line = chain.parse_line(config.ROOT_LINE)
        self.assertEqual(line.errors, [])
        self.assertEqual((line.depth, line.user, line.cell, line.note), (0, "Londopy", None, "root"))
        self.assertEqual(chain.check_root([config.ROOT_LINE]), [])

    def test_hash_matches_sha256_and_ignores_line_endings(self):
        want = hashlib.sha256(config.ROOT_LINE.encode()).hexdigest()[:12]
        self.assertEqual(chain.line_hash(config.ROOT_LINE), want)
        self.assertEqual(chain.line_hash(config.ROOT_LINE + "\r"), want)
        self.assertEqual(chain.line_hash(config.ROOT_LINE + "   "), want)

    def test_next_line_is_valid(self):
        lines = [config.ROOT_LINE]
        text = link(lines, "alice", Cell(31, 15, "@"), "hi from SLO")
        self.assertTrue(text.startswith("1 | alice | 2026-09-25 | "))
        self.assertTrue(text.endswith(" | 31,15=@ | hi from SLO"))
        parsed = chain.parse_line(text)
        self.assertEqual(chain.check_link(parsed, 1, lines[-1], "alice"), [])
        self.assertEqual(parsed.cell, Cell(31, 15, "@"))

    def test_line_without_note_keeps_six_columns(self):
        text = link([config.ROOT_LINE], "bob")
        self.assertTrue(text.endswith(" | - |"))
        parsed = chain.parse_line(text)
        self.assertEqual((parsed.errors, parsed.note, parsed.cell), ([], "", None))

    def test_five_fields_are_accepted(self):
        text = link([config.ROOT_LINE], "bob")[:-2]  # drop the trailing " |"
        self.assertEqual(chain.parse_line(text).errors, [])

    def test_username_match_ignores_case(self):
        lines = [config.ROOT_LINE]
        parsed = chain.parse_line(link(lines, "Alice"))
        self.assertEqual(chain.check_link(parsed, 1, lines[-1], "alice"), [])

    def assertProblem(self, problems, fragment):
        self.assertTrue(any(fragment in p for p in problems), "%r not in %r" % (fragment, problems))

    def test_context_errors(self):
        lines = [config.ROOT_LINE]
        good = link(lines, "alice")
        parsed = chain.parse_line(good)
        self.assertProblem(chain.check_link(parsed, 2, lines[-1], "alice"), "depth should be 2")
        self.assertProblem(chain.check_link(parsed, 1, lines[-1], "bob"), "username should be bob")
        self.assertProblem(chain.check_link(parsed, 1, "something else", "alice"), "prev-hash should be")

    def test_format_errors(self):
        prev = chain.line_hash(config.ROOT_LINE)
        cases = {
            "1 | alice | 2026-09-25 | %s | 64,0=# |" % prev: "off the canvas",
            "1 | alice | 2026-09-25 | %s | 1,32=# |" % prev: "off the canvas",
            "1 | alice | 2026-09-25 | %s | 1,1= |" % prev: "cell must look like",
            "1 | alice | 2026-09-25 | %s | 1-1=# |" % prev: "cell must look like",
            "1 | alice | 2026-09-25 | %s | - | %s" % (prev, "x" * 81): "limit is 80",
            "1 | alice | 2026-09-25 | %s | - | see https://x.y" % prev: "can't contain links",
            "1 | alice | 2026-09-25 | %s | - | join discord.gg/abc" % prev: "can't contain links",
            "1 | alice | 2026-09-25 | %s | - | café" % prev: "printable ASCII",
            " 1 | alice | 2026-09-25 | %s | - |" % prev: "starts with a space",
            "1 | alice | 2026-02-30 | %s | - |" % prev: "real date",
            "1 | alice | 25/09/2026 | %s | - |" % prev: "real date",
            "1 | alice | 2026-09-25 | abc | - |": "12 hex characters",
            "01 | alice | 2026-09-25 | %s | - |" % prev: "whole number",
            "1 | not a user | 2026-09-25 | %s | - |" % prev: "GitHub username",
            "1 | alice | 2026-09-25 | %s | - | a | b" % prev: "too many |",
            "1 | alice | 2026-09-25": "needs 6 fields",
        }
        for text, fragment in cases.items():
            with self.subTest(text=text):
                self.assertProblem(chain.parse_line(text).errors, fragment)

    def test_cell_edge_values(self):
        self.assertEqual(chain.parse_cell("0,0=!"), (Cell(0, 0, "!"), None))
        self.assertEqual(chain.parse_cell("63,31=~"), (Cell(63, 31, "~"), None))
        self.assertEqual(chain.parse_cell("5,6=="), (Cell(5, 6, "="), None))
        self.assertEqual(chain.parse_cell("-"), (None, None))
        self.assertIsNotNone(chain.parse_cell("5,6=ab")[1])

    def test_notes_that_are_not_links(self):
        for note in ("node.js is fun", "e.g. hello", "Cal Poly SLO!", "v1.2.3", ""):
            with self.subTest(note=note):
                self.assertEqual(chain.note_problems(note), [])


class FileTest(unittest.TestCase):
    def test_read_lines_normalises(self):
        text = "﻿" + config.ROOT_LINE + "\r\n\r\n  \nsecond line   \n\n"
        self.assertEqual(chain.read_lines(text), [config.ROOT_LINE, "second line"])

    def test_check_root(self):
        self.assertEqual(chain.check_root([]), ["CHAIN.txt is empty"])
        self.assertTrue(chain.check_root(["0 | someone | 2026-09-24 | 000000000000 | - | root"]))

    def test_canvas_replays_in_order(self):
        rows = chain.canvas_rows([Cell(0, 0, "a"), None, Cell(63, 31, "z"), Cell(0, 0, "b")])
        self.assertEqual(len(rows), config.CANVAS_H)
        self.assertTrue(all(len(r) == config.CANVAS_W for r in rows))
        self.assertEqual(rows[0][0], "b")
        self.assertEqual(rows[31][63], "z")
        self.assertEqual(rows[5], "." * 64)

    def test_safe_helpers(self):
        self.assertEqual(chain.safe_user("<script>alice"), "scriptalice")
        self.assertEqual(chain.safe_user("!!!"), "?")
        self.assertEqual(chain.safe_text("a\x1bbé"), "a?b?")


if __name__ == "__main__":
    unittest.main()
