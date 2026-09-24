// The link helper: reads the parent fork's CHAIN.txt and writes the next line.
// Same rules as tools/link.py and the tracker (see common.js).

import {
  BLANK, CANVAS_H, CANVAS_W, USERNAME_RE, canvasRows, cellsOf, el, formatLine, initTheme,
  lineHash, loadData, noteProblems, parseCell, parseLine, pathTo, readLines, repoUrl, todayUtc,
} from "./common.js";

const $ = (id) => document.getElementById(id);
const state = {
  data: null, byId: new Map(), repo: "the-long-fork", owner: "", branch: "main",
  lines: null, hash: "", cells: [],
};

initTheme($("theme"));
buildPicker();
wire();
start();

async function start() {
  try {
    state.data = await loadData("../data.json");
    state.byId = new Map(state.data.nodes.map((n) => [n.id, n]));
    state.repo = state.data.repo_name || state.repo;
    const s = state.data.summary;
    const url = repoUrl(String(s.tip_url || "").replace("https://github.com/", ""));
    $("tip-text").replaceChildren("The tip right now is ", url ? el("a", { href: url }, "@" + s.tip) : "@" + s.tip,
      s.depth ? ", at depth " + s.depth + "." : ", the root itself: nobody has linked yet.");
    if (url) $("fork-link").href = url + "/fork";
    $("parent").value = new URLSearchParams(location.search).get("parent") || s.tip;
    await loadParent();
  } catch (err) {
    $("tip-text").textContent = "Couldn't load the current tip; the root repo's STATUS.txt lists it.";
  }
  update();
}

function wire() {
  $("parent-form").addEventListener("submit", (event) => { event.preventDefault(); loadParent(); });
  for (const id of ["user", "note", "cx", "cy", "cch"]) $(id).addEventListener("input", () => { paint(); update(); });
  $("clear-cell").addEventListener("click", () => {
    for (const id of ["cx", "cy", "cch"]) $(id).value = "";
    paint();
    update();
  });
  $("copy-line").addEventListener("click", () => copy($("line").textContent, $("copy-line")));
  $("copy-message").addEventListener("click", () => copy($("message").textContent, $("copy-message")));
}

function status(text, bad = false) {
  const node = $("parent-status");
  node.textContent = text;
  node.classList.toggle("bad", bad);
}

async function loadParent() {
  const owner = $("parent").value.trim().replace(/^@/, "");
  if (!USERNAME_RE.test(owner)) {
    status("That doesn't look like a GitHub username.", true);
    return;
  }
  const s = state.data && state.data.summary;
  const isTip = !!s && String(s.tip).toLowerCase() === owner.toLowerCase();
  const branch = isTip ? s.tip_branch || "main" : "HEAD";
  status("Reading @" + owner + "'s CHAIN.txt…");
  let text;
  try {
    const response = await fetch("https://raw.githubusercontent.com/" + owner + "/" + state.repo + "/" + branch + "/CHAIN.txt", { cache: "no-cache" });
    if (!response.ok) throw new Error(response.status === 404 ? "not found" : "HTTP " + response.status);
    text = await response.text();
  } catch (err) {
    state.lines = null;
    status("Couldn't read " + owner + "/" + state.repo + "/CHAIN.txt (" + err.message + "). Check the name, and that the fork exists.", true);
    $("parent-last").hidden = true;
    update();
    return;
  }
  const lines = readLines(text);
  if (!lines.length) {
    state.lines = null;
    status("That CHAIN.txt is empty.", true);
    update();
    return;
  }
  const last = lines[lines.length - 1];
  state.owner = owner;
  state.branch = branch === "HEAD" ? "main" : branch;
  state.lines = lines;
  state.hash = await lineHash(last);
  const known = state.data && state.data.nodes.find((n) => n.line === last && n.depth === lines.length - 1 && n.status !== "unverified");
  state.cells = known ? cellsOf(pathTo(state.byId, known.id)) : lines.map((line) => parseLine(line).cell);
  status("Read @" + owner + "'s CHAIN.txt: " + lines.length + " lines, so your link is depth " + lines.length + "."
    + (s && !isTip ? " That fork isn't the tip, so your link starts or continues a side branch." : "")
    + (known ? "" : " (The tracker hasn't seen this fork yet, so the canvas below counts every cell in it.)"));
  $("parent-last").hidden = false;
  $("parent-last").textContent = "Its last line:\n" + last;
  paint();
  update();
}

