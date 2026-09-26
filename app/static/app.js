"use strict";

const $ = (s) => document.querySelector(s);

const LANGS = [["", "Idioma: automático"], ["pt", "Português"], ["en", "Inglês"], ["es", "Espanhol"],
               ["fr", "Francês"], ["it", "Italiano"], ["de", "Alemão"]];
const STATUS = { queued: "Na fila", separating: "A separar", lyrics: "Letra", done: "Pronta", error: "Erro" };
const LEAD = 0.25;          // a linha acende um pouco antes de começar
const OFFSET_STEP = 0.25;

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, "");
    else if (v != null && v !== false) e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) e.append(k);
  return e;
}

const fmt = (s) => {
  s = Math.max(0, Math.floor(s || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (r.status === 401) {
    location.href = "/login";
    throw new Error("Sessão terminada");
  }
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || r.statusText);
  }
  return r.json();
}

const storage = {
  get(k, def) { try { const v = localStorage.getItem(k); return v === null ? def : JSON.parse(v); } catch { return def; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* sem storage */ } },
};

for (const sel of document.querySelectorAll(".lang-select"))
  for (const [v, label] of LANGS) sel.append(el("option", { value: v }, label));

function show(id) {
  $("#library").hidden = id !== "library";
  $("#player").hidden = id !== "player";
}

/* ================================================================ BIBLIOTECA */

let songs = [], dls = [], lastData = "";

async function refresh() {
  try {
    [songs, dls] = await Promise.all([api("/api/songs"), api("/api/downloads")]);
  } catch (e) { console.error(e); return; }
  // só redesenha se algo mudou (evita piscar imagens e perder cliques)
  const data = JSON.stringify([songs, dls]);
  if (data === lastData) return;
  lastData = data;
  renderSongs();
  renderDownloads();
  renderDlPick();
}

function renderSongs() {
  const q = $("#search").value.trim().toLowerCase();
  const shown = songs.filter((s) => !q || `${s.title} ${s.artist}`.toLowerCase().includes(q));
  const list = $("#songs");
  list.replaceChildren();
  if (!shown.length)
    list.append(el("li", { class: "empty" }, songs.length ? "Nada encontrado." : "Ainda não há músicas. Adiciona a primeira! 🎶"));
  for (const s of shown) list.append(songItem(s));
}

function songItem(s) {
  const busy = s.status !== "done" && s.status !== "error";
  const src = { lrclib: "letra online", whisper: "letra transcrita" }[s.lyrics_source];
  return el("li", { class: `song ${s.status}` },
    el("div", { class: "info" },
      el("div", { class: "t", title: s.original_name || "" }, s.title || "Sem título"),
      el("div", { class: "a" }, `${s.artist || "Artista desconhecido"} · ${fmt(s.duration)}`),
      busy && el("div", { class: "prog" },
        el("div", { class: "stage-txt" }, s.stage || STATUS[s.status]),
        el("div", { class: "bar" }, el("i", { style: `width:${s.progress || 0}%` }))),
      s.status === "error" && el("div", { class: "err" }, s.error || "Erro desconhecido"),
    ),
    el("div", { class: "actions" },
      src && el("span", { class: "tag", title: s.lyrics_matched || "" }, src),
      s.status === "done"
        ? el("button", { class: "primary", onclick: () => (location.hash = `#/cantar/${s.id}`) }, "▶ Cantar")
        : el("span", { class: `badge ${s.status}` }, STATUS[s.status]),
      el("button", { class: "ghost", title: "Refazer a letra", disabled: busy, onclick: () => openRedo(s) }, "✎"),
      el("button", { class: "ghost", title: "Apagar", onclick: () => removeSong(s) }, "🗑"),
    ));
}

$("#search").addEventListener("input", renderSongs);
setInterval(() => { if (!$("#library").hidden) refresh(); }, 2500);

async function removeSong(s) {
  if (!confirm(`Apagar "${s.title}"?`)) return;
  try { await api(`/api/songs/${s.id}`, { method: "DELETE" }); } catch (e) { alert(e.message); }
  refresh();
}

/* ---- adicionar músicas */

let picked = [], srcMode = "device", dlPickKey = "";
const drop = $("#drop");
const dlName = (d) => [d.artist, d.title].filter(Boolean).join(" - ") || d.url;

