"""Build a made-up fork network and run the tracker on it, for previewing the
README blocks, tree.txt and the Pages site without touching GitHub.

    python tests/demo.py OUTDIR
    python -m http.server -d OUTDIR/docs 8000

Every name in the demo is invented.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import support
import tracker
from gitfarm import FakeAPI, GitFarm, rmtree
from longfork.chain import Cell

# A little fork glyph for the canvas, drawn one cell per link.
GLYPH = [(31, 22), (31, 21), (31, 20), (31, 19), (31, 18), (30, 17), (29, 16), (28, 15),
         (32, 17), (33, 16), (34, 15), (27, 14), (35, 14)]
NOTES = {1: "hi from the first link", 3: "ham club says hello", 6: "73 de the relay",
         9: "keep it going", 12: "longest fork or bust"}


def build(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name in ("README.md", "EXCLUDED.txt"):
        shutil.copy(support.REPO_ROOT / name, out / name)
    shutil.copytree(support.REPO_ROOT / "docs", out / "docs", dirs_exist_ok=True)
    with open(out / "EXCLUDED.txt", "a", encoding="utf-8", newline="\n") as f:
        f.write("spamton | an ad in the note\n")

    work = Path(tempfile.mkdtemp())
    farm = GitFarm(work / "farm")
    farm.base.mkdir()
    farm.create_root()
    api = FakeAPI(farm)
    try:
        main = ["amara", "bexley", "cosmo", "dalia", "emrys", "fenna", "gideon", "hollis",
                "ines", "jubal", "kestrel", "lumen", "marlow", "nadia"]
        parent = "Londopy"
        for depth, owner in enumerate(main, 1):
            farm.tick(hours=9)
            farm.fork(owner, parent)
            x, y = GLYPH[(depth - 1) % len(GLYPH)]
            farm.add_link(owner, cell=Cell(x, y, "#"), note=NOTES.get(depth, ""),
                          message=None if depth % 4 else "Update CHAIN.txt")
            parent = owner
        # A side branch off the root: someone forked the root instead of the tip.
        farm.fork("oriel", "Londopy")
        farm.add_link("oriel", cell=Cell(2, 2, "o"), note="oops, forked the root")
        farm.fork("pim", "oriel")
        farm.add_link("pim", cell=Cell(3, 2, "p"))
        # A side branch off depth 5, with a broken link in the middle.
        farm.fork("quill", "emrys")
        farm.add_link("quill", cell=Cell(10, 28, "q"), user="quilly")  # wrong username
        farm.fork("rune", "quill")
        farm.add_link("rune", cell=Cell(11, 28, "r"))
        farm.fork("spamton", "rune")
        farm.add_link("spamton", note="best deals in town")
        farm.fork("tamsin", "spamton")
        farm.add_link("tamsin", cell=Cell(12, 28, "t"))
        # A fork that changed the README instead of adding a line, and a pending one.
        farm.fork("uriah", "hollis")
        farm.edit("uriah", lambda w: (w / "README.md").write_text("mine now\n"), "Update README.md")
        farm.fork("vesper", "nadia")

        args = ["--repo-dir", str(out), "--cache", str(work / "cache"), "--api", api.url,
                "--git-url", farm.url_template(), "--jobs", "4"]
        tracker.main(args + ["--now", "2026-09-30T06:00:00Z"])
        # Then gideon deletes their fork; hollis and everyone after stay counted.
        farm.delete("gideon")
        tracker.main(args + ["--now", "2026-09-30T18:00:00Z"])
    finally:
        api.close()
        rmtree(work)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    build(Path(sys.argv[1]))
