import unittest

import support  # noqa: F401
from longfork import config, network
from longfork.chain import Cell
from netsim import Net, by_owner, prior_of


def statuses(tree):
    return {n.owner: n.status for n in tree.nodes.values()}


class ChainShapeTest(unittest.TestCase):
    def test_straight_chain(self):
        net = Net()
        net.fork("alice", "Londopy")
        net.fork("bob", "alice")
        net.fork("carol", "bob")
        tree = net.build()
        self.assertEqual(tree.depth, 3)
        self.assertEqual(tree.tip_node.owner, "carol")
        self.assertEqual([tree.nodes[i].owner for i in tree.main], ["Londopy", "alice", "bob", "carol"])
        self.assertEqual((tree.total_links, tree.side_branches), (3, 0))

    def test_side_branch_from_forking_the_root(self):
        net = Net()
        net.fork("alice", "Londopy")
        net.fork("bob", "alice")
        net.fork("xavier", "Londopy")  # forked the root instead of the tip
        tree = net.build()
        self.assertEqual((tree.depth, tree.tip_node.owner), (2, "bob"))
        self.assertEqual((tree.total_links, tree.side_branches), (3, 1))
        self.assertEqual(by_owner(tree, "xavier").depth, 1)
        self.assertEqual([[tree.nodes[i].owner for i in b] for b in tree.branches], [["xavier"]])

    def test_side_branch_can_overtake(self):
        net = Net()
        net.fork("alice", "Londopy")
        net.fork("bob", "alice")
        net.fork("xavier", "Londopy")
        net.fork("yara", "xavier")
        net.fork("zoe", "yara")
        tree = net.build()
        self.assertEqual((tree.depth, tree.tip_node.owner), (3, "zoe"))
        self.assertEqual([tree.nodes[i].owner for i in tree.main], ["Londopy", "xavier", "yara", "zoe"])

    def test_tie_goes_to_the_fork_created_first(self):
        net = Net()
        net.fork("alice", "Londopy")
        net.fork("late", "alice", when="2026-09-26T00:00:00Z")
        net.fork("early", "alice", when="2026-09-25T12:00:00Z")
        tree = net.build(now="2026-09-27T00:00:00Z")
        self.assertEqual(tree.tip_node.owner, "early")
        self.assertEqual(tree.side_branches, 1)

    def test_pending_fork_and_a_fork_of_it(self):
        net = Net()
        net.fork("alice", "Londopy", add=False)  # forked, no line yet
        net.fork("bob", "alice")  # forked alice's (still empty) fork
        tree = net.build()
        self.assertEqual([r.owner for r, _ in tree.pending], ["alice"])
        self.assertEqual(tree.nodes[tree.root].pending[0].owner, "alice")
        bob = by_owner(tree, "bob")
        self.assertEqual((bob.status, bob.depth, bob.parent), ("valid", 1, tree.root))


