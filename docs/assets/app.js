// The live chain page. Reads data.json (written by the tracker) and draws the
// hero, the tree, the depth chart, the canvas and the table view.

import {
  BLANK, CANVAS_H, CANVAS_W, STATUS_LABEL, ago, canvasRows, cellsOf, el, fmtDate, fmtTime,
  initTheme, loadData, repoUrl,
} from "./common.js";

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const STATUS_URL = "https://github.com/Londopy/the-long-fork/blob/main/STATUS.txt";
const KIND_LABEL = {
  tip: "the tip", main: "main chain", side: "side branch", root: "root", invalid: "invalid",
  excluded: "excluded", lost: "lost (fork deleted)", unverified: "unverified line",
};
const PROBLEMS = new Set(["invalid", "excluded", "lost", "unverified"]);

initTheme($("theme"));
main();

async function main() {
  let data;
  try {
    data = await loadData("data.json");
  } catch (err) {
    $("depth").textContent = "?";
    $("tipline").replaceChildren(
      el("span", { class: "error" }, "Couldn't load the chain data. "),
      el("a", { href: STATUS_URL }, "STATUS.txt"), " always has the tip.");
    return;
  }
  const byId = new Map(data.nodes.map((n) => [n.id, n]));
  hero(data);
  stats(data);
  legend();
  table(data);
  others(data, byId);
  canvas(data);
  $("footer").prepend("Last checked " + fmtTime(data.generated_at) + ". ");
  if (window.d3) {
    tree(data);
    chart(data);
  } else {
    $("tree-hint").textContent = "The interactive tree needs d3, which didn't load. The table below has every line.";
    $("chart").textContent = "The chart needs d3, which didn't load. The table below has every date.";
  }
}

function kindOf(n) {
  if (n.tip && n.status !== "root") return "tip";
  if (n.status === "valid") return n.main ? "main" : "side";
  return n.status;
}

function openable(n) {
  return ["root", "valid", "invalid", "excluded"].includes(n.status) ? repoUrl(n.repo) : null;
}

function timeOf(n) {
  return Date.parse(n.linked_at || n.first_seen || n.forked_at) || 0;
}

// Markers: the same shapes in the tree, the legend and the table, so status
// is never carried by color alone.

function svgEl(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  return node;
}

function markerParts(kind) {
  const ring = (r) => svgEl("circle", { r, class: "ring" });
  switch (kind) {
    case "tip": return [ring(12.5), svgEl("circle", { r: 7, class: "m-main" }), svgEl("circle", { r: 10.5, class: "m-tipring" })];
    case "main": return [ring(7), svgEl("circle", { r: 5, class: "m-main" })];
    case "side": return [ring(6.5), svgEl("circle", { r: 4.5, class: "m-side" })];
    case "root": return [ring(8.5), svgEl("circle", { r: 6.5, class: "m-root" })];
    case "invalid": return [ring(7), svgEl("path", { d: "M-4,-4L4,4M4,-4L-4,4", class: "m-bad" })];
    case "excluded": return [ring(7.5), svgEl("circle", { r: 5.5, class: "m-excluded" }),
      svgEl("path", { d: "M-3.6,3.6L3.6,-3.6", class: "m-bad", "stroke-width": 2 })];
    case "lost": return [ring(7.5), svgEl("circle", { r: 5, class: "m-lost" })];
    default: return [ring(5), svgEl("circle", { r: 3.5, class: "m-ghost" })];
  }
}

function glyph(kind, size = 16) {
  const svg = svgEl("svg", { viewBox: "-13 -13 26 26", width: size, height: size, "aria-hidden": "true" });
  svg.append(...markerParts(kind));
  return svg;
}

function legend() {
  const kinds = ["tip", "main", "side", "invalid", "excluded", "lost", "unverified"];
  $("legend").replaceChildren(...kinds.map((k) => el("li", {}, glyph(k), KIND_LABEL[k])));
}

// Hero and numbers