// The canvas picker: 64 x 32 spans, one click handler.

function buildPicker() {
  const picker = $("picker");
  const frag = document.createDocumentFragment();
  for (let y = 0; y < CANVAS_H; y++) {
    for (let x = 0; x < CANVAS_W; x++) {
      const span = document.createElement("span");
      span.dataset.x = x;
      span.dataset.y = y;
      span.title = x + "," + y;
      frag.append(span);
    }
  }
  picker.append(frag);
  picker.addEventListener("click", (event) => {
    const span = event.target.closest("span");
    if (!span) return;
    $("cx").value = span.dataset.x;
    $("cy").value = span.dataset.y;
    $("cch").focus();
    paint();
    update();
  });
  paint();
}

function chosen() {
  const x = $("cx").value.trim(), y = $("cy").value.trim(), ch = $("cch").value;
  return { x, y, ch, any: x !== "" || y !== "" || ch !== "" };
}

function paint() {
  const rows = canvasRows(state.cells);
  const pick = chosen();
  const px = Number(pick.x), py = Number(pick.y);
  const spans = $("picker").children;
  for (let i = 0; i < spans.length; i++) {
    const x = i % CANVAS_W, y = Math.floor(i / CANVAS_W);
    const selected = pick.x !== "" && pick.y !== "" && x === px && y === py;
    const ch = selected && pick.ch ? pick.ch : rows[y][x];
    spans[i].textContent = ch;
    spans[i].className = (ch === BLANK && !selected ? "blank" : "") + (selected ? " sel" : "");
  }
}

// The line

function update() {
  const problems = [];
  const user = $("user").value.trim().replace(/^@/, "");
  const note = $("note").value.trim();
  $("note-count").textContent = note.length + "/80";
  $("note-count").classList.toggle("over", note.length > 80);

  let cell = null;
  const pick = chosen();
  if (pick.any) {
    if (pick.x === "" || pick.y === "" || pick.ch === "") {
      problems.push("Finish the canvas cell (x, y and a character), or press Skip the canvas.");
    } else {
      const parsed = parseCell(pick.x + "," + pick.y + "=" + pick.ch);
      if (parsed.error) problems.push("Canvas: " + parsed.error + ".");
      cell = parsed.cell;
    }
  }
  if (!state.lines) problems.push("Read the CHAIN.txt of the fork you forked (step 2).");
  if (!user) problems.push("Enter your GitHub username (step 3).");
  else if (!USERNAME_RE.test(user)) problems.push("That doesn't look like a GitHub username.");
  else if (state.owner && user.toLowerCase() === state.owner.toLowerCase()) {
    problems.push("Step 2 should be the fork you forked, not your own.");
  } else if (state.lines && parseLine(state.lines[state.lines.length - 1]).user.toLowerCase() === user.toLowerCase()) {
    problems.push("That CHAIN.txt already ends with your line.");
  }
  for (const p of noteProblems(note)) problems.push("Note: " + p + ".");

  let line = "";
  if (!problems.length) {
    line = formatLine(state.lines.length, user, todayUtc(), state.hash, cell, note);
    for (const p of parseLine(line).errors) problems.push(p);
  }
  $("problems").replaceChildren(...problems.map((p) => el("li", {}, p)));
  const ready = !problems.length;
  $("line").textContent = ready ? line : "Fill in the steps above.";
  $("line").classList.toggle("ready", ready);
  $("message").textContent = ready ? "link " + state.lines.length + ": " + user : "link N: your-username";
  $("copy-line").disabled = !ready;
  $("copy-message").disabled = !ready;
  const edit = $("edit-link");
  if (ready) {
    edit.href = "https://github.com/" + user + "/" + state.repo + "/edit/" + state.branch + "/CHAIN.txt";
    edit.classList.remove("disabled");
    edit.removeAttribute("aria-disabled");
  } else {
    edit.removeAttribute("href");
    edit.classList.add("disabled");
    edit.setAttribute("aria-disabled", "true");
  }
}

async function copy(text, button) {
  const label = button.textContent;
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = "Copied";
  } catch (err) {
    button.textContent = "Select and copy it by hand";
  }
  setTimeout(() => { button.textContent = label; }, 1600);
}
