/* Ulanzi Studio for Linux — front-end (no build step, no framework). */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : document.createTextNode(kid));
  return el;
}

async function api(method, url, body, isForm = false) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) opts.body = body;
    else { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  }
  const r = await fetch(url, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    toast(typeof msg === "string" ? msg : JSON.stringify(msg), "error");
    throw new Error(msg);
  }
  return r.headers.get("content-type")?.includes("json") ? r.json() : r;
}

function toast(msg, kind = "") {
  const t = h("div", { class: `toast ${kind}` }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "error" ? 7000 : 3000);
}

const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const clone = (o) => JSON.parse(JSON.stringify(o));

// ------------------------------------------------------------------ state
const S = {
  state: null, actions: [], byType: {}, profile: null, pageId: null,
  selected: null, editState: 0, clipboard: null, apps: null, glyphs: null, ownSaveAt: 0,
};
const ICON = 196;

function currentPage() { return S.profile.pages.concat(S.profile.folders).find(p => p.id === S.pageId) || S.profile.pages[0]; }
function keyAt(i) { return currentPage().keys[String(i)] || null; }
function fontOf(st) { return st?.font || S.profile.default_font; }
function iconUrl(name) { return name ? `/icons/${name}` : null; }
function glyphChar(code) { return String.fromCodePoint(parseInt(code, 16)); }
const FA_FAMILY = { s: '900 1px "FA Solid"', r: '400 1px "FA Regular"', b: '400 1px "FA Brands"' };
function faFont(style, px) { return FA_FAMILY[style].replace("1px", `${px}px`); }

async function loadGlyphs() {
  if (!S.glyphs) {
    S.glyphs = await (await fetch("/static/vendor/fontawesome/icons.json")).json();
    S.glyphMap = {};
    for (const g of S.glyphs) if (!S.glyphMap[g[0]]) S.glyphMap[g[0]] = g;
  }
  return S.glyphs;
}

// ------------------------------------------------------------- icon render
async function ensureFonts() {
  await Promise.all([document.fonts.load('900 40px "FA Solid"'), document.fonts.load('400 40px "FA Regular"'), document.fonts.load('400 40px "FA Brands"')]);
}

function loadImage(src) {
  return new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = src; });
}

/** design: {kind:'glyph'|'image'|'none', glyph:[name,code,style], bg, fg, scale, round, fit, src} */
async function drawDesign(canvas, d, img) {
  const c = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  c.clearRect(0, 0, W, H);
  c.fillStyle = "#000"; c.fillRect(0, 0, W, H);
  c.save();
  if (d.round) { c.beginPath(); c.roundRect(0, 0, W, H, W * 0.15); c.clip(); }
  c.fillStyle = d.bg || "#000"; c.fillRect(0, 0, W, H);
  if (d.kind === "glyph" && d.glyph) {
    await ensureFonts();
    const px = W * (d.scale || 50) / 100;
    c.font = faFont(d.glyph[2], px);
    c.fillStyle = d.fg || "#fff"; c.textAlign = "center"; c.textBaseline = "middle";
    c.fillText(glyphChar(d.glyph[1]), W / 2, H * 0.47);
  } else if (d.kind === "image" && img) {
    const fit = d.fit || "cover";
    const pad = fit === "pad" ? W * (1 - (d.scale || 70) / 100) / 2 : 0;
    const bw = W - 2 * pad, bh = H - 2 * pad;
    const r = fit === "cover" ? Math.max(bw / img.width, bh / img.height) : Math.min(bw / img.width, bh / img.height);
    const w = img.width * r, hh = img.height * r;
    c.drawImage(img, (W - w) / 2, (H - hh) / 2, w, hh);
  }
  c.restore();
}

async function uploadCanvas(canvas) {
  const blob = await new Promise(r => canvas.toBlob(r, "image/png"));
  const fd = new FormData(); fd.append("file", blob, "icon.png"); fd.append("kind", "icon");
  return (await api("POST", "/api/icons", fd, true)).name;
}

async function renderDesign(d) {
  const cv = h("canvas", { width: ICON, height: ICON });
  let img = null;
  if (d.kind === "image" && d.src) img = await loadImage(d.src);
  await drawDesign(cv, d, img);
  return uploadCanvas(cv);
}

async function defaultDesign(def, stateIdx) {
  await loadGlyphs();
  const g = S.glyphMap[def.icon] || S.glyphMap["circle-question"];
  const bg = stateIdx === 1 ? "#c92a2a" : def.color;
  return { kind: "glyph", glyph: [g[0], g[1], g[2]], bg, fg: "#ffffff", scale: 48, round: true, auto: true };
}

// ---------------------------------------------------------------- loading
async function loadAll() {
  [S.state, S.actions] = await Promise.all([api("GET", "/api/state"), api("GET", "/api/actions")]);
  S.byType = Object.fromEntries(S.actions.map(a => [a.type, a]));
  S.profile = await api("GET", `/api/profiles/${S.state.active_profile}`);
  S.pageId = S.state.current_page;
  if (!S.profile.pages.concat(S.profile.folders).some(p => p.id === S.pageId)) S.pageId = S.profile.pages[0].id;
  renderAll();
}

async function reloadProfile() {
  S.profile = await api("GET", `/api/profiles/${S.state.active_profile}`);
  if (!S.profile.pages.concat(S.profile.folders).some(p => p.id === S.pageId)) S.pageId = S.profile.pages[0].id;
}

function renderAll() {
  renderTop(); renderBanner(); renderDeck(); renderPagebar(); renderCrumbs(); renderLibrary(); renderInspector();
}

// -------------------------------------------------------------------- top
function renderTop() {
  const d = S.state.device;
  $("#devicePill").classList.toggle("on", d.connected);
  $("#deviceName").textContent = d.connected ? `${d.product || "Ulanzi Deck"}` : "No device";
  $("#devicePill").title = d.connected ? `${d.vid}:${d.pid}  ${d.path}` : (d.hint || "");
  const sel = $("#profileSelect");
  sel.replaceChildren(...S.state.profiles.map(p => h("option", { value: p.id, selected: p.id === S.state.active_profile }, p.name)));
  $("#brightness").value = S.state.settings.brightness;
  $("#brightnessVal").textContent = S.state.settings.brightness + "%";
}

function renderBanner() {
  const b = $("#banner");
  const d = S.state.device, kb = S.state.keyboard;
  const msgs = [];
  if (!d.connected && d.hint) msgs.push(h("div", {}, h("b", {}, "Device: "), d.hint));
  if (kb.backend !== "uinput") msgs.push(h("div", {}, h("b", {}, "Keyboard: "), kb.error || "Hotkeys use xdotool, which only reaches X11/XWayland windows. Install the udev rule (see README) for Wayland support."));
  b.replaceChildren(...msgs);
  b.hidden = !msgs.length;
}