function hero(data) {
  const s = data.summary;
  $("depth").textContent = Number(s.depth).toLocaleString();
  const url = repoUrl(String(s.tip_url || "").replace("https://github.com/", ""));
  if (s.depth === 0) {
    $("tipline").textContent = "No links yet. Fork the root to be link 1.";
  } else {
    $("tipline").replaceChildren("The tip is ", el("a", { href: url }, el("strong", {}, "@" + s.tip)),
      ", linked " + ago(s.last_link_at) + ".");
  }
  if (url) $("fork-tip").href = url + "/fork";
  if (s.stalled) {
    const icon = svgEl("svg", { viewBox: "0 0 20 20", "aria-hidden": "true" });
    icon.append(svgEl("path", { d: "M10 2 19 18H1Z", fill: "var(--warning)" }),
      svgEl("path", { d: "M10 7.5v5M10 15.2v.1", stroke: "#0b0b0b", "stroke-width": 2, "stroke-linecap": "round" }));
    $("stall").replaceChildren(el("div", { class: "banner", role: "status" }, icon, el("div", {},
      el("strong", {}, "CHAIN STALLED"),
      "No new link since " + fmtDate(s.last_link_at) + ". Start a side branch and overtake.")));
  }
}

function stats(data) {
  const s = data.summary;
  $("stat-links").textContent = Number(s.total_links).toLocaleString();
  $("stat-branches").textContent = Number(s.side_branches).toLocaleString();
  $("stat-pending").textContent = Number(s.pending).toLocaleString();
  if (s.depth > 0) {
    $("stat-last").textContent = ago(s.last_link_at);
    $("stat-last-sub").textContent = fmtDate(s.last_link_at);
  } else {
    $("stat-last").textContent = "none yet";
    $("stat-last-sub").textContent = "be the first";
  }
}

// Table view

function table(data) {
  const order = data.nodes.slice().sort((a, b) => (timeOf(b) - timeOf(a)) || (b.depth - a.depth));
  const filters = {
    all: () => true,
    main: (n) => n.main,
    side: (n) => !n.main && n.status === "valid",
    problems: (n) => PROBLEMS.has(n.status),
  };
  const render = () => {
    const shown = order.filter(filters[$("filter").value] || filters.all);
    $("count").textContent = shown.length + " of " + order.length;
    $("rows").replaceChildren(...(shown.length ? shown.map(row)
      : [el("tr", {}, el("td", { colspan: 6, class: "muted" }, "Nothing here."))]));
  };
  $("filter").addEventListener("change", render);
  render();
}

function row(n) {
  const kind = kindOf(n);
  const url = openable(n);
  const why = n.status === "valid" || n.status === "root" ? [] : n.reasons.map((r) => el("span", { class: "why" }, r));
  const style = (n.warnings || []).map((w) => el("span", { class: "why" }, "style: " + w));
  return el("tr", {},
    el("td", { class: "num" }, String(n.depth)),
    el("td", {}, url ? el("a", { href: url }, "@" + n.owner) : "@" + n.owner),
    el("td", {}, el("span", { class: "status" }, glyph(kind, 14), KIND_LABEL[kind]), why, style),
    el("td", { class: "mono" }, n.date || ""),
    el("td", { class: "mono" }, n.cell || "-"),
    el("td", {}, n.note || ""));
}

function others(data, byId) {
  const list = (items, empty) => (items.length ? items : [el("li", { class: "dim" }, empty)]);
  $("pending").replaceChildren(...list(data.pending.map((p) => {
    const at = byId.get(p.node);
    const url = repoUrl(p.repo);
    return el("li", {}, url ? el("a", { href: url }, "@" + p.owner) : "@" + p.owner,
      el("span", { class: "dim" }, at ? " forked depth " + at.depth + " (@" + at.owner + ") " + ago(p.forked_at) : ""));
  }), "Nobody right now."));
  $("problems").replaceChildren(...list(data.problems.map((p) => {
    const url = repoUrl(p.repo);
    return el("li", {}, url ? el("a", { href: url }, "@" + p.owner) : "@" + p.owner,
      el("span", { class: "dim" }, ": " + p.reason));
  }), "None."));
}

// Canvas, with a replay along the main chain

function canvas(data) {
  const main = data.nodes.filter((n) => n.main).sort((a, b) => a.depth - b.depth);
  const scrub = $("scrub");
  scrub.max = String(main.length - 1);
  scrub.value = scrub.max;
  scrub.disabled = main.length < 2;
  const draw = () => {
    const i = Number(scrub.value);
    const rows = canvasRows(cellsOf(main.slice(0, i + 1)));
    const parts = [];
    rows.forEach((line, r) => {
      for (const m of line.matchAll(/(\.+)|([^.]+)/g)) parts.push(m[1] ? el("span", { class: "blank" }, m[1]) : m[2]);
      if (r < rows.length - 1) parts.push("\n");
    });
    $("canvas").replaceChildren(...parts);
    const set = rows.join("").split("").filter((c) => c !== BLANK).length;
    const n = main[i];
    $("scrub-value").textContent = i === main.length - 1 ? "depth " + n.depth + " (the tip)" : "depth " + n.depth + " (@" + n.owner + ")";
    $("canvas-note").textContent = set + " of " + CANVAS_W * CANVAS_H + " cells set. Each link may set one.";
  };
  scrub.addEventListener("input", draw);
  draw();
}

