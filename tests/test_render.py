"""Output details that the end-to-end tests don't pin down."""

import json
import unittest

import support  # noqa: F401
from longfork import config, render
from longfork.chain import Cell
from netsim import Net


class RenderTest(unittest.TestCase):
    def test_mermaid_collapses_big_trees(self):
        net = Net()
        parent = "Londopy"
        for i in range(120):
            parent = net.fork("m%d" % i, parent).owner
            if i % 10 == 0:
                net.fork("s%d" % i, parent)
        text = render.mermaid(net.build())
        shown = text.count(":::") - text.count("classDef")
        self.assertLessEqual(shown, config.MERMAID_MAX_NODES + 30)
        self.assertIn("more links|", text)
        self.assertIn("(tip)", text)
        self.assertTrue(text.startswith("```mermaid\ngraph TD\n") and text.endswith("```"))

    def test_canvas_fence_outgrows_backticks_in_the_canvas(self):
        net = Net()
        parent = "Londopy"
        for i in range(4):
            parent = net.fork("u%d" % i, parent, cell=Cell(i, 0, "`")).owner
        block = render.canvas_block(net.build())
        self.assertIn("\n`````text\n", block)  # four backticks in a row inside, so a five-tick fence
        self.assertTrue(block.endswith("\n`````"))

    def test_replace_block_leaves_text_without_markers_alone(self):
        self.assertEqual(render.replace_block("no markers", "TIP", "x"), "no markers")
        text = "a\n<!-- TIP START -->\nold\n<!-- TIP END -->\nb"
        self.assertEqual(render.replace_block(text, "TIP", "new"),
                         "a\n<!-- TIP START -->\nnew\n<!-- TIP END -->\nb")

    def test_mask_ignores_only_timestamps(self):
        a = "last checked:  2026-09-24 12:00 UTC\ndepth: 3\n" + '"generated_at": "2026-09-24T12:00:00Z"'
        b = "last checked:  2026-09-25 00:30 UTC\ndepth: 3\n" + '"generated_at": "2026-09-25T00:30:00Z"'
        self.assertEqual(render.mask(a), render.mask(b))
        self.assertNotEqual(render.mask(a), render.mask(b.replace("depth: 3", "depth: 4")))

    def test_data_json_round_trips_as_prior_state(self):
        net = Net()
        net.fork("alice", "Londopy", note="hi")
        tree = net.build()
        text = render.data_json(tree, [], 7, False, tree.now)
        nodes, milestones, summary = render.load_data(text)
        self.assertEqual(summary["tip"], "alice")
        self.assertEqual(summary["next_prev_hash"], json.loads(text)["summary"]["next_prev_hash"])
        self.assertEqual({n["status"] for n in nodes.values()}, {"root", "valid"})
        self.assertEqual(render.load_data("not json"), ({}, [], {}))

    def test_stalled_outputs(self):
        net = Net()
        net.fork("alice", "Londopy", when="2026-09-25T00:00:00Z")
        tree = net.build(now="2026-10-10T00:00:00Z")
        self.assertTrue(tree.stalled(7))
        self.assertIn(render.STALL_TEXT, render.status_txt(tree, True, tree.now))
        self.assertIn("CHAIN STALLED", render.tip_block(tree, True))
        badge = json.loads(render.badge_json(tree, True))
        self.assertEqual((badge["message"], badge["color"]), ("1 (stalled)", "orange"))


if __name__ == "__main__":
    unittest.main()
