"use strict";

// Tauri 2 globals exposed via withGlobalTauri:true in tauri.conf.json.
const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;
const { getCurrentWindow } = window.__TAURI__.window;
const appWindow = getCurrentWindow();

const state = {
  tab: "now",
  isMini: false,
  track: null,
  trackKey: null,
  lyrics: null,
  lyricsActiveIdx: -1,
  accent: "#fc3c44",
  recentSig: "",
  historySig: "",
  recents: [],
};

const $ = (id) => document.getElementById(id);

const ui = {
  backdrop: $("backdrop"),
  cover: $("cover"),
  title: $("title"),
  artist: $("artist"),
  album: $("album"),
  waveform: $("waveform"),
  timeNow: $("timeNow"),
  timeRemain: $("timeRemain"),
  iconPlay: $("iconPlay"),
  statusDot: $("statusDot"),
  statusText: $("statusText"),
  recentList: $("recentList"),
  historyList: $("historyList"),
  lyricsScroll: $("lyricsScroll"),
  lyricsStatus: $("lyricsStatus"),
  statTime: $("statTime"),
  statTracks: $("statTracks"),
  statTop: $("statTop"),
  appVersion: $("appVersion"),
  btnShuffle: $("btnShuffle"),
  btnRepeat: $("btnRepeat"),
  miniBar: $("miniBar"),
  miniBarCover: $("miniBarCover"),
  miniBarTitle: $("miniBarTitle"),
  miniBarArtist: $("miniBarArtist"),
  miniBarTime: $("miniBarTime"),
  updatePanel: $("updatePanel"),
  updateHeadline: $("updateHeadline"),
  updateSubline: $("updateSubline"),
  updateCurrent: $("updateCurrent"),
  updateBtn: $("updateBtn"),
  updateProgressWrap: $("updateProgressWrap"),
  updateProgressFill: $("updateProgressFill"),
  updateProgressPct: $("updateProgressPct"),
  updateProgressBytes: $("updateProgressBytes"),
  updateError: $("updateError"),
};

// ---- RPC helper ----
async function rpc(method, params = {}) {
  try {
    return await invoke("rpc", { method, params });
  } catch (e) {
    console.error(`rpc(${method})`, e);
    return null;
  }
}