// The tree

function describe(n) {
  const kind = kindOf(n);
  if (kind === "unverified") {
    return [el("div", { class: "big" }, "Depth " + n.depth), el("div", {}, "An unverified line"),
      el("div", { class: "dim" }, "No fork in the network added it, so it isn't shown and nothing below it counts.")];
  }
  const parts = [
    el("div", { class: "big" }, n.status === "root" ? "The root" : "Depth " + n.depth),
    el("div", {}, "@" + n.owner + " · " + KIND_LABEL[kind]),
  ];
  if (n.linked_at && n.status !== "root") parts.push(el("div", { class: "dim" }, "linked " + fmtDate(n.linked_at)));
  if (n.cell) parts.push(el("div", { class: "dim" }, "cell " + n.cell));
  if (n.note && n.status !== "root") parts.push(el("div", {}, "“" + n.note + "”"));
  if (PROBLEMS.has(n.status) && n.reasons.length) parts.push(el("ul", {}, n.reasons.map((r) => el("li", {}, r))));
  if (openable(n)) parts.push(el("div", { class: "dim" }, "Click to open the fork."));
  return parts;
}

function labelOf(n) {
  const kind = kindOf(n);
  if (kind === "tip") return n.owner + " · tip";
  if (kind === "unverified") return "(unverified)";
  if (PROBLEMS.has(n.status)) return n.owner + " (" + STATUS_LABEL[n.status] + ")";
  return n.owner;
}

function placeTip(box, tip, clientX, clientY) {
  const rect = box.getBoundingClientRect();
  tip.hidden = false;
  let left = clientX - rect.left + 14;
  let top = clientY - rect.top + 14;
  if (left + tip.offsetWidth > rect.width - 8) left = Math.max(8, clientX - rect.left - tip.offsetWidth - 14);
  if (top + tip.offsetHeight > rect.height - 8) top = Math.max(8, clientY - rect.top - tip.offsetHeight - 14);
  tip.style.left = left + "px";
  tip.style.top = top + "px";
}

