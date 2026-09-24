"""Project constants.

The root repo's name also appears, literally, in the `if:` guard of every
root-only workflow job. tests/test_config.py keeps the two in sync.
"""

from __future__ import annotations

ROOT_OWNER = "Londopy"
REPO_NAME = "the-long-fork"
ROOT_REPO = ROOT_OWNER + "/" + REPO_NAME
ROOT_DATE = "2026-09-24"
ROOT_LINE = "0 | " + ROOT_OWNER + " | " + ROOT_DATE + " | 000000000000 | - | root"

PAGES_URL = "https://londopy.github.io/the-long-fork/"

CHAIN_FILE = "CHAIN.txt"
# The only file a link may change. The canvas cell rides inside the line.
ALLOWED_FILES = frozenset({CHAIN_FILE})

CANVAS_W = 64
CANVAS_H = 32
CANVAS_BLANK = "."

NOTE_MAX = 80
HASH_LEN = 12

STALL_DAYS = 7
HEARTBEAT_HOURS = 24
MILESTONES = (10, 25, 50, 100, 250, 500, 1000)
MERMAID_MAX_NODES = 60

# Forks bigger than this (GitHub's `size`, in KB) are not fetched.
MAX_REPO_KB = 50_000