// ---- formatting helpers ----
function fmtTime(s) {
  s = Math.max(0, Math.floor(s || 0));
  if (s >= 3600) {
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  }
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function fmtDurationHuman(s) {
  s = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

function fmtBytes(n) {
  if (!n || n < 0) return "0";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

const ICON_PLAY = '<path d="M8 5v14l11-7z"/>';
const ICON_PAUSE = '<path d="M6 5h4v14H6zM14 5h4v14h-4z"/>';

function setIcon(playing) {
  ui.iconPlay.innerHTML = playing ? ICON_PAUSE : ICON_PLAY;
}

function setStatus(status, discord_on) {
  ui.statusText.textContent = status;
  ui.statusDot.classList.remove("playing", "paused", "error");
  if (status === "Playing") ui.statusDot.classList.add(discord_on ? "playing" : "paused");
  else if (status === "Paused") ui.statusDot.classList.add("paused");
  else if (status.startsWith("Error")) ui.statusDot.classList.add("error");
}

function applyAccent(hex) {
  if (!hex || hex === state.accent) return;
  state.accent = hex;
  document.documentElement.style.setProperty("--accent", hex);
}

function showTab(name) {
  state.tab = name;
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".tabpanel").forEach(p => p.classList.toggle("active", p.dataset.panel === name));
}

document.querySelectorAll(".tab").forEach(t => {
  t.addEventListener("click", () => showTab(t.dataset.tab));
});

// ---- waveform ----
const WAVEFORM_BARS = 56;

function hashSeed(str) {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h >>> 0;
}

function mulberry32(seed) {
  let t = seed >>> 0;
  return function () {
    t = (t + 0x6D2B79F5) >>> 0;
    let r = Math.imul(t ^ (t >>> 15), 1 | t);
    r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
  };
}

function buildWaveform(seedKey) {
  const rng = mulberry32(hashSeed(seedKey || "tune"));
  const frag = document.createDocumentFragment();
  for (let i = 0; i < WAVEFORM_BARS; i++) {
    const base = 0.25 + 0.35 * Math.abs(Math.sin((i / WAVEFORM_BARS) * Math.PI * 2.6));
    const jitter = (rng() - 0.5) * 0.55;
    const h = Math.max(0.18, Math.min(1, base + jitter));
    const bar = document.createElement("div");
    bar.className = "wf-bar";
    bar.style.setProperty("--h", `${(h * 100).toFixed(1)}%`);
    frag.appendChild(bar);
  }
  ui.waveform.innerHTML = "";
  ui.waveform.appendChild(frag);
}

function paintWaveform(ratio) {
  const bars = ui.waveform.children;
  const n = bars.length;
  if (!n) return;
  const headIdx = Math.min(n - 1, Math.max(0, Math.floor(ratio * n)));
  for (let i = 0; i < n; i++) {
    const b = bars[i];
    b.classList.toggle("played", i < headIdx);
    b.classList.toggle("head", i === headIdx);
  }
}

// ---- track render ----
function renderTrack(track) {
  if (!track) {
    ui.title.textContent = "Not playing";
    ui.artist.textContent = "—";
    ui.album.textContent = "";
    ui.cover.classList.remove("has-art");
    ui.cover.style.backgroundImage = "";
    ui.backdrop.style.backgroundImage = "";
    paintWaveform(0);
    ui.timeNow.textContent = "0:00";
    ui.timeRemain.textContent = "-0:00";
    setIcon(false);
    setShuffleState(false);
    setRepeatState("none");
    return;
  }

  const newKey = `${track.artist}|${track.title}`;
  const trackChanged = newKey !== state.trackKey;
  state.trackKey = newKey;

  if (trackChanged) {
    ui.title.textContent = track.title || "—";
    ui.artist.textContent = track.artist || "";
    ui.album.textContent = track.album || "";
    ui.title.classList.remove("title-fade");
    void ui.title.offsetWidth;
    ui.title.classList.add("title-fade");
    ui.artist.classList.remove("title-fade");
    void ui.artist.offsetWidth;
    ui.artist.classList.add("title-fade");
    buildWaveform(newKey);
  }

  if (track.cover_data_url) {
    if (track.cover_data_url !== ui.cover.dataset.coverSig) {
      ui.cover.dataset.coverSig = track.cover_data_url;
      ui.cover.style.backgroundImage = `url("${track.cover_data_url}")`;
      ui.backdrop.style.backgroundImage = `url("${track.cover_data_url}")`;
      ui.cover.classList.add("has-art");
      ui.cover.classList.remove("swap");
      void ui.cover.offsetWidth;
      ui.cover.classList.add("swap");
    }
  } else {
    ui.cover.classList.remove("has-art");
    ui.cover.style.backgroundImage = "";
    ui.backdrop.style.backgroundImage = "";
    ui.cover.dataset.coverSig = "";
  }

  if (track.accent) applyAccent(track.accent);
  setIcon(track.playing);
  setShuffleState(!!track.shuffle);
  setRepeatState(track.repeat || "none");
}

function renderMiniBar(recents) {
  const last = (recents && recents.length > 1) ? recents[1] : null;
  const bar = ui.miniBar;
  if (!last) {
    bar.classList.add("empty");
    ui.miniBarTitle.textContent = "—";
    ui.miniBarArtist.textContent = "Nothing yet this session";
    ui.miniBarTime.textContent = "";
    ui.miniBarCover.style.backgroundImage = "";
    return;
  }
  bar.classList.remove("empty");
  ui.miniBarTitle.textContent = last.title || "—";
  ui.miniBarArtist.textContent = last.artist || "";
  ui.miniBarTime.textContent = last.duration ? fmtTime(last.duration) : "";
  ui.miniBarCover.style.backgroundImage = "";
}

function setShuffleState(active) {
  ui.btnShuffle.classList.toggle("active", !!active);
}

function setRepeatState(mode) {
  ui.btnRepeat.classList.remove("active", "repeat-track");
  if (mode === "list") ui.btnRepeat.classList.add("active");
  if (mode === "track") ui.btnRepeat.classList.add("active", "repeat-track");
}

function tickProgress() {
  const t = state.track;
  if (t && t.duration) {
    const elapsed = t.playing
      ? Math.min(t.duration, Math.max(0, Math.floor(Date.now() / 1000) - t.start))
      : Math.min(t.duration, Math.max(0, t.position || 0));
    const ratio = t.duration ? elapsed / t.duration : 0;
    paintWaveform(ratio);
    ui.timeNow.textContent = fmtTime(elapsed);
    ui.timeRemain.textContent = `-${fmtTime(Math.max(0, t.duration - elapsed))}`;
    if (state.tab === "lyrics") highlightLyric(elapsed);
  }
}

// ---- recents / history / stats / lyrics ----
function escape(s) {
  return String(s || "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]);
}

function renderRecents(items) {
  const sig = items.map(e => `${e.title}|${e.artist}`).join(";");
  if (sig === state.recentSig) return;
  state.recentSig = sig;
  ui.recentList.innerHTML = "";
  if (!items.length) {
    ui.recentList.innerHTML = '<div class="empty">No tracks yet.</div>';
    return;
  }
  for (const e of items) {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `<span class="dot"></span><span class="text">${escape(e.title)} · ${escape(e.artist)}</span>`;
    ui.recentList.appendChild(row);
  }
}

function renderHistory(items) {
  const sig = items.map(e => `${e.ts}|${e.title}|${e.artist}`).join(";");
  if (sig === state.historySig) return;
  state.historySig = sig;
  ui.historyList.innerHTML = "";
  if (!items.length) {
    ui.historyList.innerHTML = '<div class="empty">No history yet.</div>';
    return;
  }
  for (const e of items) {
    const row = document.createElement("div");
    row.className = "row";
    const t = new Date((e.ts || 0) * 1000);
    const time = `${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`;
    row.innerHTML = `<span class="dot"></span><span class="text">${escape(e.title)} · ${escape(e.artist)}</span><span class="time">${time}</span>`;
    ui.historyList.appendChild(row);
  }
}

function renderLyrics(lines) {
  state.lyrics = lines;
  state.lyricsActiveIdx = -1;
  ui.lyricsScroll.innerHTML = "";
  if (!lines || !lines.length) {
    ui.lyricsStatus.textContent = "No synced lyrics found.";
    return;
  }
  ui.lyricsStatus.textContent = `${lines.length} lines · synced`;
  for (const [, text] of lines) {
    const div = document.createElement("div");
    div.className = "lyric-line";
    div.textContent = text || "♪";
    ui.lyricsScroll.appendChild(div);
  }
}

function highlightLyric(elapsed) {
  if (!state.lyrics || !state.lyrics.length) return;
  let idx = -1;
  for (let i = 0; i < state.lyrics.length; i++) {
    if (state.lyrics[i][0] <= elapsed) idx = i;
    else break;
  }
  if (idx === state.lyricsActiveIdx) return;
  const lines = ui.lyricsScroll.children;
  if (state.lyricsActiveIdx >= 0 && lines[state.lyricsActiveIdx]) {
    lines[state.lyricsActiveIdx].classList.remove("active");
  }
  if (idx >= 0 && lines[idx]) {
    lines[idx].classList.add("active");
    const el = lines[idx];
    const scroller = ui.lyricsScroll;
    const elTop = el.offsetTop;
    const targetScroll = elTop - scroller.clientHeight / 2 + el.clientHeight / 2;
    scroller.scrollTo({ top: targetScroll, behavior: "smooth" });
  }
  state.lyricsActiveIdx = idx;
}

function renderStats(s) {
  ui.statTime.textContent = fmtDurationHuman(s.total_seconds);
  ui.statTracks.textContent = String(s.tracks);
  ui.statTop.textContent = s.top_artist || "—";
}

// ---- update panel ----
const updateState = { kind: "idle", active: false, pending: false };

function renderUpdate(s) {
  Object.assign(updateState, s || {});
  if (s.current && ui.updateCurrent) ui.updateCurrent.textContent = s.current;

  const kind = s.kind || "idle";
  const v = s.version || s.tag || "";
  const headline = ui.updateHeadline;
  const sub = ui.updateSubline;
  const btn = ui.updateBtn;

  headline.classList.remove("is-ready", "is-error");
  ui.updateError.hidden = true;
  ui.updateProgressWrap.hidden = true;
  btn.classList.remove("is-primary");
  btn.disabled = false;

  switch (kind) {
    case "ready":
      headline.textContent = `Update v${v} ready to install`;
      headline.classList.add("is-ready");
      sub.textContent = "Tune will close and relaunch on the new version.";
      btn.textContent = "Restart & install";
      btn.classList.add("is-primary");
      break;
    case "downloading":
      headline.textContent = `Downloading update v${v}…`;
      sub.textContent = "Hang tight — this won't take long.";
      btn.textContent = "Downloading…";
      btn.disabled = true;
      ui.updateProgressWrap.hidden = false;
      const pct = typeof s.progress === "number" ? s.progress : 0;
      ui.updateProgressFill.style.width = `${pct}%`;
      ui.updateProgressPct.textContent = `${pct}%`;
      ui.updateProgressBytes.textContent = `${fmtBytes(s.bytes || 0)} / ${fmtBytes(s.total || 0)}`;
      break;
    case "checking":
      headline.textContent = "Checking for updates…";
      sub.textContent = "Talking to GitHub.";
      btn.textContent = "Checking…";
      btn.disabled = true;
      break;
    case "available":
      headline.textContent = `Update v${v} available`;
      sub.textContent = `Will download automatically.`;
      btn.textContent = "Download now";
      break;
    case "up_to_date":
      headline.textContent = "You're on the latest version";
      sub.textContent = `Current version v${s.current || updateState.current || "—"}.`;
      btn.textContent = "Check again";
      break;
    case "error":
      headline.textContent = "Update failed";
      headline.classList.add("is-error");
      sub.textContent = "Click to retry.";
      btn.textContent = "Retry";
      ui.updateError.hidden = false;
      ui.updateError.textContent = String(s.error || "unknown error");
      break;
    default:
      headline.textContent = "Updates";
      sub.textContent = `Current version v${s.current || updateState.current || "—"}.`;
      btn.textContent = "Check now";
  }
}

ui.updateBtn.addEventListener("click", async () => {
  if (updateState.kind === "ready") {
    await invoke("install_update");
    return;
  }
  await invoke("check_for_updates");
});

// ---- backend events ----
listen("backend://event", (e) => {
  const u = e.payload;
  if (!u) return;
  if (u.event === "tick") {
    const d = u.data || {};
    if (d.status !== undefined) setStatus(d.status, d.discord_on);
    if ("track" in d) {
      state.track = d.track;
      renderTrack(d.track);
    }
    if (d.recents) {
      state.recents = d.recents;
      renderRecents(d.recents);
      renderMiniBar(d.recents);
    }
    if (d.stats) renderStats(d.stats);
    if (d.history) renderHistory(d.history);
    tickProgress();
  } else if (u.event === "lyrics") {
    if (u.data && u.data.key === state.trackKey) renderLyrics(u.data.lines);
  } else if (u.event === "ready") {
    const d = u.data || {};
    applySettings(d.settings || {});
    if (d.version) {
      ui.appVersion.textContent = d.version;
      if (ui.updateCurrent) ui.updateCurrent.textContent = d.version;
    }
  }
});

listen("update://event", (e) => {
  if (e.payload) renderUpdate(e.payload);
});

// ---- settings wiring ----
function applySettings(s) {
  if (s.show_on_discord !== undefined) $("setDiscord").checked = !!s.show_on_discord;
  if (s.minimize_to_tray !== undefined) $("setTray").checked = !!s.minimize_to_tray;
  if (s.start_minimized !== undefined) $("setStartMin").checked = !!s.start_minimized;
  if (s.always_on_top !== undefined) $("setAOT").checked = !!s.always_on_top;
  if (s.autostart !== undefined) $("setAutostart").checked = !!s.autostart;
  if (s.auto_check_updates !== undefined) $("setAutoUpdate").checked = !!s.auto_check_updates;
}

$("setDiscord").addEventListener("change", (e) => rpc("save_setting", { key: "show_on_discord", value: e.target.checked }));
$("setTray").addEventListener("change", (e) => rpc("save_setting", { key: "minimize_to_tray", value: e.target.checked }));
$("setStartMin").addEventListener("change", (e) => rpc("save_setting", { key: "start_minimized", value: e.target.checked }));
$("setAOT").addEventListener("change", async (e) => {
  await rpc("save_setting", { key: "always_on_top", value: e.target.checked });
  await invoke("set_always_on_top", { enabled: e.target.checked });
});
$("setAutostart").addEventListener("change", (e) => invoke("set_autostart", { enabled: e.target.checked }));
$("setAutoUpdate").addEventListener("change", (e) => rpc("save_setting", { key: "auto_check_updates", value: e.target.checked }));

// ---- media buttons ----
$("btnPlay").addEventListener("click", () => rpc("media_action", { action: "toggle" }));
$("btnPrev").addEventListener("click", () => rpc("media_action", { action: "prev" }));
$("btnNext").addEventListener("click", () => rpc("media_action", { action: "next" }));
$("btnShuffle").addEventListener("click", () => rpc("media_action", { action: "shuffle_toggle" }));
$("btnRepeat").addEventListener("click", () => rpc("media_action", { action: "repeat_cycle" }));
$("btnMini").addEventListener("click", () => invoke("toggle_mini"));
$("btnMin").addEventListener("click", () => appWindow.minimize());
$("btnClose").addEventListener("click", () => appWindow.hide());

// ---- waveform seek ----
ui.waveform.addEventListener("click", (e) => {
  const t = state.track;
  if (!t || !t.duration) return;
  const rect = ui.waveform.getBoundingClientRect();
  const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
  const targetSeconds = Math.floor(t.duration * ratio);
  rpc("media_seek", { seconds: targetSeconds });
});

buildWaveform("tune");
setInterval(tickProgress, 500);

// ---- progress tick that polls update state on Settings tab ----
setInterval(async () => {
  if (state.tab === "settings") {
    const s = await invoke("get_update_state");
    if (s) renderUpdate(s);
  }
}, 600);

// ---- cover parallax ----
(function () {
  const cover = ui.cover;
  if (!cover) return;
  const glare = document.createElement("div");
  glare.className = "cover-glare";
  cover.appendChild(glare);

  const MAX_TILT = 9;
  const MAX_LIFT = 14;
  let raf = null;
  let pending = null;

  function apply() {
    raf = null;
    if (!pending) return;
    const { rx, ry, gx, gy } = pending;
    cover.style.transform = `perspective(900px) rotateX(${rx.toFixed(2)}deg) rotateY(${ry.toFixed(2)}deg) translateZ(${MAX_LIFT}px)`;
    cover.style.setProperty("--glare-x", `${gx}%`);
    cover.style.setProperty("--glare-y", `${gy}%`);
  }

  cover.addEventListener("mousemove", (e) => {
    const rect = cover.getBoundingClientRect();
    const px = (e.clientX - rect.left) / rect.width;
    const py = (e.clientY - rect.top) / rect.height;
    pending = {
      rx: (0.5 - py) * 2 * MAX_TILT,
      ry: (px - 0.5) * 2 * MAX_TILT,
      gx: px * 100,
      gy: py * 100,
    };
    cover.classList.add("tilting");
    if (!raf) raf = requestAnimationFrame(apply);
  });

  function reset() {
    pending = null;
    if (raf) { cancelAnimationFrame(raf); raf = null; }
    cover.classList.remove("tilting");
    cover.style.transform = "";
  }
  cover.addEventListener("mouseleave", reset);
  cover.addEventListener("blur", reset);
})();

// ---- frameless resize ----
const RESIZE_DIRS = { l: "West", r: "East", b: "South", br: "SouthEast", bl: "SouthWest" };
document.querySelectorAll(".resize-edge").forEach(el => {
  el.addEventListener("mousedown", (e) => {
    e.preventDefault();
    const dir = RESIZE_DIRS[el.dataset.resize];
    if (dir) appWindow.startResizeDragging(dir).catch(() => {});
  });
});
