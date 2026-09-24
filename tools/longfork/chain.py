"""The CHAIN.txt line format.

    <depth> | <username> | <YYYY-MM-DD> | <prev-hash> | <cell> | <note>

depth      position of the line in the file; the root line is 0
username   the GitHub account that owns the fork
date       YYYY-MM-DD
prev-hash  first 12 hex chars of SHA-256 of the previous line's text
cell       x,y=c sets one canvas cell (0 <= x < 64, 0 <= y < 32, c is printable
           ASCII other than a space or |), or - to skip
note       optional: up to 80 printable ASCII characters, no |, no links

Lines are compared and hashed without their line ending or trailing
whitespace, so CRLF files and editors that trim trailing spaces break nothing.
Blank lines are ignored.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

from . import config

USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
DEPTH_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
HASH_RE = re.compile(r"^[0-9a-fA-F]{12}$")
CELL_RE = re.compile(r"^([0-9]{1,3}),([0-9]{1,3})=(.)$")
URL_RE = re.compile(
    r"(?i)(?:https?://|www\.|\b[a-z0-9-]+\.(?:com|net|org|io|dev|gg|xyz|co|me|app"
    r"|ly|link|site|online|info|biz|us|uk|ru|cn|tk|tv|to|sh|ai|so|fm|lol)\b)"
)
FIELDS_HELP = "depth | username | date | prev-hash | cell | note"


def canonical(text: str) -> str:
    """A line's text without its line ending or trailing whitespace."""
    return text.rstrip()


def read_lines(text: str) -> List[str]:
    """Split CHAIN.txt into canonical, non-blank lines."""
    if text.startswith("﻿"):
        text = text[1:]
    lines = []
    for raw in text.split("\n"):
        line = canonical(raw)
        if line.strip():
            lines.append(line)
    return lines


def decode(data: bytes) -> str:
    """Decode file bytes without ever failing; odd bytes survive a round trip."""
    return data.decode("utf-8", "surrogateescape")


def line_hash(text: str) -> str:
    """First 12 hex chars of SHA-256 of a line, as the next line's prev-hash."""
    data = canonical(text).encode("utf-8", "surrogateescape")
    return hashlib.sha256(data).hexdigest()[: config.HASH_LEN]


@dataclass(frozen=True)
class Cell:
    x: int
    y: int
    ch: str

    def __str__(self) -> str:
        return "%d,%d=%s" % (self.x, self.y, self.ch)


def parse_cell(text: str) -> Tuple[Optional[Cell], Optional[str]]:
    """Parse the cell field. Returns (cell or None, error or None)."""
    if text == "-":
        return None, None
    m = CELL_RE.match(text)
    if not m:
        return None, "cell must look like x,y=c (for example 12,5=#), or be - to skip"
    x, y, ch = int(m.group(1)), int(m.group(2)), m.group(3)
    if not (0 <= x < config.CANVAS_W and 0 <= y < config.CANVAS_H):
        return None, "cell %d,%d is off the canvas (x is 0-%d, y is 0-%d)" % (
            x, y, config.CANVAS_W - 1, config.CANVAS_H - 1)
    if not ("!" <= ch <= "~") or ch == "|":
        return None, "the cell character must be printable ASCII, not a space or |"
    return Cell(x, y, ch), None


def note_problems(note: str) -> List[str]:
    problems = []
    if len(note) > config.NOTE_MAX:
        problems.append("the note is %d characters; the limit is %d" % (len(note), config.NOTE_MAX))
    if any(not (" " <= ch <= "~") for ch in note):
        problems.append("the note must be plain printable ASCII")
    elif URL_RE.search(note):
        problems.append("notes can't contain links")
    return problems


@dataclass
class Line:
    """One parsed CHAIN.txt line. `errors` holds problems visible in the line
    alone; check_link() adds the ones that depend on where it sits."""

    text: str
    depth: Optional[int] = None
    user: str = ""
    date: str = ""
    prev_hash: str = ""
    cell: Optional[Cell] = None
    note: str = ""
    errors: List[str] = field(default_factory=list)