function updateSend() {
  // artista/título só fazem sentido para uma música de cada vez
  const single = srcMode === "downloads" || picked.length <= 1;
  $("#artist").disabled = $("#title").disabled = !single;
  $("#send").disabled = srcMode === "device" ? picked.length === 0 : !$("#dlPick").value;
}

function setSrc(mode) {
  srcMode = mode;
  for (const b of $("#srcSeg").children) b.classList.toggle("on", b.dataset.src === mode);
  $("#drop").hidden = mode !== "device";
  $("#fromDl").hidden = mode !== "downloads";
  updateSend();
}
for (const b of $("#srcSeg").children) b.addEventListener("click", () => setSrc(b.dataset.src));
$("#dlPick").addEventListener("change", updateSend);
$("#dlPickHint").append("Para descarregar outra música usa a aba ", el("a", { href: "#/downloads" }, "Downloads"), ".");

function renderDlPick() {
  const done = dls.filter((d) => d.status === "done");
  const inKaraoke = new Set(songs.map((s) => s.download_id).filter(Boolean));
  const key = done.map((d) => `${d.id}${inKaraoke.has(d.id)}${dlName(d)}`).join();
  if (key === dlPickKey) return;
  dlPickKey = key;
  const sel = $("#dlPick"), prev = sel.value;
  sel.replaceChildren(...done.map((d) => el("option", { value: d.id },
    `${dlName(d)} (${fmt(d.duration)})${inKaraoke.has(d.id) ? " · já está no karaoke" : ""}`)));
  if (!done.length) sel.append(el("option", { value: "" }, "Ainda não há downloads prontos"));
  if (done.some((d) => d.id === prev)) sel.value = prev;
  updateSend();
}

function pick(files) {
  picked = [...files];
  const txt = $("#dropText");
  txt.classList.toggle("picked", picked.length > 0);
  txt.textContent = picked.length === 0 ? "Arrasta músicas para aqui ou escolhe ficheiros"
    : picked.length === 1 ? `🎵 ${picked[0].name}` : `🎵 ${picked.length} músicas escolhidas`;
  updateSend();
}

$("#file").addEventListener("change", (e) => pick(e.target.files));
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  drop.classList.remove("over");
  if (e.dataTransfer.files.length) pick(e.dataTransfer.files);
});

function uploadOne(file, fields, onProgress) {
  return new Promise((resolve, reject) => {
    const fd = new FormData();
    fd.append("file", file);
    for (const [k, v] of Object.entries(fields)) fd.append(k, v);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/songs");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      if (xhr.status < 300) return resolve();
      let msg = xhr.statusText;
      try { msg = JSON.parse(xhr.responseText).detail || msg; } catch { /* resposta sem JSON */ }
      reject(new Error(`${file.name}: ${msg}`));
    };
    xhr.onerror = () => reject(new Error(`${file.name}: falha de rede`));
    xhr.send(fd);
  });
}

async function submitFromDownload() {
  const fd = new FormData();
  fd.append("artist", $("#artist").value);
  fd.append("title", $("#title").value);
  fd.append("language", $("#language").value);
  fd.append("force_transcribe", $("#force").checked ? "true" : "false");
  $("#send").disabled = true;
  try {
    await api(`/api/downloads/${$("#dlPick").value}/karaoke`, { method: "POST", body: fd });
    for (const id of ["#artist", "#title"]) $(id).value = "";
    $("#force").checked = false;
    refresh();
  } catch (err) { alert(err.message); }
  updateSend();
}

$("#upload").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (srcMode === "downloads") return submitFromDownload();
  if (!picked.length) return;
  const single = picked.length === 1;
  const fields = {
    artist: single ? $("#artist").value : "",
    title: single ? $("#title").value : "",
    language: $("#language").value,
    force_transcribe: $("#force").checked ? "true" : "false",
  };
  const bar = $("#upbar"), fill = bar.firstElementChild;
  $("#send").disabled = true;
  bar.hidden = false;
  const errors = [];
  for (const [i, f] of picked.entries()) {
    try {
      await uploadOne(f, fields, (p) => (fill.style.width = `${((i + p) / picked.length) * 100}%`));
    } catch (err) { errors.push(err.message); }
    refresh();
  }
  bar.hidden = true;
  fill.style.width = "0";
  $("#upload").reset();
  pick([]);
  if (errors.length) alert(errors.join("\n"));
});