// ------------------------------------------------------------------- deck
function keyEl(i, key, opts = {}) {
  const st = key ? key.states[Math.min(opts.state ?? key.state, key.states.length - 1)] : null;
  const el = h("div", { class: "key" + (key ? "" : " empty"), "data-index": i });
  if (st?.icon) el.append(h("img", { src: iconUrl(st.icon), alt: "" }));
  const f = fontOf(st);
  if (st?.title && f.show_title) {
    el.append(h("div", {
      class: `title ${f.align}`,
      style: { color: "#" + f.color.replace("#", ""), fontSize: `calc(var(--key) * ${f.size} / 92)`, fontWeight: f.weight > 90 ? 700 : 500 },
    }, st.title));
  }
  if (key && key.states.length > 1 && !opts.noBadge) el.append(h("span", { class: "badge" }, `${key.state + 1}/${key.states.length}`));
  if (key?.long_press && !opts.noBadge) el.append(h("span", { class: "badge lp", title: "Has long-press action" }, "⏱"));
  return el;
}

function renderDeck() {
  const deck = $("#deck");
  const kids = [];
  for (let i = 0; i < S.state.layout.buttons; i++) {
    const key = keyAt(i);
    const el = keyEl(i, key);
    if (S.selected?.kind === "key" && S.selected.index === i) el.classList.add("selected");
    wireKey(el, i, key);
    kids.push(el);
  }
  const info = h("div", { class: "key info" + (S.selected?.kind === "info" ? " selected" : ""), "data-index": S.state.layout.info_index });
  info.append(h("div", { class: "info-view", id: "infoView" }));
  info.addEventListener("click", () => select({ kind: "info" }));
  info.addEventListener("dragover", e => { if (e.dataTransfer.types.includes("application/x-action")) { e.preventDefault(); info.classList.add("drop"); } });
  info.addEventListener("dragleave", () => info.classList.remove("drop"));
  info.addEventListener("drop", async e => {
    e.preventDefault(); info.classList.remove("drop");
    const t = e.dataTransfer.getData("application/x-action");
    if (t) { await patchProfile({ info_action: { type: t, settings: defaultSettings(S.byType[t]) } }); select({ kind: "info" }); }
  });
  kids.push(info);
  deck.replaceChildren(...kids);
  updateInfoView();
}

function wireKey(el, i, key) {
  el.addEventListener("click", () => select({ kind: "key", index: i }));
  el.addEventListener("dblclick", () => { if (key?.action) api("POST", `/api/press/${i}`); });
  el.addEventListener("contextmenu", e => { e.preventDefault(); select({ kind: "key", index: i }); keyMenu(e.clientX, e.clientY, i); });
  if (key) {
    el.draggable = true;
    el.addEventListener("dragstart", e => { e.dataTransfer.setData("application/x-key", String(i)); e.dataTransfer.effectAllowed = "copyMove"; });
  }
  el.addEventListener("dragover", e => {
    const ty = e.dataTransfer.types;
    if (ty.includes("application/x-action") || ty.includes("application/x-key")) {
      e.preventDefault(); el.classList.add("drop");
      e.dataTransfer.dropEffect = ty.includes("application/x-key") ? (e.ctrlKey ? "copy" : "move") : "copy";
    }
  });
  el.addEventListener("dragleave", () => el.classList.remove("drop"));
  el.addEventListener("drop", async e => {
    e.preventDefault(); el.classList.remove("drop");
    const t = e.dataTransfer.getData("application/x-action");
    const src = e.dataTransfer.getData("application/x-key");
    if (t) await assignAction(i, t);
    else if (src !== "" && Number(src) !== i) await moveKey(Number(src), i, e.ctrlKey);
  });
}

function updateInfoView() {
  const v = $("#infoView");
  if (!v) return;
  const mode = S.profile.info_mode;
  const now = new Date();
  const twelve = S.state.settings.twelve_hour;
  const pad = n => String(n).padStart(2, "0");
  let hh = now.getHours();
  const suffix = twelve ? (hh >= 12 ? " PM" : " AM") : "";
  if (twelve) hh = hh % 12 || 12;
  const time = `${pad(hh)}:${pad(now.getMinutes())}`;
  const date = `${now.getFullYear()}/${pad(now.getMonth() + 1)}/${pad(now.getDate())}`;
  const wd = now.toLocaleDateString("en", { weekday: "short" });
  let content;
  if (mode === 2) {
    content = S.profile.info_background ? h("img", { src: iconUrl(S.profile.info_background), style: { width: "100%", height: "100%", objectFit: "cover" } }) : h("div", { class: "small" }, "Background image");
  } else if (mode === 0) {
    content = h("div", { class: "stats" }, h("div", {}, h("b", {}, "—"), "CPU"), h("div", {}, h("b", {}, "—"), "RAM"), h("div", {}, h("b", {}, "—"), "GPU"));
  } else if (mode === 1) {
    const hr = (now.getHours() % 12 + now.getMinutes() / 60) * 30, mn = now.getMinutes() * 6, sc = now.getSeconds() * 6;
    content = h("div", { class: "dial" },
      h("i", { style: { width: "3px", height: "20px", marginLeft: "-1.5px", transform: `rotate(${hr}deg)` } }),
      h("i", { style: { width: "2px", height: "28px", marginLeft: "-1px", transform: `rotate(${mn}deg)` } }),
      h("i", { style: { width: "1px", height: "30px", marginLeft: "-.5px", background: "#f55", transform: `rotate(${sc}deg)` } }));
  } else {
    const sub = { 200: `${date} ${wd}`, 201: wd, 202: date, 203: "" }[mode] ?? "";
    content = h("div", {}, h("div", { class: "big" }, time + suffix), sub ? h("div", { class: "small" }, sub) : null);
  }
  v.replaceChildren(content);
}
setInterval(updateInfoView, 1000);