class InvalidLinkTest(unittest.TestCase):
    def test_wrong_username_is_invalid_but_the_next_link_counts(self):
        net = Net()
        net.fork("alice", "Londopy", user="alicia")
        net.fork("bob", "alice")
        tree = net.build()
        alice = by_owner(tree, "alice")
        self.assertEqual(alice.status, "invalid")
        self.assertIn("username should be alice", alice.reasons[0])
        self.assertEqual((tree.tip_node.owner, tree.depth, tree.total_links), ("bob", 2, 1))

    def test_changing_another_file_is_invalid(self):
        net = Net()
        net.fork("alice", "Londopy", changed=["CHAIN.txt", "README.md"])
        net.fork("bob", "alice")
        tree = net.build()
        self.assertIn("README.md", by_owner(tree, "alice").reasons[0])
        self.assertEqual(by_owner(tree, "bob").status, "valid")

    def test_bad_hash_is_invalid(self):
        net = Net()
        base = net.content["londopy"]
        bad = "1 | alice | 2026-09-24 | 0123456789ab | - |"
        net.fork("alice", "Londopy", lines=base + [bad])
        tree = net.build()
        self.assertIn("prev-hash should be", by_owner(tree, "alice").reasons[0])

    def test_adding_lines_for_others_cannot_inflate_depth(self):
        net = Net()
        net.fork("alice", "Londopy", extra=["ghost1", "ghost2"])
        net.fork("bob", "alice")
        tree = net.build()
        self.assertEqual(statuses(tree), {"Londopy": "root", "ghost1": "unverified",
                                          "ghost2": "unverified", "alice": "invalid", "bob": "invalid"})
        self.assertTrue(any("wasn't in the parent" in r for r in by_owner(tree, "alice").reasons))
        self.assertTrue(any("can't be verified" in r for r in by_owner(tree, "bob").reasons))
        self.assertEqual((tree.depth, tree.total_links), (0, 0))

    def test_copying_the_tip_without_forking_it_does_not_count(self):
        net = Net()
        net.fork("alice", "Londopy")
        net.fork("bob", "alice")
        copied = list(net.content["bob"])
        net.fork("mallory", "Londopy", lines=copied + [
            "3 | mallory | 2026-09-24 | %s | - |" % __import__("longfork").chain.line_hash(copied[-1])])
        tree = net.build()
        self.assertEqual(by_owner(tree, "mallory").status, "invalid")
        self.assertEqual(tree.tip_node.owner, "bob")

    def test_rewriting_an_earlier_line_taints_everything_below(self):
        from longfork import chain
        net = Net()
        net.fork("alice", "Londopy", note="hi")
        net.fork("bob", "alice")
        forged = list(net.content["bob"])
        forged[1] = forged[1].replace("| hi", "| bye")
        forged[2] = chain.format_line(2, "bob", forged[2].split(" | ")[2], chain.line_hash(forged[1]))
        forged.append(chain.next_line(forged, "carol", "2026-09-24"))
        net.fork("carol", "bob", lines=forged)
        net.fork("dave", "carol")
        tree = net.build()
        carol, dave = by_owner(tree, "carol"), by_owner(tree, "dave")
        self.assertTrue(carol.rewrote)
        self.assertIn("rewrote line 1", carol.reasons[0])
        self.assertEqual(dave.status, "invalid")
        self.assertEqual((tree.tip_node.owner, tree.depth), ("bob", 2))

    def test_excluded_link_does_not_count_but_the_next_one_does(self):
        net = Net()
        net.fork("alice", "Londopy")
        net.fork("bob", "alice")
        tree = net.build(excluded={"alice": "spam note"})
        alice = by_owner(tree, "alice")
        self.assertEqual(alice.status, "excluded")
        self.assertIn("spam note", alice.reasons[0])
        self.assertEqual((tree.tip_node.owner, tree.total_links), ("bob", 1))

    def test_broken_root_raises(self):
        net = Net()
        net.insp["londopy"].lines = ["0 | someone | 2026-09-24 | 000000000000 | - | root"]
        with self.assertRaises(network.RootError):
            net.build()
        net.insp["londopy"].lines = [config.ROOT_LINE, "1 | x | 2026-09-24 | 000000000000 | - |"]
        with self.assertRaises(network.RootError):
            net.build()