def parse_line(text: str) -> Line:
    line = Line(text=canonical(text))
    t, errors = line.text, line.errors
    if t != t.lstrip():
        errors.append("the line starts with a space")
    if any(not (" " <= ch <= "~") for ch in t):
        errors.append("the line must be plain printable ASCII")
    parts = [p.strip() for p in t.split("|")]
    if len(parts) < 5:
        errors.append("the line needs 6 fields separated by |: " + FIELDS_HELP)
        return line
    if len(parts) > 6:
        errors.append("too many | separators (a note can't contain |)")

    depth, user, date, prev_hash, cell = parts[:5]
    line.note = " | ".join(parts[5:])
    if DEPTH_RE.match(depth):
        line.depth = int(depth)
    else:
        errors.append("the depth must be a whole number, got %r" % depth)
    line.user = user
    if not USERNAME_RE.match(user):
        errors.append("%r doesn't look like a GitHub username" % user)
    line.date = date
    if not _valid_date(date):
        errors.append("the date must be a real date written YYYY-MM-DD, got %r" % date)
    if HASH_RE.match(prev_hash):
        line.prev_hash = prev_hash.lower()
    else:
        errors.append("the prev-hash must be 12 hex characters, got %r" % prev_hash)
    line.cell, cell_error = parse_cell(cell)
    if cell_error:
        errors.append(cell_error)
    errors.extend(note_problems(line.note))
    return line


def _valid_date(text: str) -> bool:
    if not DATE_RE.match(text):
        return False
    try:
        _dt.date(int(text[:4]), int(text[5:7]), int(text[8:]))
    except ValueError:
        return False
    return True


def check_link(line: Line, position: int, prev_text: Optional[str], owner: Optional[str]) -> List[str]:
    """Every problem with a link line at `position`, following `prev_text`,
    in a fork owned by `owner`. An empty list means the line is correct."""
    errors = list(line.errors)
    if line.depth is not None and line.depth != position:
        errors.append("the depth should be %d (one more than the line above), not %d"
                      % (position, line.depth))
    if owner is not None and line.user and line.user.lower() != owner.lower():
        errors.append("the username should be %s (the account that owns this fork), not %s"
                      % (owner, line.user))
    if prev_text is not None and line.prev_hash:
        want = line_hash(prev_text)
        if line.prev_hash != want:
            errors.append("the prev-hash should be %s (the hash of the line above), not %s"
                          % (want, line.prev_hash))
    return errors


def check_root(lines: List[str]) -> List[str]:
    if not lines:
        return ["CHAIN.txt is empty"]
    if lines[0] != config.ROOT_LINE:
        return ["line 0 must be exactly: " + config.ROOT_LINE]
    return []


def format_line(depth: int, user: str, date: str, prev_hash: str,
                cell: Optional[Cell] = None, note: str = "") -> str:
    text = "%d | %s | %s | %s | %s |" % (depth, user, date, prev_hash, cell or "-")
    return text + " " + note if note else text


def next_line(lines: List[str], user: str, date: str,
              cell: Optional[Cell] = None, note: str = "") -> str:
    """The line that extends `lines` by one link."""
    return format_line(len(lines), user, date, line_hash(lines[-1]), cell, note)


def canvas_rows(cells: Iterable[Optional[Cell]]) -> List[str]:
    """Replay cells in order onto a blank canvas."""
    grid = [[config.CANVAS_BLANK] * config.CANVAS_W for _ in range(config.CANVAS_H)]
    for cell in cells:
        if cell is not None:
            grid[cell.y][cell.x] = cell.ch
    return ["".join(row) for row in grid]


def safe_user(text: str) -> str:
    """Something from a line that claims to be a username, made safe to print."""
    cleaned = re.sub(r"[^A-Za-z0-9-]", "", text)[:39]
    return cleaned or "?"


def safe_text(text: str) -> str:
    """Untrusted text with anything outside printable ASCII replaced."""
    return "".join(ch if " " <= ch <= "~" else "?" for ch in text)


def today_utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