// --------------------------------------------------------------- pagebar
function renderPagebar() {
  const bar = $("#pagebar");
  const inFolder = S.profile.folders.some(f => f.id === S.pageId);
  const tabs = S.profile.pages.map((p, idx) => {
    const t = h("div", { class: "page-tab" + (p.id === S.pageId ? " active" : ""), draggable: true, title: "Double-click to rename" },
      h("span", {}, p.name),
      S.profile.pages.length > 1 ? h("span", { class: "x", title: "Delete page", onclick: async e => { e.stopPropagation(); await deletePage(p); } }, "✕") : null);
    t.addEventListener("click", () => navigate(p.id));
    t.addEventListener("dblclick", async () => {
      const name = await promptDialog("Rename page", "", p.name);
      if (name) { S.profile = await api("PATCH", `/api/profiles/${S.profile.id}/pages/${p.id}`, { name }); renderPagebar(); }
    });
    t.addEventListener("dragstart", e => e.dataTransfer.setData("application/x-page", p.id));
    t.addEventListener("dragover", e => {
      const ty = e.dataTransfer.types;
      if (ty.includes("application/x-page") || ty.includes("application/x-key")) { e.preventDefault(); t.classList.add("drop"); }
    });
    t.addEventListener("dragleave", () => t.classList.remove("drop"));
    t.addEventListener("drop", async e => {
      e.preventDefault(); t.classList.remove("drop");
      const pid = e.dataTransfer.getData("application/x-page");
      const src = e.dataTransfer.getData("application/x-key");
      if (pid && pid !== p.id) {
        const order = S.profile.pages.map(x => x.id).filter(x => x !== pid);
        order.splice(idx, 0, pid);
        S.profile = await api("POST", `/api/profiles/${S.profile.id}/pages/reorder`, { order });
        renderPagebar();
      } else if (src !== "" && p.id !== S.pageId) {
        const dst = firstFreeSlot(p, Number(src));
        S.profile = await api("POST", `/api/profiles/${S.profile.id}/pages/${S.pageId}/keys/move`, { src: Number(src), dst, dst_page: p.id, copy_: e.ctrlKey });
        renderDeck(); toast(`Key ${e.ctrlKey ? "copied" : "moved"} to ${p.name}`, "ok");
      }
    });
    return t;
  });
  const add = h("div", { class: "page-tab page-add", title: "Add page" }, "+ Page");
  add.addEventListener("click", async () => {
    const r = await api("POST", `/api/profiles/${S.profile.id}/pages`, {});
    S.profile = r.profile; navigate(r.page);
  });
  bar.replaceChildren(...tabs, add);
  bar.style.opacity = inFolder ? 0.5 : 1;
}

function firstFreeSlot(page, preferred) {
  if (!page.keys[String(preferred)]) return preferred;
  for (let i = 0; i < S.state.layout.buttons; i++) if (!page.keys[String(i)]) return i;
  return preferred;
}

function renderCrumbs() {
  const c = $("#crumbs");
  const folder = S.profile.folders.find(f => f.id === S.pageId);
  if (!folder) { c.replaceChildren(h("span", {}, `${S.profile.name}`), h("span", {}, "›"), h("span", {}, currentPage().name)); return; }
  const chain = [];
  let p = folder;
  while (p) { chain.unshift(p); p = S.profile.pages.concat(S.profile.folders).find(x => x.id === p.parent); }
  c.replaceChildren(
    h("button", { onclick: () => navigate(chain[0].id) }, "← Back"),
    ...chain.flatMap((pg, i) => [h("span", {}, "›"), i < chain.length - 1 ? h("button", { onclick: () => navigate(pg.id) }, pg.name) : h("b", {}, `📁 ${pg.name}`)]),
  );
}

async function navigate(pageId) {
  S.pageId = pageId; S.selected = null;
  renderDeck(); renderPagebar(); renderCrumbs(); renderInspector();
  await api("POST", "/api/navigate", { page_id: pageId });
}

async function deletePage(p) {
  if (!(await confirmDialog(`Delete page “${p.name}” and all its keys?`))) return;
  S.profile = await api("DELETE", `/api/profiles/${S.profile.id}/pages/${p.id}`);
  if (S.pageId === p.id) S.pageId = S.profile.pages[0].id;
  renderAll();
}

// --------------------------------------------------------------- library
function renderLibrary() {
  const q = $("#actionSearch").value.trim().toLowerCase();
  const cats = {};
  for (const a of S.actions) {
    if (q && !(a.name + " " + a.category + " " + a.description).toLowerCase().includes(q)) continue;
    (cats[a.category] ||= []).push(a);
  }
  const list = $("#actionList");
  list.replaceChildren(...Object.entries(cats).map(([cat, acts]) =>
    h("details", { class: "cat", open: true },
      h("summary", {}, cat),
      ...acts.map(a => {
        const el = h("div", { class: "act", draggable: true, title: a.description },
          h("span", { class: "ico", style: { background: a.color } }, glyphFor(a.icon)),
          h("span", { class: "nm" }, a.name, h("small", {}, a.description)));
        el.addEventListener("dragstart", e => { e.dataTransfer.setData("application/x-action", a.type); e.dataTransfer.effectAllowed = "copy"; });
        el.addEventListener("dblclick", () => { if (S.selected?.kind === "key") assignAction(S.selected.index, a.type); });
        return el;
      }))));
}

function glyphFor(name) {
  const g = S.glyphMap?.[name];
  return g ? glyphChar(g[1]) : "";
}

// ----------------------------------------------------------------- keys ops
function defaultSettings(def) {
  const s = {};
  for (const f of def?.fields || []) if (f.default !== undefined) s[f.key] = f.default;
  for (const f of def?.fields || []) if (f.type === "steps") s[f.key] = [];
  return s;
}

async function assignAction(index, type) {
  const def = S.byType[type];
  const n = def.states || 1;
  const states = [];
  for (let s = 0; s < n; s++) {
    const design = await defaultDesign(def, s);
    states.push({ icon: await renderDesign(design), title: "", font: null, icon_design: design });
  }
  const old = keyAt(index);
  const key = { action: { type, settings: defaultSettings(def) }, long_press: old?.long_press || null, states, state: 0 };
  if (type === "folder") states[0].title = "Folder";
  await saveKey(index, key, true);
  select({ kind: "key", index });
}

async function saveKey(index, key, rerender = false) {
  S.ownSaveAt = Date.now();
  S.profile = await api("PUT", `/api/profiles/${S.profile.id}/pages/${S.pageId}/keys/${index}`, key);
  renderDeck();
  if (rerender) renderInspector();
}

const saveKeyDebounced = debounce((i, k) => saveKey(i, k), 350);

async function clearKey(index) {
  S.ownSaveAt = Date.now();
  S.profile = await api("DELETE", `/api/profiles/${S.profile.id}/pages/${S.pageId}/keys/${index}`);
  renderDeck(); renderInspector();
}

async function moveKey(src, dst, copy) {
  S.ownSaveAt = Date.now();
  S.profile = await api("POST", `/api/profiles/${S.profile.id}/pages/${S.pageId}/keys/move`, { src, dst, copy_: !!copy });
  S.selected = { kind: "key", index: dst };
  renderDeck(); renderInspector();
}