/* ================================================================ DOWNLOADS */

const DL_BUSY = { queued: "Na fila", downloading: "A descarregar" };

function renderDownloads() {
  const list = $("#downloads");
  list.replaceChildren();
  if (!dls.length) list.append(el("li", { class: "empty" }, "Ainda não descarregaste nada. Cola um link acima! 🔗"));
  const songById = Object.fromEntries(songs.map((s) => [s.id, s]));
  for (const d of dls) list.append(dlItem(d, songById[d.song_id]));
  const active = dls.filter((d) => d.status in DL_BUSY).length;
  $("#dlBadge").hidden = !active;
  $("#dlBadge").textContent = active;
}

function dlItem(d, song) {
  const busy = d.status in DL_BUSY;
  let action;
  if (d.status === "done") {
    if (!song) action = el("button", { class: "primary", onclick: () => dlToKaraoke(d) }, "🎤 Criar karaoke");
    else if (song.status === "done")
      action = el("button", { class: "primary", onclick: () => (location.hash = `#/cantar/${song.id}`) }, "▶ Cantar");
    else if (song.status === "error") action = el("span", { class: "badge error" }, "Erro no karaoke");
    else action = el("span", { class: "badge" }, "A preparar karaoke…");
  } else {
    action = el("span", { class: `badge ${d.status}` }, DL_BUSY[d.status] || "Erro");
  }
  const link = el("a", { href: d.webpage_url || d.url, target: "_blank", rel: "noopener" }, d.site || "abrir link");
  return el("li", { class: `song ${d.status}` },
    d.thumbnail
      ? el("img", { class: "thumb", src: d.thumbnail, alt: "", loading: "lazy", referrerpolicy: "no-referrer" })
      : el("div", { class: "thumb" }),
    el("div", { class: "info" },
      el("div", { class: "t", title: d.url }, d.title || d.url),
      el("div", { class: "a" },
        d.status === "done" ? `${d.artist || "Artista desconhecido"} · ${fmt(d.duration)} · ` : "", link),
      busy && el("div", { class: "prog" },
        el("div", { class: "stage-txt" }, d.stage || DL_BUSY[d.status]),
        el("div", { class: "bar" }, el("i", { style: `width:${d.progress || 0}%` }))),
      d.status === "error" && el("div", { class: "err" }, d.error || "Erro desconhecido"),
    ),
    el("div", { class: "actions" },
      action,
      el("button", { class: "ghost", title: "Apagar download", onclick: () => removeDl(d) }, "🗑")));
}

async function dlToKaraoke(d) {
  try { await api(`/api/downloads/${d.id}/karaoke`, { method: "POST", body: new FormData() }); }
  catch (e) { alert(e.message); }
  refresh();
}

async function removeDl(d) {
  if (!confirm(`Apagar o download "${d.title || d.url}"?\n(O karaoke criado a partir dele continua na lista de músicas.)`)) return;
  try { await api(`/api/downloads/${d.id}`, { method: "DELETE" }); } catch (e) { alert(e.message); }
  refresh();
}

$("#dlForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData();
  fd.append("url", $("#dlUrl").value);
  fd.append("auto_karaoke", $("#dlAuto").checked ? "true" : "false");
  fd.append("language", $("#dlLang").value);
  $("#dlSend").disabled = true;
  try {
    await api("/api/downloads", { method: "POST", body: fd });
    $("#dlUrl").value = "";
    refresh();
  } catch (err) { alert(err.message); }
  $("#dlSend").disabled = false;
});

/* ---- refazer letra */

let redoSong = null;

function openRedo(s) {
  redoSong = s;
  const f = $("#redoForm");
  f.artist.value = s.artist || "";
  f.title.value = s.title || "";
  f.language.value = s.language || "";
  f.force.checked = false;
  $("#redoDlg").showModal();
}

$("#redoDlg").addEventListener("close", async () => {
  if ($("#redoDlg").returnValue !== "ok" || !redoSong) return;
  const f = $("#redoForm");
  const fd = new FormData();
  fd.append("artist", f.artist.value);
  fd.append("title", f.title.value);
  fd.append("language", f.language.value);
  fd.append("force_transcribe", f.force.checked ? "true" : "false");
  try { await api(`/api/songs/${redoSong.id}/redo-lyrics`, { method: "POST", body: fd }); }
  catch (e) { alert(e.message); }
  refresh();
});