function tree(data) {
  const d3 = window.d3;
  const box = $("tree");
  const tipBox = $("tree-tip");
  let root;
  try {
    root = d3.stratify().id((d) => d.id).parentId((d) => d.parent)(data.nodes);
  } catch (err) {
    $("tree-hint").textContent = "The tree data didn't fit together; the table below has every line.";
    return;
  }
  const when = (d) => String(d.data.forked_at || d.data.first_seen || "");
  root.sort((a, b) => (Number(b.data.main) - Number(a.data.main)) || when(a).localeCompare(when(b)));
  d3.tree().nodeSize([40, 116]).separation((a, b) => (a.parent === b.parent ? 1 : 1.25))(root);
  let x0 = Infinity, x1 = -Infinity, y1 = 0;
  root.each((d) => { x0 = Math.min(x0, d.x); x1 = Math.max(x1, d.x); y1 = Math.max(y1, d.y); });

  const svg = d3.select(box).insert("svg", ":first-child")
    .attr("role", "group")
    .attr("aria-label", "The chain tree: " + data.nodes.length + " lines, depth " + data.summary.depth);
  const g = svg.append("g");
  const isMain = (l) => (l.source.data.main && l.target.data.main ? 1 : 0);
  g.append("g").selectAll("path")
    .data(root.links().sort((a, b) => isMain(a) - isMain(b)))
    .join("path")
    .attr("class", (l) => "link" + (isMain(l) ? " main" : ""))
    .attr("d", d3.linkHorizontal().x((d) => d.y).y((d) => d.x));

  const focusable = data.nodes.length <= 400;
  const nodes = g.append("g").selectAll("g")
    .data(root.descendants().sort((a, b) => Number(a.data.tip) - Number(b.data.tip)))
    .join("g")
    .attr("class", "node")
    .attr("transform", (d) => "translate(" + d.y + "," + d.x + ")")
    .attr("tabindex", focusable ? 0 : null)
    .attr("role", focusable ? "button" : null)
    .attr("aria-label", (d) => "Depth " + d.data.depth + ", @" + d.data.owner + ", " + KIND_LABEL[kindOf(d.data)]);
  nodes.append("circle").attr("class", "hit").attr("r", 13);
  nodes.each(function (d) { for (const part of markerParts(kindOf(d.data))) this.append(part); });
  nodes.append("text")
    .attr("class", (d) => "label" + (d.data.tip ? " tip" : ""))
    .attr("text-anchor", "middle")
    .attr("y", (d) => (d.data.tip ? -17 : -12))
    .text((d) => labelOf(d.data));

  const show = (event, d) => {
    tipBox.replaceChildren(...describe(d.data));
    if (event.clientX !== undefined && event.type.startsWith("pointer")) {
      placeTip(box, tipBox, event.clientX, event.clientY);
    } else {
      const r = event.currentTarget.getBoundingClientRect();
      placeTip(box, tipBox, r.right, r.bottom);
    }
  };
  const hide = () => { tipBox.hidden = true; };
  const open = (d) => {
    const url = openable(d.data);
    if (url) window.open(url, "_blank", "noopener");
  };
  nodes.on("pointerenter", show).on("pointermove", show).on("pointerleave", hide)
    .on("focus", show).on("blur", hide)
    .on("click", (event, d) => open(d))
    .on("keydown", (event, d) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(d); }
    });

  const hint = $("tree-hint");
  // A plain wheel scrolls the page; Ctrl/Cmd + wheel (and trackpad pinch,
  // which arrives as a ctrl-wheel) zooms. Dragging and touch pinch as usual.
  const zoom = d3.zoom().scaleExtent([0.02, 2.5])
    .filter((event) => (event.type === "wheel" ? event.ctrlKey || event.metaKey : !event.button))
    .on("zoom", (event) => {
    g.attr("transform", event.transform);
    const far = event.transform.k < 0.6;
    box.classList.toggle("far", far);
    hint.textContent = far ? "Zoom in to see names." : "";
    hide();
  });
  svg.call(zoom).on("dblclick.zoom", null);

  const fitTransform = () => {
    const W = box.clientWidth, H = box.clientHeight, pad = 36;
    const cw = y1 + 170, ch = Math.max(x1 - x0, 1);
    const k = Math.min((W - 2 * pad) / cw, (H - 2 * pad) / ch, 1.4);
    return d3.zoomIdentity.translate(pad + Math.max(0, (W - 2 * pad - cw * k) / 2), H / 2 - ((x0 + x1) / 2) * k).scale(k);
  };
  const tipTransform = (k) => {
    const tip = root.find((d) => d.data.tip) || root;
    return d3.zoomIdentity.translate(box.clientWidth * 0.62 - tip.y * k, box.clientHeight / 2 - tip.x * k).scale(k);
  };
  // Frame the tree once the box has a size (a hidden tab has none), and keep
  // re-framing on resize until the visitor pans or zooms themselves.
  let touched = false;
  zoom.on("start", (event) => { if (event.sourceEvent) touched = true; });
  const frame = () => {
    if (touched || !box.clientWidth || !box.clientHeight) return;
    const fit = fitTransform();
    svg.call(zoom.transform, fit.k >= 0.6 ? fit : tipTransform(1));
  };
  frame();
  new ResizeObserver(frame).observe(box);
  const animate = (t) => svg.transition().duration(matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 350).call(zoom.transform, t);
  $("fit").addEventListener("click", () => animate(fitTransform()));
  $("to-tip").addEventListener("click", () => animate(tipTransform(Math.max(1, d3.zoomTransform(svg.node()).k))));
}

// Depth over time: one series, so no legend box; the title names it.