function select(sel) {
  S.selected = sel; S.editState = 0;
  if (sel?.kind === "key") { const k = keyAt(sel.index); if (k) S.editState = k.state; }
  renderDeck(); renderInspector();
}

function keyMenu(x, y, i) {
  const key = keyAt(i);
  const items = [
    ["▶  Run action", () => api("POST", `/api/press/${i}`), !key?.action],
    ["✎  Edit icon", () => openIconEditor(i, S.editState), !key],
    null,
    ["⧉  Copy", () => { S.clipboard = clone(key); toast("Key copied"); }, !key],
    ["⎘  Paste", () => saveKey(i, clone(S.clipboard), true), !S.clipboard],
    ["✂  Cut", async () => { S.clipboard = clone(key); await clearKey(i); }, !key],
    null,
    ["🗑  Clear", () => clearKey(i), !key],
  ];
  showMenu(x, y, items);
}

function showMenu(x, y, items) {
  const m = $("#ctxMenu");
  m.replaceChildren(...items.map(it => it ? h("button", { disabled: it[2], onclick: () => { m.hidden = true; it[1](); } }, it[0]) : h("hr")));
  m.hidden = false;
  const r = m.getBoundingClientRect();
  m.style.left = Math.min(x, innerWidth - r.width - 8) + "px";
  m.style.top = Math.min(y, innerHeight - r.height - 8) + "px";
}
document.addEventListener("click", e => { if (!e.target.closest("#ctxMenu")) $("#ctxMenu").hidden = true; });

document.addEventListener("keydown", e => {
  if (e.target.closest("input, textarea, select, dialog")) return;
  if (S.selected?.kind !== "key") return;
  const i = S.selected.index, key = keyAt(i);
  if ((e.key === "Delete" || e.key === "Backspace") && key) clearKey(i);
  else if (e.ctrlKey && e.key === "c" && key) { S.clipboard = clone(key); toast("Key copied"); }
  else if (e.ctrlKey && e.key === "v" && S.clipboard) saveKey(i, clone(S.clipboard), true);
  else if (e.ctrlKey && e.key === "x" && key) { S.clipboard = clone(key); clearKey(i); }
});

// -------------------------------------------------------------- inspector
function renderInspector() {
  const box = $("#inspector");
  box.classList.remove("empty");
  if (!S.selected) {
    box.classList.add("empty");
    box.replaceChildren(h("div", {}, "Select a key to edit it, or drag an action from the library onto a key."));
    return;
  }
  if (S.selected.kind === "info") return renderInfoInspector(box);
  const i = S.selected.index;
  const key = keyAt(i);
  if (!key) {
    box.replaceChildren(h("div", { class: "insp-grid" },
      h("div", { class: "insp-icon" }, keyEl(i, null)),
      h("div", {},
        h("div", { class: "insp-head" }, h("h3", {}, `Key ${i + 1}`), h("span", { class: "muted" }, "empty")),
        h("div", { class: "form" },
          h("label", {}, "Action"),
          actionTypeSelect(null, t => t && assignAction(i, t), true)))));
    return;
  }
  const def = S.byType[key.action?.type];
  const sidx = Math.min(S.editState, key.states.length - 1);
  const st = key.states[sidx];
  const save = () => saveKeyDebounced(i, key);

  let preview = keyEl(i, key, { state: sidx, noBadge: true });
  preview.addEventListener("click", () => openIconEditor(i, sidx));
  preview.title = "Click to change the icon";

  const stateTabs = key.states.length > 1 ? h("div", { class: "state-tabs" },
    ...key.states.map((_, n) => h("button", { class: n === sidx ? "active" : "", onclick: () => { S.editState = n; renderInspector(); } },
      def?.state_names?.[n] || `State ${n + 1}`))) : null;

  // title + font
  const fontPop = fontEditor(st, () => { save(); refreshPreview(); });
  const fontRow = h("div", { class: "font-row" }, h("span"), fontPop);
  const titleIn = h("textarea", { rows: 1, style: { minHeight: "32px" }, placeholder: "Title" });
  titleIn.value = st.title || "";
  titleIn.addEventListener("input", () => { st.title = titleIn.value; save(); refreshPreview(); });
  function refreshPreview() {
    const np = keyEl(i, key, { state: sidx, noBadge: true });
    np.addEventListener("click", () => openIconEditor(i, sidx));
    preview.replaceWith(np);
    preview = np;
    renderDeck();
  }

  const form = h("div", { class: "form" },
    h("label", {}, "Title"),
    h("div", { class: "title-row" }, titleIn,
      h("button", { class: "btn small", title: "Title style", onclick: () => fontRow.classList.toggle("open") }, h("span", { class: "fa-glyph" }, ""))),
    fontRow,
    h("label", {}, "Action"),
    actionTypeSelect(key.action?.type, async t => {
      if (!t) { key.action = null; await saveKey(i, key, true); return; }
      await assignAction(i, t);
    }),
    ...actionFields(def, key.action.settings, save, { index: i, key }),
  );

  // long press
  const lpDef = S.byType[key.long_press?.type];
  const lpForm = h("div", { class: "form", style: { marginTop: "12px" } },
    h("label", {}, "Long press"),
    actionTypeSelect(key.long_press?.type, async t => {
      key.long_press = t ? { type: t, settings: defaultSettings(S.byType[t]) } : null;
      await saveKey(i, key, true);
    }, true, "None"),
    ...(key.long_press ? actionFields(lpDef, key.long_press.settings, save, { index: i, key, nested: true }) : []));

  box.replaceChildren(h("div", { class: "insp-grid" },
    h("div", { class: "insp-icon" }, preview, stateTabs,
      h("div", { class: "inline", style: { display: "flex", gap: "6px" } },
        h("button", { class: "btn small", onclick: () => api("POST", `/api/press/${i}`) }, "▶ Test"),
        h("button", { class: "btn small danger", onclick: () => clearKey(i) }, "Clear"))),
    h("div", {},
      h("div", { class: "insp-head" },
        def ? h("span", { class: "ico", style: { background: def.color } }, glyphFor(def.icon)) : null,
        h("h3", {}, def?.name || "No action"), h("span", { class: "muted" }, `· Key ${i + 1}`)),
      def?.description ? h("div", { class: "desc" }, def.description) : null,
      form, lpForm)));
}

function actionTypeSelect(value, onChange, allowEmpty = false, emptyLabel = "— choose an action —", exclude = []) {
  const sel = h("select", {});
  if (allowEmpty || !value) sel.append(h("option", { value: "" }, emptyLabel));
  const cats = {};
  for (const a of S.actions) if (!exclude.includes(a.type)) (cats[a.category] ||= []).push(a);
  for (const [cat, acts] of Object.entries(cats))
    sel.append(h("optgroup", { label: cat }, ...acts.map(a => h("option", { value: a.type, selected: a.type === value }, a.name))));
  sel.addEventListener("change", () => onChange(sel.value || null));
  return sel;
}