/* ================================================================ PALCO */

const inst = $("#aInst"), voc = $("#aVoc");
const seek = $("#seek");
let songId = null, lines = [], lineEls = [], wordEls = [], activeIdx = -2;
let offset = 0, raf = 0, seeking = false;

async function openPlayer(id) {
  show("player");
  let meta, ly;
  try {
    [meta, ly] = await Promise.all([api(`/api/songs/${id}`), api(`/api/songs/${id}/lyrics`)]);
  } catch (e) {
    alert(`Não foi possível abrir a música: ${e.message}`);
    location.hash = "";
    return;
  }
  songId = id;
  $("#pTitle").textContent = meta.title || "Sem título";
  $("#pArtist").textContent = meta.artist || "";
  $("#pSource").textContent = ly.source === "lrclib" ? "letra online" : "letra transcrita";
  $("#pSource").title = ly.matched || "";
  document.title = `🎤 ${meta.title}`;

  lines = [...ly.lines].sort((a, b) => a.start - b.start);
  buildLyrics();
  offset = storage.get(`offset:${id}`, 0);
  showOffset();

  inst.src = `/api/songs/${id}/audio/instrumental`;
  voc.src = `/api/songs/${id}/audio/vocals`;
  seek.max = meta.duration || 100;
  $("#tDur").textContent = fmt(meta.duration);
  applyVolumes();
  activeIdx = -2;
  cancelAnimationFrame(raf);
  raf = requestAnimationFrame(tick);
}

function closePlayer() {
  cancelAnimationFrame(raf);
  for (const a of [inst, voc]) { a.pause(); a.removeAttribute("src"); a.load(); }
  songId = null;
  document.title = "Karaoke em Casa";
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
}

function buildLyrics() {
  const box = $("#lyrics");
  box.replaceChildren();
  lineEls = [];
  wordEls = [];
  lines.forEach((ln) => {
    const d = el("div", { class: "line" });
    const spans = [];
    ln.words.forEach((w, j) => {
      if (j) d.append(" ");
      const s = el("span", { class: "w" }, w.text);
      s._p = -1;
      spans.push(s);
      d.append(s);
    });
    // clicar numa linha salta para ela
    d.addEventListener("click", () => { inst.currentTime = Math.max(0, ln.start - offset - 1); });
    box.append(d);
    lineEls.push(d);
    wordEls.push(spans);
  });
  if (!lines.length) box.append(el("div", { class: "line active" }, "(não foi encontrada letra)"));
}

function setP(span, p) {
  if (span._p !== p) { span._p = p; span.style.setProperty("--p", `${p}%`); }
}

function findActive(t) {
  let i = -1;
  for (let k = 0; k < lines.length && lines[k].start - LEAD <= t; k++) i = k;
  return i;
}

function setActive(idx) {
  activeIdx = idx;
  lineEls.forEach((d, i) => {
    d.classList.toggle("active", i === idx);
    d.classList.toggle("past", i < idx);
    d.classList.toggle("next", i === idx + 1);
    if (i !== idx) for (const s of wordEls[i]) setP(s, i < idx ? 100 : 0);
  });
  center();
}

function center() {
  const d = lineEls[Math.max(0, activeIdx)];
  if (!d) return;
  const y = $("#lyricsBox").clientHeight * 0.45 - (d.offsetTop + d.offsetHeight / 2);
  $("#lyrics").style.transform = `translateY(${y}px)`;
}
window.addEventListener("resize", center);

function updateCountdown(t) {
  const cd = $("#countdown");
  const nx = lines[activeIdx + 1];
  let txt = "";
  if (nx) {
    const gapFrom = activeIdx >= 0 ? lines[activeIdx].end : 0;
    const rem = nx.start - t;
    if (nx.start - gapFrom > 5 && t > gapFrom) {
      if (rem > 0 && rem <= 3) txt = "●".repeat(Math.ceil(rem));
      else if (t > gapFrom + 1) txt = "♪ ♪ ♪";
    }
  }
  if (cd.textContent !== txt) cd.textContent = txt;
}

