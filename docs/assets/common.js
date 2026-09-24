// Shared by every page: theme, data loading, small DOM helpers, and a mirror
// of tools/longfork/chain.py so the link helper checks lines exactly like the
// tracker does. Untrusted text only ever goes in through textContent.

export const CANVAS_W = 64;
export const CANVAS_H = 32;
export const BLANK = ".";
export const NOTE_MAX = 80;
export const USERNAME_RE = /^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$/;
const DEPTH_RE = /^(?:0|[1-9][0-9]*)$/;
const DATE_RE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;
const HASH_RE = /^[0-9a-fA-F]{12}$/;
const CELL_RE = /^([0-9]{1,3}),([0-9]{1,3})=(.)$/;
const URL_RE = /(?:https?:\/\/|www\.|\b[a-z0-9-]+\.(?:com|net|org|io|dev|gg|xyz|co|me|app|ly|link|site|online|info|biz|us|uk|ru|cn|tk|tv|to|sh|ai|so|fm|lol)\b)/i;
const REPO_RE = /^[A-Za-z0-9-]+\/[A-Za-z0-9._-]+$/;

// Theme: system by default; a choice is remembered per browser.
export function initTheme(button) {
  const order = ["system", "light", "dark"];
  let choice = "system";
  try { choice = localStorage.getItem("theme") || "system"; } catch (e) { /* storage blocked */ }
  const apply = () => {
    if (choice === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = choice;
    if (button) {
      button.textContent = { system: "Theme: auto", light: "Theme: light", dark: "Theme: dark" }[choice];
      button.setAttribute("aria-label", "Color theme (" + choice + "); click to change");
    }
  };
  apply();
  if (button) {
    button.addEventListener("click", () => {
      choice = order[(order.indexOf(choice) + 1) % order.length];
      try { localStorage.setItem("theme", choice); } catch (e) { /* fine */ }
      apply();
      document.dispatchEvent(new CustomEvent("themechange"));
    });
  }
}

export async function loadData(url = "data.json") {
  const response = await fetch(url, { cache: "no-cache" });
  if (!response.ok) throw new Error("data.json: HTTP " + response.status);
  return response.json();
}

// el("a", {href: "...", class: "x"}, "text", child) -- text arguments become text nodes.
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function repoUrl(fullName) {
  return typeof fullName === "string" && REPO_RE.test(fullName) ? "https://github.com/" + fullName : null;
}

const DAY = 86400000;
const dateFmt = new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
const timeFmt = new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC", timeZoneName: "short" });

export function fmtDate(iso) {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? "" : dateFmt.format(t);
}

export function fmtTime(iso) {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? "" : timeFmt.format(t);
}

export function daysSince(iso, now = Date.now()) {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : Math.floor((now - t) / DAY);
}

export function ago(iso) {
  const days = daysSince(iso);
  if (days === null) return "";
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  return days + " days ago";
}

// The CHAIN.txt line format (see tools/longfork/chain.py).

export function canonical(text) {
  return text.replace(/\s+$/, "");
}

export function readLines(text) {
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
  return text.split("\n").map(canonical).filter((line) => line.trim() !== "");
}

export async function lineHash(text) {
  const bytes = new TextEncoder().encode(canonical(text));
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return Array.from(digest, (b) => b.toString(16).padStart(2, "0")).join("").slice(0, 12);
}

const printable = (ch) => ch >= " " && ch <= "~";

export function parseCell(text) {
  if (text === "-") return { cell: null, error: null };
  const m = CELL_RE.exec(text);
  if (!m) return { cell: null, error: "the cell must look like x,y=c (for example 12,5=#), or be - to skip" };
  const x = Number(m[1]), y = Number(m[2]), ch = m[3];
  if (x >= CANVAS_W || y >= CANVAS_H) {
    return { cell: null, error: `cell ${x},${y} is off the canvas (x is 0-${CANVAS_W - 1}, y is 0-${CANVAS_H - 1})` };
  }
  if (!(ch >= "!" && ch <= "~") || ch === "|") {
    return { cell: null, error: "the cell character must be printable ASCII, not a space or |" };
  }
  return { cell: { x, y, ch }, error: null };
}

export function noteProblems(note) {
  const problems = [];
  if (note.length > NOTE_MAX) problems.push(`the note is ${note.length} characters; the limit is ${NOTE_MAX}`);
  if ([...note].some((ch) => !printable(ch))) problems.push("the note must be plain printable ASCII");
  else if (URL_RE.test(note)) problems.push("notes can't contain links");
  if (note.includes("|")) problems.push("a note can't contain |");
  return problems;
}

export function parseLine(text) {
  const line = { text: canonical(text), depth: null, user: "", date: "", prevHash: "", cell: null, note: "", errors: [] };
  const parts = line.text.split("|").map((p) => p.trim());
  if (parts.length < 5) {
    line.errors.push("the line needs 6 fields separated by |");
    return line;
  }
  const [depth, user, date, prevHash, cell] = parts;
  line.note = parts.slice(5).join(" | ");
  if (DEPTH_RE.test(depth)) line.depth = Number(depth);
  else line.errors.push("the depth must be a whole number");
  line.user = user;
  if (!USERNAME_RE.test(user)) line.errors.push("that doesn't look like a GitHub username");
  line.date = date;
  if (!DATE_RE.test(date)) line.errors.push("the date must be written YYYY-MM-DD");
  if (HASH_RE.test(prevHash)) line.prevHash = prevHash.toLowerCase();
  else line.errors.push("the prev-hash must be 12 hex characters");
  const parsed = parseCell(cell);
  line.cell = parsed.cell;
  if (parsed.error) line.errors.push(parsed.error);
  return line;
}

export function formatLine(depth, user, date, prevHash, cell, note) {
  const cellText = cell ? `${cell.x},${cell.y}=${cell.ch}` : "-";
  const text = `${depth} | ${user} | ${date} | ${prevHash} | ${cellText} |`;
  return note ? text + " " + note : text;
}

export function todayUtc() {
  return new Date().toISOString().slice(0, 10);
}

export function canvasRows(cells) {
  const grid = Array.from({ length: CANVAS_H }, () => Array(CANVAS_W).fill(BLANK));
  for (const cell of cells) if (cell) grid[cell.y][cell.x] = cell.ch;
  return grid.map((row) => row.join(""));
}

// The node path from the root to `id`, using data.json's nodes.
export function pathTo(byId, id) {
  const path = [];
  for (let cur = byId.get(id); cur; cur = byId.get(cur.parent)) path.push(cur);
  return path.reverse();
}

// Cells that count along a path: from valid links and lost ones.
export function cellsOf(path) {
  return path
    .filter((n) => n.cell && (n.status === "valid" || n.status === "lost"))
    .map((n) => parseCell(n.cell).cell);
}

export const STATUS_LABEL = {
  root: "root", valid: "counts", invalid: "invalid", excluded: "excluded",
  lost: "lost", unverified: "unverified",
};