function fontEditor(st, onChange) {
  const f = clone(fontOf(st));
  const commit = () => { st.font = clone(f); onChange(); };
  const seg = (opts, cur, set) => {
    const s = h("div", { class: "seg" });
    for (const [v, label] of opts) s.append(h("button", { class: v === cur ? "active" : "", onclick: () => { set(v); $$("button", s).forEach(b => b.classList.toggle("active", b.textContent === label)); commit(); } }, label));
    return s;
  };
  const show = h("input", { type: "checkbox", checked: f.show_title });
  show.addEventListener("change", () => { f.show_title = show.checked; commit(); });
  const size = h("input", { type: "number", min: 6, max: 30, value: f.size, style: { width: "64px" } });
  size.addEventListener("input", () => { f.size = +size.value || 10; commit(); });
  const color = h("input", { type: "color", value: "#" + f.color.replace("#", "") });
  color.addEventListener("input", () => { f.color = color.value.slice(1).toUpperCase(); commit(); });
  const bold = h("input", { type: "checkbox", checked: f.weight > 90 });
  bold.addEventListener("change", () => { f.weight = bold.checked ? 120 : 80; commit(); });
  const reset = h("button", { class: "btn small", onclick: () => { st.font = null; onChange(); renderInspector(); } }, "Use profile default");
  return h("div", { class: "font-pop" },
    h("label", {}, show, "Show"),
    h("label", {}, "Size", size),
    h("label", {}, "Colour", color),
    h("label", {}, bold, "Bold"),
    seg([["top", "Top"], ["middle", "Middle"], ["bottom", "Bottom"]], f.align, v => f.align = v),
    reset);
}

function actionFields(def, settings, save, ctx) {
  const out = [];
  for (const f of def?.fields || []) {
    out.push(h("label", {}, f.label));
    out.push(fieldInput(f, settings, save, ctx));
  }
  return out;
}

function fieldInput(f, settings, save, ctx) {
  const set = v => { settings[f.key] = v; save(); };
  const val = settings[f.key] ?? f.default ?? "";
  switch (f.type) {
    case "textarea": {
      const t = h("textarea", { placeholder: f.placeholder || "" }); t.value = val;
      t.addEventListener("input", () => set(t.value)); return t;
    }
    case "number": {
      const t = h("input", { type: "number", min: f.min, max: f.max, value: val });
      t.addEventListener("input", () => set(Number(t.value))); return t;
    }
    case "select": {
      const s = h("select", {}, ...f.options.map(([v, l]) => h("option", { value: v, selected: v === val }, l)));
      s.addEventListener("change", () => set(s.value)); return s;
    }
    case "toggle": {
      const c = h("input", { type: "checkbox", checked: !!val });
      c.addEventListener("change", () => set(c.checked));
      return h("div", { class: "inline" }, c);
    }
    case "hotkey": return hotkeyInput(val, set);
    case "app": return appPicker(val, set, ctx);
    case "page": {
      const s = h("select", {}, h("option", { value: "" }, "— page —"), ...S.profile.pages.map(p => h("option", { value: p.id, selected: p.id === val }, p.name)));
      s.addEventListener("change", () => set(s.value)); return s;
    }
    case "profile": {
      const s = h("select", {}, h("option", { value: "" }, "— profile —"), ...S.state.profiles.map(p => h("option", { value: p.id, selected: p.id === val }, p.name)));
      s.addEventListener("change", () => set(s.value)); return s;
    }
    case "folder": {
      const folder = S.profile.folders.find(x => x.id === val);
      return h("div", { class: "inline" },
        h("span", { class: "muted" }, folder ? `${Object.keys(folder.keys).length} key(s) inside` : "Created when saved"),
        folder ? h("button", { class: "btn small primary", onclick: () => navigate(folder.id) }, "Open folder →") : null);
    }
    case "steps": return stepsEditor(settings, f.key, save);
    default: {
      const t = h("input", { type: "text", placeholder: f.placeholder || "", value: val });
      t.addEventListener("input", () => set(t.value)); return t;
    }
  }
}

const CODE_NAMES = { Space: "space", Enter: "enter", Escape: "esc", Backspace: "backspace", Tab: "tab", Delete: "delete", Insert: "insert",
  ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right", Home: "home", End: "end", PageUp: "pageup", PageDown: "pagedown",
  Minus: "-", Equal: "=", BracketLeft: "[", BracketRight: "]", Semicolon: ";", Quote: "'", Backquote: "`", Backslash: "\\", Comma: ",", Period: ".", Slash: "/",
  PrintScreen: "print", CapsLock: "capslock", ContextMenu: "menu", NumpadEnter: "enter",
  MediaPlayPause: "playpause", MediaTrackNext: "next", MediaTrackPrevious: "prev", AudioVolumeUp: "volup", AudioVolumeDown: "voldown", AudioVolumeMute: "mute" };

function hotkeyInput(val, set) {
  const inp = h("input", { type: "text", class: "hotkey", value: val, placeholder: "Click, then press the shortcut" });
  let capture = true;
  const toggle = h("button", { class: "btn small", title: "Type the combination as text instead" }, "Aa");
  toggle.addEventListener("click", () => { capture = !capture; toggle.classList.toggle("primary", !capture); inp.focus(); });
  inp.addEventListener("focus", () => capture && inp.classList.add("capturing"));
  inp.addEventListener("blur", () => inp.classList.remove("capturing"));
  inp.addEventListener("keydown", e => {
    if (!capture) return;
    if (e.key === "Tab") return;
    e.preventDefault();
    if (["Control", "Shift", "Alt", "Meta", "AltGraph", "OS"].includes(e.key)) return;
    let k = CODE_NAMES[e.code];
    if (!k) {
      if (/^Key[A-Z]$/.test(e.code)) k = e.code.slice(3).toLowerCase();
      else if (/^Digit\d$/.test(e.code)) k = e.code.slice(5);
      else if (/^F\d+$/.test(e.code)) k = e.code.toLowerCase();
      else if (/^Numpad\d$/.test(e.code)) k = "kp" + e.code.slice(6);
      else k = e.key.toLowerCase();
    }
    const mods = [e.ctrlKey && "ctrl", e.shiftKey && "shift", e.altKey && "alt", e.metaKey && "super"].filter(Boolean);
    inp.value = [...mods, k].join("+");
    set(inp.value);
  });
  inp.addEventListener("input", () => { if (!capture) set(inp.value); });
  return h("div", { class: "title-row" }, inp, toggle);
}