function tick() {
  const t = inst.currentTime + offset;
  const idx = findActive(t);
  if (idx !== activeIdx) setActive(idx);
  if (idx >= 0) {
    lines[idx].words.forEach((w, j) => {
      const p = t <= w.start ? 0 : t >= w.end ? 100 : Math.round(((t - w.start) / (w.end - w.start)) * 100);
      setP(wordEls[idx][j], p);
    });
  }
  updateCountdown(t);
  if (!seeking) seek.value = inst.currentTime;
  $("#tCur").textContent = fmt(inst.currentTime);
  raf = requestAnimationFrame(tick);
}

/* ---- áudio: instrumental é o "relógio", a voz guia segue-o */

function syncVoc(force) {
  if (force || Math.abs(voc.currentTime - inst.currentTime) > 0.1) voc.currentTime = inst.currentTime;
}

function applyVolumes() {
  inst.volume = $("#volInst").value;
  voc.volume = $("#volVoc").value;
  storage.set("volInst", +$("#volInst").value);
  storage.set("volVoc", +$("#volVoc").value);
  if (voc.volume === 0) voc.pause();
  else if (!inst.paused && voc.paused) { syncVoc(true); voc.play().catch(() => {}); }
}

$("#volInst").value = storage.get("volInst", 1);
$("#volVoc").value = storage.get("volVoc", 0);
$("#volInst").addEventListener("input", applyVolumes);
$("#volVoc").addEventListener("input", applyVolumes);

inst.addEventListener("play", () => {
  $("#play").textContent = "❚❚";
  if (voc.volume > 0) { syncVoc(true); voc.play().catch(() => {}); }
});
inst.addEventListener("pause", () => { $("#play").textContent = "▶"; voc.pause(); wake(); });
inst.addEventListener("seeked", () => syncVoc(true));
inst.addEventListener("loadedmetadata", () => {
  seek.max = inst.duration;
  $("#tDur").textContent = fmt(inst.duration);
});
setInterval(() => { if (!inst.paused && voc.volume > 0) syncVoc(false); }, 500);

function togglePlay() { inst.paused ? inst.play() : inst.pause(); }
$("#play").addEventListener("click", togglePlay);

seek.addEventListener("input", () => { seeking = true; $("#tCur").textContent = fmt(seek.value); });
seek.addEventListener("change", () => { inst.currentTime = +seek.value; seeking = false; });

function showOffset() {
  $("#offVal").textContent = `${offset > 0 ? "+" : ""}${offset.toFixed(2).replace(/0$/, "")}s`;
}
function nudge(d) {
  offset = Math.round((offset + d) * 100) / 100;
  storage.set(`offset:${songId}`, offset);
  showOffset();
}
$("#offMinus").addEventListener("click", () => nudge(-OFFSET_STEP));
$("#offPlus").addEventListener("click", () => nudge(OFFSET_STEP));

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen();
  else $("#player").requestFullscreen().catch(() => {});
}
$("#fs").addEventListener("click", toggleFullscreen);
$("#back").addEventListener("click", () => (location.hash = ""));

/* ---- esconder os controlos quando ninguém mexe no rato */

let idleTimer = 0;
function wake() {
  $("#player").classList.remove("idle");
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => { if (!inst.paused) $("#player").classList.add("idle"); }, 3000);
}
for (const ev of ["mousemove", "touchstart", "keydown"]) $("#player").addEventListener(ev, wake);

document.addEventListener("keydown", (e) => {
  if ($("#player").hidden || e.target.matches("input:not([type=range]), select")) return;
  switch (e.key) {
    case " ": e.preventDefault(); togglePlay(); break;
    case "ArrowLeft": inst.currentTime = Math.max(0, inst.currentTime - 5); break;
    case "ArrowRight": inst.currentTime += 5; break;
    case "f": case "F": toggleFullscreen(); break;
    case "Escape": if (!document.fullscreenElement) location.hash = ""; break;
    default: return;
  }
  wake();
});

/* ================================================================ rotas */

function route() {
  const m = location.hash.match(/^#\/cantar\/([0-9a-f]{12})$/);
  if (m) return openPlayer(m[1]);
  if (songId) closePlayer();
  show("library");
  const tab = location.hash === "#/downloads" ? "downloads" : "songs";
  $("#tabSongs").hidden = tab !== "songs";
  $("#tabDownloads").hidden = tab !== "downloads";
  for (const a of document.querySelectorAll(".tab")) a.classList.toggle("on", a.dataset.tab === tab);
  refresh();
}
window.addEventListener("hashchange", route);
route();
