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
  playbar: $("playbar"),
  playbarFill: $("playbarFill"),
  playbarHandle: $("playbarHandle"),
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

// ---- playbar (progress + click/drag seek) ----
function paintPlaybar(ratio) {
  const r = Math.min(1, Math.max(0, ratio));
  const pct = (r * 100).toFixed(2);
  ui.playbarFill.style.width = `${pct}%`;
  ui.playbarHandle.style.left = `${pct}%`;
  ui.playbar.setAttribute("aria-valuenow", String(Math.round(r * 100)));
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
    paintPlaybar(0);
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
    // Immediately swap to a loading state so the user sees a response,
    // even while the backend is still walking the lyrics provider chain.
    renderLyricsLoading();
    ui.title.classList.remove("title-fade");
    void ui.title.offsetWidth;
    ui.title.classList.add("title-fade");
    ui.artist.classList.remove("title-fade");
    void ui.artist.offsetWidth;
    ui.artist.classList.add("title-fade");
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
    ui.miniBarCover.classList.remove("has-art");
    return;
  }
  bar.classList.remove("empty");
  ui.miniBarTitle.textContent = last.title || "—";
  ui.miniBarArtist.textContent = last.artist || "";
  ui.miniBarTime.textContent = last.duration ? fmtTime(last.duration) : "";
  if (last.cover_data_url) {
    ui.miniBarCover.style.backgroundImage = `url("${last.cover_data_url}")`;
    ui.miniBarCover.classList.add("has-art");
  } else {
    ui.miniBarCover.style.backgroundImage = "";
    ui.miniBarCover.classList.remove("has-art");
  }
}

function setShuffleState(active) {
  ui.btnShuffle.classList.toggle("active", !!active);
}

function setRepeatState(mode) {
  ui.btnRepeat.classList.remove("active", "repeat-track");
  if (mode === "list") ui.btnRepeat.classList.add("active");
  if (mode === "track") ui.btnRepeat.classList.add("active", "repeat-track");
}

let _lastTextTick = 0;
let _lastShownSecond = -1;

function tickProgress(now = performance.now()) {
  const t = state.track;
  if (!t || !t.duration) return;
  const elapsedFloat = t.playing
    ? Math.min(t.duration, Math.max(0, Date.now() / 1000 - t.start))
    : Math.min(t.duration, Math.max(0, t.position || 0));
  const ratio = elapsedFloat / t.duration;
  state.lastRatio = ratio;
  paintPlaybar(ratio);
  // Text: only refresh when the visible second changes — once per second max.
  const second = Math.floor(elapsedFloat);
  if (second !== _lastShownSecond) {
    _lastShownSecond = second;
    ui.timeNow.textContent = fmtTime(second);
    ui.timeRemain.textContent = `-${fmtTime(Math.max(0, t.duration - second))}`;
  }
  if (state.tab === "lyrics" && now - _lastTextTick > 250) {
    _lastTextTick = now;
    highlightLyric(elapsedFloat);
  }
}