async function loadApps() { if (!S.apps) S.apps = await api("GET", "/api/apps"); return S.apps; }

function appPicker(val, set, ctx) {
  const wrap = h("div", { class: "app-pick" });
  const img = h("img", { alt: "" });
  const sel = h("select", { style: { flex: 1 } }, h("option", { value: "" }, "Loading applications…"));
  wrap.append(img, sel);
  loadApps().then(apps => {
    sel.replaceChildren(h("option", { value: "" }, "— choose an application —"), ...apps.map(a => h("option", { value: a.id, selected: a.id === val }, a.name)));
    showIcon();
  });
  function showIcon() {
    const a = S.apps?.find(x => x.id === sel.value);
    img.hidden = !a?.icon;
    if (a?.icon) img.src = `/api/apps/icon?name=${encodeURIComponent(a.icon)}`;
  }
  sel.addEventListener("change", async () => {
    set(sel.value); showIcon();
    const a = S.apps.find(x => x.id === sel.value);
    if (!a || !ctx?.key || ctx.nested) return;
    const st = ctx.key.states[0];
    if (st.icon_design?.auto && a.icon) {
      const design = { kind: "image", src: `/api/apps/icon?name=${encodeURIComponent(a.icon)}`, bg: "#1b1e24", fit: "pad", scale: 66, round: true, auto: true };
      try { st.icon = await renderDesign(design); st.icon_design = design; } catch {}
    }
    if (!st.title) st.title = a.name;
    await saveKey(ctx.index, ctx.key, true);
  });
  return wrap;
}

function stepsEditor(settings, fieldKey, save) {
  const steps = settings[fieldKey] ||= [];
  const box = h("div", { class: "steps" });
  const exclude = ["folder", "multi_action", "multi_action_switch"];
  function redraw() {
    box.replaceChildren(
      ...steps.map((step, n) => {
        const def = S.byType[step.type];
        return h("div", { class: "step" },
          h("div", { class: "step-head" },
            h("span", { class: "ico", style: { background: def?.color } }, glyphFor(def?.icon)),
            h("b", {}, `${n + 1}. ${def?.name || step.type}`), h("span", { class: "spacer" }),
            h("button", { class: "icon-btn", title: "Move up", disabled: n === 0, onclick: () => { [steps[n - 1], steps[n]] = [steps[n], steps[n - 1]]; save(); redraw(); } }, "↑"),
            h("button", { class: "icon-btn", title: "Move down", disabled: n === steps.length - 1, onclick: () => { [steps[n + 1], steps[n]] = [steps[n], steps[n + 1]]; save(); redraw(); } }, "↓"),
            h("button", { class: "icon-btn", title: "Remove", onclick: () => { steps.splice(n, 1); save(); redraw(); } }, "✕")),
          def?.fields?.length ? h("div", { class: "form" }, ...actionFields(def, step.settings, save, { nested: true })) : null);
      }),
      actionTypeSelect(null, t => { if (t) { steps.push({ type: t, settings: defaultSettings(S.byType[t]) }); save(); redraw(); } }, true, "+ Add action…", exclude));
  }
  redraw();
  return box;
}

// ---------------------------------------------------- info window inspector
function renderInfoInspector(box) {
  const p = S.profile;
  const modeNames = { 0: "System statistics (CPU / RAM / GPU)", 1: "Analog clock", 2: "Background image", 200: "Digital: time + date + weekday",
    201: "Digital: time + weekday", 202: "Digital: time + date", 203: "Digital: time only" };
  const mode = h("select", {}, ...S.state.info_modes.map(([v]) => h("option", { value: v, selected: v === p.info_mode }, modeNames[v] || v)));
  mode.addEventListener("change", async () => { await patchProfile({ info_mode: Number(mode.value) }); renderInspector(); });
  const twelve = h("input", { type: "checkbox", checked: S.state.settings.twelve_hour });
  twelve.addEventListener("change", () => patchSettings({ twelve_hour: twelve.checked }));
  const bgFile = h("input", { type: "file", accept: "image/*", hidden: true });
  bgFile.addEventListener("change", async () => {
    const f = bgFile.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f); fd.append("kind", "background");
    const { name } = await api("POST", "/api/icons", fd, true);
    await patchProfile({ info_background: name, info_mode: 2 }); renderInspector();
  });
  const act = p.info_action;
  const def = S.byType[act?.type];
  const save = debounce(() => patchProfile({ info_action: act }), 350);
  box.replaceChildren(h("div", {},
    h("div", { class: "insp-head" }, h("h3", {}, "Info window"), h("span", { class: "muted" }, "· wide display, 458×196")),
    h("div", { class: "form" },
      h("label", {}, "Display"), mode,
      h("label", {}, "12-hour clock"), h("div", { class: "inline" }, twelve),
      ...(p.info_mode === 2 ? [h("label", {}, "Background"), h("div", { class: "inline" },
        h("button", { class: "btn small", onclick: () => bgFile.click() }, p.info_background ? "Change image…" : "Choose image…"),
        h("span", { class: "muted" }, "Scaled to 458×196"), bgFile)] : []),
      h("label", {}, "Tap action"),
      actionTypeSelect(act?.type, async t => {
        if (!t) await patchProfile({ clear_info_action: true });
        else await patchProfile({ info_action: { type: t, settings: defaultSettings(S.byType[t]) } });
        renderInspector();
      }, true, "None", ["folder"]),
      ...(act ? actionFields(def, act.settings, save, { nested: true }) : []))));
}

async function patchProfile(patch) {
  S.ownSaveAt = Date.now();
  S.profile = await api("PATCH", `/api/profiles/${S.profile.id}`, patch);
  renderDeck();
}

async function patchSettings(patch) {
  S.state.settings = await api("PATCH", "/api/settings", patch);
  renderTop(); updateInfoView();
}

// ------------------------------------------------------------- icon editor
const SWATCHES = ["#000000", "#1b1e24", "#3b5bdb", "#1971c2", "#0c8599", "#2b8a3e", "#5c940d", "#e67700",
  "#e8590c", "#c92a2a", "#c2255c", "#9c36b5", "#6741d9", "#495057", "#ffffff", "#f59f00"];
const IE = { design: null, img: null, index: 0, state: 0 };

