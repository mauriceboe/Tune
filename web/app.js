"use strict";

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
};

const $ = (id) => document.getElementById(id);

const ui = {
  backdrop: $("backdrop"),
  cover: $("cover"),
  title: $("title"),
  artist: $("artist"),
  album: $("album"),
  progressFill: $("progressFill"),
  timeNow: $("timeNow"),
  timeTotal: $("timeTotal"),
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
};

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

function renderTrack(track) {
  if (!track) {
    ui.title.textContent = "Not playing";
    ui.artist.textContent = "—";
    ui.album.textContent = "";
    ui.cover.classList.remove("has-art");
    ui.cover.style.backgroundImage = "";
    ui.backdrop.style.backgroundImage = "";
    ui.progressFill.style.width = "0%";
    ui.timeNow.textContent = "0:00";
    ui.timeTotal.textContent = "0:00";
    setIcon(false);
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
}

function tickProgress() {
  const t = state.track;
  if (t && t.playing && t.duration) {
    const elapsed = Math.min(t.duration, Math.max(0, Math.floor(Date.now() / 1000) - t.start));
    const ratio = elapsed / t.duration;
    ui.progressFill.style.width = `${Math.min(100, ratio * 100).toFixed(2)}%`;
    ui.timeNow.textContent = fmtTime(elapsed);
    ui.timeTotal.textContent = fmtTime(t.duration);
    if (state.tab === "lyrics") highlightLyric(elapsed);
  }
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

function escape(s) {
  return String(s || "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]);
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

window.__app = {
  push(updateJson) {
    const u = typeof updateJson === "string" ? JSON.parse(updateJson) : updateJson;
    if (u.status !== undefined) setStatus(u.status, u.discord_on);
    if ("track" in u) {
      state.track = u.track;
      renderTrack(u.track);
    }
    if (u.recents) renderRecents(u.recents);
    if (u.stats) renderStats(u.stats);
    if (u.history) renderHistory(u.history);
    if ("lyrics_for" in u) {
      if (u.lyrics_for === state.trackKey) renderLyrics(u.lyrics);
    }
    tickProgress();
  },
  setMini(mini) {
    state.isMini = mini;
    document.body.classList.toggle("mini", mini);
  },
  setSettings(s) {
    if (s.show_on_discord !== undefined) $("setDiscord").checked = !!s.show_on_discord;
    if (s.minimize_to_tray !== undefined) $("setTray").checked = !!s.minimize_to_tray;
    if (s.start_minimized !== undefined) $("setStartMin").checked = !!s.start_minimized;
    if (s.always_on_top !== undefined) $("setAOT").checked = !!s.always_on_top;
    if (s.autostart !== undefined) $("setAutostart").checked = !!s.autostart;
    if (s.version) ui.appVersion.textContent = s.version;
  },
};

// progress tick every 500ms
setInterval(tickProgress, 500);

function call(method, ...args) {
  if (window.pywebview && window.pywebview.api && window.pywebview.api[method]) {
    return window.pywebview.api[method](...args);
  }
  return Promise.resolve(null);
}

$("btnPlay").addEventListener("click", () => call("media_action", "toggle"));
$("btnPrev").addEventListener("click", () => call("media_action", "prev"));
$("btnNext").addEventListener("click", () => call("media_action", "next"));
$("btnMini").addEventListener("click", () => call("toggle_mini"));
$("btnMin").addEventListener("click", () => call("minimize"));
$("btnClose").addEventListener("click", () => call("close_window"));

$("setDiscord").addEventListener("change", (e) => call("save_setting", "show_on_discord", e.target.checked));
$("setTray").addEventListener("change", (e) => call("save_setting", "minimize_to_tray", e.target.checked));
$("setStartMin").addEventListener("change", (e) => call("save_setting", "start_minimized", e.target.checked));
$("setAOT").addEventListener("change", (e) => call("save_setting", "always_on_top", e.target.checked));
$("setAutostart").addEventListener("change", (e) => call("set_autostart", e.target.checked));

window.addEventListener("pywebviewready", () => {
  call("ready").then((s) => { if (s) window.__app.setSettings(s); });
});

document.addEventListener("click", (e) => {
  const a = e.target.closest("a.ext-link");
  if (!a) return;
  e.preventDefault();
  call("open_url", a.href);
});

// Frameless-window edge resize via Python bridge
(function () {
  let active = null;
  let lastSent = 0;

  function onMouseDown(e) {
    const el = e.target.closest(".resize-edge");
    if (!el) return;
    e.preventDefault();
    active = {
      mode: el.dataset.resize,
      sx: e.screenX,
      sy: e.screenY,
      w: window.outerWidth,
      h: window.outerHeight,
    };
    document.body.style.userSelect = "none";
  }
  function onMouseMove(e) {
    if (!active) return;
    const dx = e.screenX - active.sx;
    const dy = e.screenY - active.sy;
    let w = active.w, h = active.h;
    let dragLeft = false;
    if (active.mode.includes("r")) w = active.w + dx;
    if (active.mode.includes("l")) { w = active.w - dx; dragLeft = true; }
    if (active.mode.includes("b")) h = active.h + dy;
    w = Math.max(380, Math.min(1600, w));
    h = Math.max(460, Math.min(1400, h));
    const now = performance.now();
    if (now - lastSent < 32) return;
    lastSent = now;
    call("resize_window", Math.round(w), Math.round(h), dragLeft);
  }
  function onMouseUp() {
    if (active) {
      active = null;
      document.body.style.userSelect = "";
    }
  }
  document.addEventListener("mousedown", onMouseDown);
  document.addEventListener("mousemove", onMouseMove);
  document.addEventListener("mouseup", onMouseUp);
  document.addEventListener("mouseleave", onMouseUp);
})();
