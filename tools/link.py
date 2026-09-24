#!/usr/bin/env python3
"""Write your CHAIN.txt line for you.

Run it in a clone of your fork, before you commit:

    python tools/link.py --user your-username --cell 12,5=# --note "hello"
    python tools/link.py --user your-username --write

The first prints the line; --write appends it to CHAIN.txt. Then commit with
the message it prints.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from longfork import chain, config  # noqa: E402

REMOTE_RE = re.compile(r"github\.com[:/]+([A-Za-z0-9-]+)/")


def origin_owner(work: Path) -> str:
    done = subprocess.run(["git", "-C", str(work), "remote", "get-url", "origin"],
                          capture_output=True, text=True)
    m = REMOTE_RE.search(done.stdout) if done.returncode == 0 else None
    return m.group(1) if m else ""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Make your the-long-fork line.")
    p.add_argument("--user", help="your GitHub username (default: the owner of the origin remote)")
    p.add_argument("--cell", default="-", help="x,y=c to set one canvas cell, or - to skip (default)")
    p.add_argument("--note", default="", help="optional note, up to 80 plain ASCII characters")
    p.add_argument("--date", default=None, help="YYYY-MM-DD (default: today, UTC)")
    p.add_argument("--file", default=config.CHAIN_FILE, help="path to CHAIN.txt")
    p.add_argument("--write", action="store_true", help="append the line to the file")
    args = p.parse_args(argv)

    path = Path(args.file)
    try:
        raw = path.read_bytes()
    except OSError:
        print("There's no %s here. Run this in a clone of your fork." % path)
        return 1
    lines = chain.read_lines(chain.decode(raw))
    problems = chain.check_root(lines)
    if problems:
        print("This %s is broken: %s" % (path, problems[0]))
        return 1
    user = args.user or origin_owner(path.resolve().parent)
    if not user:
        print("Couldn't tell your username; pass --user.")
        return 1
    if chain.parse_line(lines[-1]).user.lower() == user.lower() and len(lines) > 1:
        print("%s already ends with your line:\n  %s" % (path, lines[-1]))
        return 1
    cell, cell_error = chain.parse_cell(args.cell.strip())
    if cell_error:
        print("--cell: " + cell_error)
        return 1

    line = chain.next_line(lines, user, args.date or chain.today_utc(), cell, args.note.strip())
    problems = chain.check_link(chain.parse_line(line), len(lines), lines[-1], user)
    if problems:
        print("That line wouldn't count:")
        for problem in problems:
            print("  - " + problem)
        return 1

    print(line)
    if args.write:
        text = raw.decode("utf-8", "surrogateescape")
        sep = "" if text.endswith("\n") or not text else "\n"
        path.write_bytes((text + sep + line + "\n").encode("utf-8", "surrogateescape"))
        print("\nAdded to %s. Now commit it:" % path)
    else:
        print("\nAdd that line to the end of %s, then commit it:" % path)
    print('  git commit -am "link %d: %s"' % (len(lines), user))
    return 0


if __name__ == "__main__":
    sys.exit(main())