async function openIconEditor(index, stateIdx) {
  const key = keyAt(index);
  if (!key) return;
  const st = key.states[stateIdx];
  IE.index = index; IE.state = stateIdx;
  IE.design = clone(st.icon_design || { kind: st.icon ? "image" : "glyph", src: st.icon ? iconUrl(st.icon) : null, bg: "#000000", fg: "#ffffff", scale: 48, round: true });
  IE.img = null;
  if (IE.design.kind === "image" && IE.design.src) { try { IE.img = await loadImage(IE.design.src); } catch { IE.design.kind = "none"; } }
  await loadGlyphs();
  $("#ieBg").value = IE.design.bg || "#000000";
  $("#ieFg").value = IE.design.fg || "#ffffff";
  $("#ieScale").value = IE.design.scale || 48;
  $("#ieFit").value = IE.design.fit || "cover";
  $("#ieRound").checked = !!IE.design.round;
  $("#ieSwatches").replaceChildren(...SWATCHES.map(c => h("button", { style: { background: c }, title: c, onclick: () => { $("#ieBg").value = c; IE.design.bg = c; iePaint(); } })));
  showIeTab(IE.design.kind === "image" ? "upload" : "glyph");
  renderGlyphGrid();
  $("#iconDialog").showModal();
  iePaint();
}

async function iePaint() {
  await drawDesign($("#iePreview"), IE.design, IE.img);
}

function showIeTab(tab) {
  $$("#ieTabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  $$(".ie-tab").forEach(t => t.hidden = t.dataset.tab !== tab);
  if (tab === "apps") renderAppIconGrid();
}
$$("#ieTabs button").forEach(b => b.addEventListener("click", () => showIeTab(b.dataset.tab)));

function renderGlyphGrid() {
  const q = $("#glyphSearch").value.trim().toLowerCase();
  const list = S.glyphs.filter(g => !q || g[0].includes(q) || g[3].toLowerCase().includes(q) || g[4].toLowerCase().includes(q)).slice(0, 400);
  $("#glyphGrid").replaceChildren(...list.map(g => {
    const b = h("button", { title: `${g[4]} (${{ s: "solid", r: "regular", b: "brands" }[g[2]]})`, style: { font: faFont(g[2], 20) } }, glyphChar(g[1]));
    if (IE.design?.glyph?.[0] === g[0] && IE.design.glyph[2] === g[2]) b.classList.add("active");
    b.addEventListener("click", () => {
      IE.design.kind = "glyph"; IE.design.glyph = [g[0], g[1], g[2]]; IE.img = null; IE.design.auto = false;
      $$("#glyphGrid button").forEach(x => x.classList.remove("active")); b.classList.add("active");
      iePaint();
    });
    return b;
  }));
}
$("#glyphSearch").addEventListener("input", debounce(renderGlyphGrid, 150));

async function renderAppIconGrid() {
  const apps = await loadApps();
  const q = $("#appIconSearch").value.trim().toLowerCase();
  $("#appIconGrid").replaceChildren(...apps.filter(a => a.icon && (!q || a.name.toLowerCase().includes(q))).slice(0, 300).map(a => {
    const src = `/api/apps/icon?name=${encodeURIComponent(a.icon)}`;
    const im = h("img", { src, loading: "lazy", alt: "" });
    im.onerror = () => b.remove();
    const b = h("button", { title: a.name }, im, h("span", {}, a.name));
    b.addEventListener("click", async () => {
      IE.img = await loadImage(src);
      Object.assign(IE.design, { kind: "image", src, fit: "pad", auto: false });
      if (IE.design.scale > 85 || IE.design.scale < 40) IE.design.scale = 66;
      $("#ieFit").value = "pad"; $("#ieScale").value = IE.design.scale;
      iePaint();
    });
    return b;
  }));
}
$("#appIconSearch").addEventListener("input", debounce(renderAppIconGrid, 150));

function ieLoadFile(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async () => {
    IE.img = await loadImage(reader.result);
    // keep a server copy so the design can be re-edited later
    const fd = new FormData(); fd.append("file", file); fd.append("kind", "icon"); fd.append("fit", "contain");
    let src = reader.result;
    try { src = iconUrl((await api("POST", "/api/icons", fd, true)).name); } catch {}
    Object.assign(IE.design, { kind: "image", src, auto: false });
    iePaint();
  };
  reader.readAsDataURL(file);
}
$("#ieFile").addEventListener("change", e => ieLoadFile(e.target.files[0]));
const dz = $("#dropzone");
dz.addEventListener("dragover", e => { e.preventDefault(); dz.classList.add("over"); });
dz.addEventListener("dragleave", () => dz.classList.remove("over"));
dz.addEventListener("drop", e => { e.preventDefault(); dz.classList.remove("over"); ieLoadFile(e.dataTransfer.files[0]); });

$("#ieBg").addEventListener("input", e => { IE.design.bg = e.target.value; iePaint(); });
$("#ieFg").addEventListener("input", e => { IE.design.fg = e.target.value; iePaint(); });
$("#ieScale").addEventListener("input", e => { IE.design.scale = +e.target.value; iePaint(); });
$("#ieFit").addEventListener("change", e => { IE.design.fit = e.target.value; iePaint(); });
$("#ieRound").addEventListener("change", e => { IE.design.round = e.target.checked; iePaint(); });
$("#ieCancel").addEventListener("click", () => $("#iconDialog").close());
$("#ieClear").addEventListener("click", async () => {
  const key = keyAt(IE.index); const st = key.states[IE.state];
  st.icon = null; st.icon_design = null;
  $("#iconDialog").close();
  await saveKey(IE.index, key, true);
});
$("#ieSave").addEventListener("click", async () => {
  const key = keyAt(IE.index); const st = key.states[IE.state];
  IE.design.auto = false;
  st.icon = await uploadCanvas($("#iePreview"));
  st.icon_design = IE.design.src?.startsWith("data:") ? { ...IE.design, src: null, kind: "none" } : clone(IE.design);
  $("#iconDialog").close();
  await saveKey(IE.index, key, true);
});

// ------------------------------------------------------- settings & profiles
function openSettings() {
  const s = S.state.settings;
  const body = $("#settingsBody");
  const num = (key, attrs) => { const i = h("input", { type: "number", value: s[key], ...attrs }); i.addEventListener("change", () => patchSettings({ [key]: Number(i.value) })); return i; };
  const sleep = h("select", {}, ...[[0, "Never"], [1, "1 minute"], [5, "5 minutes"], [10, "10 minutes"], [15, "15 minutes"], [30, "30 minutes"], [60, "1 hour"]]
    .map(([v, l]) => h("option", { value: v, selected: v === s.sleep_minutes }, l)));
  sleep.addEventListener("change", () => patchSettings({ sleep_minutes: Number(sleep.value) }));
  const obs = clone(s.obs);
  const obsIn = (k, type = "text") => { const i = h("input", { type, value: obs[k] }); i.addEventListener("change", () => { obs[k] = type === "number" ? Number(i.value) : i.value; patchSettings({ obs }); }); return i; };
  const d = S.state.device;
  body.replaceChildren(
    h("div", { class: "settings-sec" }, h("h4", {}, "Device"),
      h("div", { class: "form" },
        h("label", {}, "Status"), h("div", {}, d.connected ? `Connected — ${d.product} (${d.vid}:${d.pid})` : "Not connected"),
        ...(d.info ? [h("label", {}, "Firmware info"), h("code", { style: { wordBreak: "break-all" } }, typeof d.info === "string" ? d.info : JSON.stringify(d.info))] : []),
        h("label", {}, "Screen sleep"), sleep,
        h("label", {}, "Long press after (ms)"), num("long_press_ms", { min: 200, max: 3000, step: 50 }),
        h("label", {}, ""), h("div", {}, h("button", { class: "btn small", onclick: () => api("POST", "/api/device/reconnect").then(() => toast("Layout re-sent", "ok")) }, "Re-send layout to device")))),
    h("div", { class: "settings-sec" }, h("h4", {}, "Keyboard emulation"),
      h("div", { class: "form" }, h("label", {}, "Backend"),
        h("div", {}, S.state.keyboard.backend === "uinput" ? "uinput virtual keyboard (Wayland & X11)" : S.state.keyboard.backend,
          S.state.keyboard.error ? h("div", { class: "muted" }, S.state.keyboard.error) : null))),
    h("div", { class: "settings-sec" }, h("h4", {}, "OBS Studio (WebSocket v5)"),
      h("div", { class: "form" },
        h("label", {}, "Host"), obsIn("host"), h("label", {}, "Port"), obsIn("port", "number"), h("label", {}, "Password"), obsIn("password", "password"))),
  );
  $("#settingsDialog").showModal();
}

function profileMenu(x, y) {
  const p = S.profile;
  showMenu(x, y, [
    ["＋  New profile", async () => { const name = await promptDialog("New profile", "Name", "New Profile"); if (name) { const np = await api("POST", "/api/profiles", { name }); await switchProfile(np.id); } }],
    ["✎  Rename", async () => { const name = await promptDialog("Rename profile", "", p.name); if (name) { await patchProfile({ name }); await refreshState(); } }],
    ["⧉  Duplicate", async () => { const np = await api("POST", `/api/profiles/${p.id}/duplicate`); await switchProfile(np.id); }],
    null,
    ["⇩  Export…", () => { location.href = `/api/profiles/${p.id}/export`; }],
    ["⇧  Import…", () => $("#importFile").click()],
    null,
    ["🗑  Delete", async () => { if (await confirmDialog(`Delete profile “${p.name}”?`)) { await api("DELETE", `/api/profiles/${p.id}`); await loadAll(); } }, S.state.profiles.length <= 1],
  ]);
}

$("#importFile").addEventListener("change", async e => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  const np = await api("POST", "/api/profiles/import", fd, true);
  e.target.value = "";
  toast(`Imported “${np.name}”`, "ok");
  await switchProfile(np.id);
});