let _rafHandle = 0;
function rafLoop() {
  _rafHandle = 0;
  tickProgress(performance.now());
  if (state.track) {
    _rafHandle = requestAnimationFrame(rafLoop);
  }
}
function startRaf() {
  if (!_rafHandle) _rafHandle = requestAnimationFrame(rafLoop);
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

function renderLyricsLoading() {
  state.lyrics = null;
  state.lyricsActiveIdx = -1;
  state.lyricsHasWordTiming = false;
  ui.lyricsScroll.innerHTML = "";
  ui.lyricsStatus.textContent = "Loading lyrics…";
  const skeleton = document.createElement("div");
  skeleton.className = "lyric-skeleton";
  for (let i = 0; i < 6; i++) {
    const bar = document.createElement("div");
    bar.className = "lyric-skeleton-bar";
    bar.style.setProperty("--w", `${40 + Math.floor(Math.random() * 50)}%`);
    skeleton.appendChild(bar);
  }
  ui.lyricsScroll.appendChild(skeleton);
}

function renderLyrics(lines) {
  state.lyrics = lines;
  state.lyricsActiveIdx = -1;
  state.lyricsHasWordTiming = !!(lines && lines.some((l) => Array.isArray(l[2]) && l[2].length));
  ui.lyricsScroll.innerHTML = "";
  if (!lines || !lines.length) {
    ui.lyricsStatus.textContent = "No synced lyrics found.";
    return;
  }
  ui.lyricsStatus.textContent = `${lines.length} lines${state.lyricsHasWordTiming ? " · karaoke" : " · synced"}`;
  for (let i = 0; i < lines.length; i++) {
    const [, text, words] = lines[i];
    const div = document.createElement("div");
    div.className = "lyric-line";
    if (Array.isArray(words) && words.length) {
      for (const [, wText] of words) {
        const span = document.createElement("span");
        span.className = "lyric-word";
        span.textContent = wText;
        div.appendChild(span);
      }
      div.dataset.hasWords = "1";
    } else {
      div.textContent = text || "♪";
    }
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
  const lineEls = ui.lyricsScroll.children;
  if (idx !== state.lyricsActiveIdx) {
    if (state.lyricsActiveIdx >= 0 && lineEls[state.lyricsActiveIdx]) {
      lineEls[state.lyricsActiveIdx].classList.remove("active");
    }
    if (idx >= 0 && lineEls[idx]) {
      lineEls[idx].classList.add("active");
      _scrollToLine(lineEls[idx]);
    }
    // Update neighbour styling for the Apple-Music-style depth-of-field look
    for (let i = 0; i < lineEls.length; i++) {
      const dist = Math.abs(i - idx);
      lineEls[i].style.setProperty("--lyric-dist", String(Math.min(dist, 5)));
    }
    state.lyricsActiveIdx = idx;
  }
  // Word-level highlight inside the active line, if karaoke timing is present
  if (state.lyricsHasWordTiming && idx >= 0) {
    const line = state.lyrics[idx];
    const words = Array.isArray(line[2]) ? line[2] : null;
    const el = lineEls[idx];
    if (words && el && el.dataset.hasWords === "1") {
      const lineStart = line[0];
      const local = elapsed - lineStart;
      const spans = el.children;
      for (let w = 0; w < words.length && w < spans.length; w++) {
        const wt = words[w][0];
        const passed = local >= wt;
        spans[w].classList.toggle("passed", passed);
      }
    }
  }
}

function _scrollToLine(el) {
  const scroller = ui.lyricsScroll;
  const elTop = el.offsetTop;
  const targetScroll = elTop - scroller.clientHeight / 2 + el.clientHeight / 2;
  scroller.scrollTo({ top: targetScroll, behavior: "smooth" });
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
    tickProgress(performance.now());
    startRaf();
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

// ---- playbar seek (click + drag) ----
(function () {
  let scrubbing = false;
  let pendingSeek = null;
  let seekTimer = null;

  function ratioFromEvent(e) {
    const rect = ui.playbar.getBoundingClientRect();
    return Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
  }

  function commitSeek(seconds) {
    if (seekTimer) clearTimeout(seekTimer);
    // Coalesce rapid drag updates so we don't flood the backend
    pendingSeek = seconds;
    seekTimer = setTimeout(() => {
      if (pendingSeek != null) rpc("media_seek", { seconds: pendingSeek });
      pendingSeek = null;
    }, 50);
  }

  ui.playbar.addEventListener("pointerdown", (e) => {
    const t = state.track;
    if (!t || !t.duration) return;
    scrubbing = true;
    ui.playbar.classList.add("scrubbing");
    ui.playbar.setPointerCapture(e.pointerId);
    const ratio = ratioFromEvent(e);
    paintPlaybar(ratio);
    commitSeek(Math.floor(t.duration * ratio));
  });

  ui.playbar.addEventListener("pointermove", (e) => {
    if (!scrubbing) return;
    const t = state.track;
    if (!t || !t.duration) return;
    const ratio = ratioFromEvent(e);
    paintPlaybar(ratio);
    commitSeek(Math.floor(t.duration * ratio));
  });

  function endScrub(e) {
    if (!scrubbing) return;
    scrubbing = false;
    ui.playbar.classList.remove("scrubbing");
    try { ui.playbar.releasePointerCapture(e.pointerId); } catch (_) {}
  }
  ui.playbar.addEventListener("pointerup", endScrub);
  ui.playbar.addEventListener("pointercancel", endScrub);

  // Keyboard support: ←/→ jump 5s, Shift+←/→ jump 30s
  ui.playbar.addEventListener("keydown", (e) => {
    const t = state.track;
    if (!t || !t.duration) return;
    let delta = 0;
    if (e.key === "ArrowLeft") delta = e.shiftKey ? -30 : -5;
    if (e.key === "ArrowRight") delta = e.shiftKey ? 30 : 5;
    if (delta === 0) return;
    e.preventDefault();
    const cur = (state.lastRatio || 0) * t.duration;
    const target = Math.max(0, Math.min(t.duration, Math.floor(cur + delta)));
    rpc("media_seek", { seconds: target });
  });
})();

paintPlaybar(0);

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