function chart(data) {
  const d3 = window.d3;
  const box = $("chart");
  const root = data.nodes.find((n) => n.status === "root");
  const valid = data.nodes.filter((n) => n.status === "valid" && n.linked_at)
    .sort((a, b) => Date.parse(a.linked_at) - Date.parse(b.linked_at));
  const pts = [{ t: Date.parse(root.forked_at || root.first_seen), depth: 0, n: root }];
  let best = 0;
  for (const n of valid) {
    if (n.depth > best) {
      best = n.depth;
      pts.push({ t: Math.max(Date.parse(n.linked_at), pts[pts.length - 1].t), depth: n.depth, n });
    }
  }
  if (pts.length < 2) {
    box.replaceChildren(el("p", { class: "muted" }, "No links yet. The line starts with link 1."));
    return;
  }
  const end = Math.max(Date.parse(data.generated_at) || 0, pts[pts.length - 1].t);
  const tipBox = el("div", { class: "tip-box", hidden: true });

  const draw = () => {
    const W = Math.max(260, box.clientWidth), H = 240;
    const m = { t: 14, r: 70, b: 30, l: 34 };
    const x = d3.scaleUtc([pts[0].t, end], [m.l, W - m.r]);
    const y = d3.scaleLinear([0, best], [H - m.b, m.t]).nice();
    const svg = d3.create("svg").attr("viewBox", "0 0 " + W + " " + H).attr("width", W).attr("height", H)
      .attr("tabindex", 0).attr("role", "img")
      .attr("aria-label", "Deepest link that counts, over time. It reached depth " + best + ". Use the arrow keys to step through the records.");
    const yTicks = y.ticks(Math.min(5, best)).filter(Number.isInteger);
    svg.append("g").selectAll("line").data(yTicks).join("line")
      .attr("class", (d) => (d === 0 ? "baseline" : "gridline"))
      .attr("x1", m.l).attr("x2", W - m.r).attr("y1", y).attr("y2", y);
    svg.append("g").selectAll("text").data(yTicks).join("text")
      .attr("class", "tick").attr("x", m.l - 8).attr("y", y).attr("dy", "0.32em").attr("text-anchor", "end")
      .text((d) => d.toLocaleString());
    svg.append("g").selectAll("text").data(x.ticks(Math.max(2, Math.floor((W - m.l - m.r) / 110)))).join("text")
      .attr("class", "tick").attr("x", x).attr("y", H - 8).attr("text-anchor", "middle").text(x.tickFormat());
    const series = pts.concat([{ t: end, depth: best }]);
    svg.append("path").attr("class", "area")
      .attr("d", d3.area().curve(d3.curveStepAfter).x((d) => x(d.t)).y0(y(0)).y1((d) => y(d.depth))(series));
    svg.append("path").attr("class", "line")
      .attr("d", d3.line().curve(d3.curveStepAfter).x((d) => x(d.t)).y((d) => y(d.depth))(series));
    svg.append("circle").attr("class", "dot").attr("r", 4.5).attr("cx", x(end)).attr("cy", y(best));
    svg.append("text").attr("class", "endlabel").attr("x", x(end) + 10).attr("y", y(best)).attr("dy", "0.32em")
      .text("depth " + best.toLocaleString());

    const cross = svg.append("line").attr("class", "cross").attr("y1", m.t).attr("y2", H - m.b).style("display", "none");
    const hot = svg.append("circle").attr("class", "dot").attr("r", 4.5).style("display", "none");
    const bisect = d3.bisector((d) => d.t).center;
    let index = pts.length - 1;
    const show = (i) => {
      index = i;
      const p = pts[i];
      const px = x(p.t), py = y(p.depth);
      cross.style("display", null).attr("x1", px).attr("x2", px);
      hot.style("display", null).attr("cx", px).attr("cy", py);
      tipBox.replaceChildren(
        el("div", { class: "big" }, "depth " + p.depth.toLocaleString()),
        el("div", { class: "dim" }, (i === 0 ? "the root" : "@" + p.n.owner) + " · " + fmtDate(new Date(p.t).toISOString())));
      tipBox.hidden = false;
      const left = px + 12 + tipBox.offsetWidth > W ? px - tipBox.offsetWidth - 12 : px + 12;
      tipBox.style.left = Math.max(0, left) + "px";
      tipBox.style.top = Math.max(0, py - tipBox.offsetHeight - 8) + "px";
    };
    const hide = () => {
      cross.style("display", "none");
      hot.style("display", "none");
      tipBox.hidden = true;
    };
    svg.append("rect").attr("class", "hitbox").attr("x", m.l).attr("y", 0)
      .attr("width", W - m.l - m.r + 20).attr("height", H - m.b)
      .on("pointermove", (event) => show(bisect(pts, +x.invert(d3.pointer(event)[0]))))
      .on("pointerleave", hide);
    svg.on("focus", () => show(index)).on("blur", hide).on("keydown", (event) => {
      if (event.key === "ArrowLeft") { event.preventDefault(); show(Math.max(0, index - 1)); }
      if (event.key === "ArrowRight") { event.preventDefault(); show(Math.min(pts.length - 1, index + 1)); }
    });
    box.replaceChildren(svg.node(), tipBox);
  };

  let width = 0;
  new ResizeObserver(() => {
    if (Math.abs(box.clientWidth - width) > 4) {
      width = box.clientWidth;
      draw();
    }
  }).observe(box);
}