async function switchProfile(id) {
  await api("POST", `/api/profiles/${id}/activate`);
  await loadAll();
}

async function refreshState() {
  S.state = await api("GET", "/api/state");
  renderTop(); renderBanner();
}

function promptDialog(title, text, value = "") {
  return new Promise(res => {
    const d = $("#promptDialog");
    $("#promptTitle").textContent = title; $("#promptText").textContent = text;
    const inp = $("#promptInput"); inp.hidden = false; inp.value = value;
    d.onclose = () => res(d.returnValue === "ok" ? inp.value.trim() : null);
    d.returnValue = ""; d.showModal(); inp.select();
  });
}

function confirmDialog(text) {
  return new Promise(res => {
    const d = $("#promptDialog");
    $("#promptTitle").textContent = "Confirm"; $("#promptText").textContent = text;
    $("#promptInput").hidden = true;
    d.onclose = () => res(d.returnValue === "ok");
    d.returnValue = ""; d.showModal();
  });
}

// ------------------------------------------------------------------ top wiring
$("#profileSelect").addEventListener("change", e => switchProfile(e.target.value));
$("#profileMenuBtn").addEventListener("click", e => { e.stopPropagation(); const r = e.target.getBoundingClientRect(); profileMenu(r.left, r.bottom + 4); });
$("#settingsBtn").addEventListener("click", openSettings);
$("#actionSearch").addEventListener("input", renderLibrary);
const sendBrightness = debounce(v => patchSettings({ brightness: v }), 120);
$("#brightness").addEventListener("input", e => { $("#brightnessVal").textContent = e.target.value + "%"; sendBrightness(Number(e.target.value)); });

// ------------------------------------------------------------------- live
function connectWS() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onmessage = async ev => {
    const m = JSON.parse(ev.data);
    switch (m.type) {
      case "status":
        S.state.device = m.device; renderTop(); renderBanner();
        break;
      case "input": {
        const el = $(`#deck [data-index="${m.index}"]`);
        if (el && m.kind === "button") el.classList.toggle("pressed", m.action === "press");
        break;
      }
      case "page":
        if (m.page !== S.pageId) { S.pageId = m.page; S.selected = null; await reloadProfile(); renderDeck(); renderPagebar(); renderCrumbs(); renderInspector(); }
        break;
      case "profile":
        await loadAll();
        break;
      case "profiles":
        await refreshState();
        break;
      case "profile_changed":
        if (m.profile === S.profile.id && Date.now() - S.ownSaveAt > 1500) { await reloadProfile(); renderDeck(); renderPagebar(); }
        break;
      case "key_state": {
        if (m.page === S.pageId) {
          const k = keyAt(m.index);
          if (k) { k.state = m.state; renderDeck(); if (S.selected?.index === m.index) { S.editState = m.state; renderInspector(); } }
        }
        break;
      }
      case "settings":
        S.state.settings = m.settings; renderTop();
        break;
      case "error":
        toast(m.message, "error");
        break;
    }
  };
  ws.onclose = () => setTimeout(connectWS, 1500);
}

(async () => {
  await loadGlyphs();
  await loadAll();
  connectWS();
  // deep links: #key=N  #info  #icon=N  #settings
  const m = location.hash.match(/^#(key|icon)=(\d+)$/);
  if (m) { select({ kind: "key", index: Number(m[2]) }); if (m[1] === "icon") openIconEditor(Number(m[2]), 0); }
  else if (location.hash === "#info") select({ kind: "info" });
  else if (location.hash === "#settings") openSettings();
})();