class DeletedLinkTest(unittest.TestCase):
    def test_deleted_middle_link_seen_before_is_lost_and_the_chain_survives(self):
        net = Net()
        net.fork("alice", "Londopy")
        net.fork("xeno", "alice")
        net.fork("yuki", "xeno")
        prior = prior_of(net.build())
        net.delete("xeno")
        net.reparent("yuki", "alice")
        tree = net.build(prior=prior)
        self.assertEqual(statuses(tree)["xeno"], "lost")
        self.assertEqual((by_owner(tree, "yuki").status, tree.depth), ("valid", 3))
        self.assertEqual(tree.tip_node.owner, "yuki")

    def test_deleted_middle_link_never_seen_cannot_be_verified(self):
        net = Net()
        net.fork("alice", "Londopy")
        net.fork("xeno", "alice")
        net.fork("yuki", "xeno")
        net.delete("xeno")
        net.reparent("yuki", "alice")
        tree = net.build()
        self.assertEqual(statuses(tree)["xeno"], "unverified")
        self.assertEqual(by_owner(tree, "yuki").status, "invalid")
        self.assertEqual(tree.tip_node.owner, "alice")

    def test_deleted_tip_falls_back_and_stays_listed(self):
        net = Net()
        net.fork("alice", "Londopy")
        net.fork("bob", "alice")
        prior = prior_of(net.build())
        net.delete("bob")
        tree = net.build(prior=prior)
        self.assertEqual(tree.tip_node.owner, "alice")
        self.assertEqual(statuses(tree)["bob"], "lost")
        self.assertEqual(tree.side_branches, 0)  # a lost dead end isn't a live branch


class DetailsTest(unittest.TestCase):
    def test_problems_are_listed_not_placed(self):
        net = Net()
        net.fork("alice", "Londopy", add=False, changed=["README.md"])
        broken = net.fork("bob", "Londopy")
        net.insp[broken.key].error = "CHAIN.txt is missing"
        net.fork("carl", "Londopy", lines=["0 | x | 2026-09-24 | 000000000000 | - | root"])
        tree = net.build()
        problems = {r.owner: why for r, why in tree.problems}
        self.assertIn("without adding a line", problems["alice"])
        self.assertEqual(problems["bob"], "CHAIN.txt is missing")
        self.assertIn("root line", problems["carl"])
        self.assertEqual(tree.depth, 0)

    def test_warnings_do_not_invalidate(self):
        net = Net()
        net.fork("alice", "Londopy", message="Update CHAIN.txt", owner_type="Organization",
                 name="renamed-fork")
        tree = net.build()
        alice = by_owner(tree, "alice")
        self.assertEqual(alice.status, "valid")
        joined = " / ".join(alice.warnings)
        for fragment in ("commit message should be 'link 1: alice'", "organization", "renamed"):
            self.assertIn(fragment, joined)

    def test_date_far_from_the_fork_date_warns(self):
        from longfork import chain
        net = Net()
        base = net.content["londopy"]
        net.fork("alice", "Londopy", lines=base + [chain.next_line(base, "alice", "2020-01-01")])
        self.assertIn("dated 2020-01-01", " ".join(by_owner(net.build(), "alice").warnings))

    def test_canvas_uses_counted_links_only(self):
        net = Net()
        net.fork("alice", "Londopy", cell=Cell(1, 1, "a"))
        net.fork("bob", "alice", cell=Cell(2, 2, "b"), user="robert")  # invalid
        net.fork("carol", "bob", cell=Cell(3, 3, "c"))
        tree = net.build()
        self.assertEqual(tree.cells_to(tree.tip), [Cell(1, 1, "a"), Cell(3, 3, "c")])

    def test_stall(self):
        net = Net()
        self.assertFalse(net.build(now="2027-01-01T00:00:00Z").stalled(7))  # no links yet
        net.fork("alice", "Londopy", when="2026-09-25T00:00:00Z")
        self.assertFalse(net.build(now="2026-10-01T00:00:00Z").stalled(7))
        self.assertTrue(net.build(now="2026-10-03T00:00:00Z").stalled(7))

    def test_first_seen_is_kept_across_runs(self):
        net = Net()
        net.fork("alice", "Londopy")
        first = net.build(now="2026-09-25T00:00:00Z")
        second = net.build(prior=prior_of(first), now="2026-09-30T00:00:00Z")
        self.assertEqual(by_owner(second, "alice").first_seen, "2026-09-25T00:00:00Z")

    def test_long_chain_does_not_hit_the_recursion_limit(self):
        net = Net()
        parent = "Londopy"
        for i in range(1500):
            parent = net.fork("u%d" % i, parent, when=None).owner
        tree = net.build()
        self.assertEqual(tree.depth, 1500)


if __name__ == "__main__":
    unittest.main()
