"use strict";
const $ = id => document.getElementById(id);
const root = $("archive"), audio = $("audio");
const zone = "Africa/Dar_es_Salaam";
const names = Object.fromEntries([...document.querySelectorAll('input[name="station"]')].map(r => [r.value, r.parentElement.querySelector("span").textContent]));
let current = null, generation = 0, loadGeneration = 0, neighbor = null;
let rangeEnd = null, rangeFinished = false, loading = false, moving = false;
const shortTime = stamp => new Intl.DateTimeFormat("en-GB", {timeZone: zone, hour: "2-digit", minute: "2-digit", hourCycle: "h23"}).format(new Date(stamp * 1000));
const humanDate = value => new Intl.DateTimeFormat("en-GB", {timeZone: zone, weekday: "long", day: "numeric", month: "long", year: "numeric"}).format(new Date(value + "T12:00:00+03:00"));
const elapsed = n => `${Math.floor(n / 60).toString().padStart(2, "0")}:${Math.floor(n % 60).toString().padStart(2, "0")}`;
function node(tag, text, className) { const item = document.createElement(tag); item.textContent = text; if (className) item.className = className; return item; }
function selectedStation() { return document.querySelector('input[name="station"]:checked').value; }
function selection() { return {station: selectedStation(), date: $("date").value, time: $("hour").value ? `${$("hour").value}:${$("minute").value}` : ""}; }
function clearMessage() { $("message").hidden = true; $("message").replaceChildren(); }
function tell(text) { $("message").replaceChildren(node("p", text)); $("message").hidden = false; }
function invalidate() { generation++; $("go").disabled = false; $("go").textContent = "\u25b6 FIND & PLAY"; }
async function api(path, params) {
  const response = await fetch(path + (params ? "?" + new URLSearchParams(params) : ""), {cache: "no-store"});
  if (response.status === 401) {
    audio.pause();
    location.assign("/login?next=" + encodeURIComponent(location.pathname + location.search));
    throw new Error("Please sign in again to listen.");
  }
  let data;
  try { data = await response.json(); } catch (_) { throw new Error("We could not connect to the archive. Please try again."); }
  if (!response.ok) { const error = new Error(data.error || "We could not load this broadcast. Please try again."); error.data = data; throw error; }
  return data;
}
function dayButtons() {
  const value = $("date").value;
  $("selected-date").textContent = value ? humanDate(value) : "Choose a date.";
  for (const id of ["today", "yesterday", "choose-date"]) {
    const active = id === "choose-date" ? ![root.dataset.today, root.dataset.yesterday].includes(value) : value === root.dataset[id];
    $(id).setAttribute("aria-pressed", String(active));
  }
}
function setForm(values) {
  const station = names[values.station] ? values.station : "radio-one";
  document.querySelector(`input[name="station"][value="${station}"]`).checked = true;
  $("date").value = values.date || root.dataset.today;
  const time = /^([01]\d|2[0-3]):[0-5]\d$/.test(values.time || "") ? values.time.split(":") : ["", "00"];
  $("hour").value = time[0]; $("minute").value = time[1];
  $("custom-date").hidden = [root.dataset.today, root.dataset.yesterday].includes($("date").value);
  $("range-station").value = station; $("range-date").value = $("date").value;
  dayButtons();
}
function broadcastURL(values) { return `/archive/${encodeURIComponent(values.station)}/${encodeURIComponent(values.date)}?time=${encodeURIComponent(values.time)}`; }
function bookmark(values, push = true) {
  const url = broadcastURL(values);
  if (location.pathname + location.search !== url) history[push ? "pushState" : "replaceState"](values, "", url);
  $("bookmark-link").href = url;
}
function formChanged() {
  invalidate(); clearMessage(); dayButtons();
  try { localStorage.setItem("radio.lastStation", selectedStation()); } catch (_) { /* Storage can be disabled. */ }
  $("range-station").value = selectedStation(); $("range-date").value = $("date").value;
  if (current && !audio.paused) tell(`You are still listening to ${current.station_name}. Press Find & Play to listen to your new selection.`);
}
function missing(error, values) {
  $("message").replaceChildren(node("h2", "Recording Not Available"), node("p", `We could not find a ${names[values.station]} recording for ${humanDate(values.date)} at ${values.time}.`));
  const nearby = error.data?.nearest || [];
  if (nearby.length) {
    $("message").append(node("p", "Nearest available recordings:"));
    const choices = node("div", "", "nearby");
    for (const row of nearby) {
      const label = `${row.date !== values.date ? humanDate(row.date) + " at " : ""}${shortTime(row.timestamp)}`;
      const button = node("button", `\u25b6 Play ${label}`);
      button.addEventListener("click", () => { invalidate(); clearMessage(); rangeEnd = null; load(row, 0, true); });
      choices.append(button);
    }
    $("message").append(choices);
  } else $("message").append(node("p", "Try another time or day. Recent broadcasts may take up to 10 minutes to become available."));
  $("message").hidden = false; $("message").focus();
}
async function findPlay(event) {
  event.preventDefault();
  if (!$("search").reportValidity()) return;
  const values = selection(), version = ++generation;
  loadGeneration++; audio.onloadedmetadata = null; audio.pause(); loading = false; clearMessage();
  $("go").disabled = true; $("go").textContent = "Finding your broadcast...";
  try {
    const result = await api("/api/resolve", values);
    if (version !== generation) return;
    rangeEnd = null;
    bookmark(values); load(result.recording, result.offset, true, false);
  } catch (error) {
    if (version !== generation) return;
    if (error.data?.nearest) missing(error, values); else tell(error.message);
  } finally {
    if (version === generation) { $("go").disabled = false; $("go").textContent = "\u25b6 FIND & PLAY"; }
  }
}
function playbackState() {
  $("play-pause").textContent = audio.paused ? "\u25b6 PLAY" : "\u275a\u275a PAUSE";
  $("play-state").textContent = loading ? "Loading your broadcast..." : audio.paused ? "Paused - press Play to listen" : "NOW PLAYING";
}
async function play() {
  const version = loadGeneration;
  try { await audio.play(); }
  catch (_) { if (version === loadGeneration) $("player-message").textContent = "Ready to listen. Press the large PLAY button."; }
  if (version === loadGeneration) playbackState();
}
function load(row, offset = 0, focus = false, updateURL = true, autoplay = true) {
  const version = ++loadGeneration;
  audio.pause(); loading = true; moving = false; rangeFinished = false; current = row;
  $("player").hidden = false; $("finder").open = false;
  $("now-playing").textContent = row.station_name;
  $("player-date").textContent = humanDate(row.date);
  $("broadcast-time").textContent = shortTime(row.timestamp + offset);
  $("player-message").textContent = "";
  $("player-download").href = row.download_url;
  $("seek-start").textContent = shortTime(row.timestamp);
  $("seek-end").textContent = shortTime(row.end_timestamp);
  $("seek").value = "0";
  if ($("file-detail")) $("file-detail").textContent = row.filename;
  if (updateURL) bookmark({station: row.station, date: row.date, time: shortTime(row.timestamp + offset)}, focus);
  audio.onloadedmetadata = async () => {
    if (version !== loadGeneration) return;
    loading = false;
    const limit = Number.isFinite(audio.duration) ? audio.duration : row.duration || 600;
    $("seek").max = limit;
    audio.currentTime = Math.max(0, Math.min(offset, Math.max(0, limit - 0.05)));
    audio.playbackRate = Number($("speed").value);
    if (autoplay) await play(); else playbackState();
    if (version === loadGeneration) updatePosition();
  };
  audio.src = row.audio_url; audio.load(); playbackState();
  neighbor = {filename: row.filename, promise: api("/api/adjacent", {station: row.station, filename: row.filename, direction: "next"}).catch(() => null)};
  if (focus) { $("now-playing").focus({preventScroll: true}); $("player").scrollIntoView({block: "start"}); }
}
function updatePosition() {
  if (!current) return;
  const at = current.timestamp + audio.currentTime;
  $("seek").value = audio.currentTime;
  $("seek").setAttribute("aria-valuetext", `Broadcast time ${shortTime(at)}`);
  $("position").textContent = elapsed(audio.currentTime);
  $("broadcast-time").textContent = shortTime(at);
  if (rangeEnd !== null && at >= rangeEnd) {
    const end = rangeEnd; rangeEnd = null; rangeFinished = true;
    audio.pause(); audio.currentTime = Math.max(0, end - current.timestamp);
    $("player-message").textContent = "You have reached the end of your chosen time range.";
  }
}
async function adjacent(direction, automatic = false) {
  if (!current || moving) return;
  const selected = current, version = loadGeneration;
  moving = true;
  try {
    let data = automatic && neighbor?.filename === selected.filename ? await neighbor.promise : null;
    if (!data) data = await api("/api/adjacent", {station: selected.station, filename: selected.filename, direction});
    if (version !== loadGeneration) return;
    const row = data.recording;
    if (!row) { $("player-message").textContent = "Recording unavailable for this time."; return; }
    if (automatic && rangeEnd !== null && row.timestamp >= rangeEnd) {
      rangeEnd = null; rangeFinished = true; $("player-message").textContent = "You have reached the end of your chosen time range."; return;
    }
    if (automatic && (row.timestamp - selected.end_timestamp > 2 || selected.duration === null || selected.recovered)) {
      $("player-message").textContent = `Recording unavailable for part of this time. The next available broadcast is ${humanDate(row.date)} at ${shortTime(row.timestamp)}. Press Next 10 Minutes to continue.`;
      return;
    }
    if (!automatic) { invalidate(); rangeEnd = null; }
    load(row, 0, false);
  } catch (error) { if (version === loadGeneration) $("player-message").textContent = error.message; }
  finally { if (version === loadGeneration) moving = false; }
}
function statusText(state) {
  if (state.status === "Recording") return "\u25cf Recording normally";
  if (state.age_seconds !== null) return state.age_seconds >= 900 ? "\u26a0 Recording problem - Offline" : "\u26a0 Recording problem";
  return "\u25cf Offline";
}
async function refreshStatus() {
  try {
    const data = await api("/api/status");
    root.dataset.today = data.time.slice(0, 10);
    root.dataset.yesterday = new Date(new Date(data.time.slice(0, 10) + "T12:00:00+03:00").getTime() - 86400000).toLocaleDateString("en-CA", {timeZone: zone});
    $("stations").replaceChildren();
    for (const state of data.stations) {
      const item = node("div", "", `station-status ${state.status}`);
      item.append(node("strong", state.name), node("span", statusText(state)));
      if (state.status !== "Recording") {
        const details = document.createElement("details");
        details.append(node("summary", "View details"), node("p", state.last_successful ? `Last successful recording: ${humanDate(state.last_successful.slice(0, 10))} at ${shortTime(Date.parse(state.last_successful) / 1000)}. Please tell the system administrator.` : "No recent recording has been confirmed. Please tell the system administrator."));
        item.append(details);
      }
      $("stations").append(item);
      $("today-status-" + state.station).textContent = statusText(state);
    }
  } catch (_) {
    $("stations").replaceChildren(node("p", "Recording status is unavailable. Please tell the system administrator.", "error"));
    document.querySelectorAll('[id^="today-status-"]').forEach(item => { item.textContent = "Status unavailable"; });
  }
}
async function showToday(station) {
  invalidate(); clearMessage();
  const version = generation, day = root.dataset.today;
  try {
    const data = await api("/api/recordings", {station, date: day});
    if (version !== generation) return;
    $("timeline").hidden = false; $("timeline-title").textContent = `${names[station]} - ${humanDate(day)}`;
    $("timeline-groups").replaceChildren();
    if (!data.recordings.length) $("timeline-groups").append(node("p", "No recordings are available for today yet. Choose another day above."));
    for (const [label, low, high] of [["Overnight", 0, 6], ["Morning", 6, 12], ["Afternoon", 12, 18], ["Evening", 18, 24]]) {
      const rows = data.recordings.filter(row => Number(row.time.slice(0, 2)) >= low && Number(row.time.slice(0, 2)) < high);
      if (!rows.length) continue;
      const group = document.createElement("details");
      group.open = rows.length <= 6;
      group.append(node("summary", `${label} - ${rows.length} available times`));
      const grid = node("div", "", "time-grid");
      for (const row of rows) {
        const button = node("button", `\u25b6 ${shortTime(row.timestamp)}`);
        button.setAttribute("aria-label", `Play ${names[station]} at ${shortTime(row.timestamp)}`);
        button.addEventListener("click", () => { invalidate(); rangeEnd = null; clearMessage(); load(row, 0, true); });
        grid.append(button);
      }
      group.append(grid); $("timeline-groups").append(group);
    }
    $("timeline-title").focus(); $("timeline").scrollIntoView({block: "start"});
  } catch (error) { if (version === generation) tell(error.message); }
}
$("range-search").addEventListener("submit", async event => {
  event.preventDefault(); invalidate(); const version = generation;
  $("range-results").textContent = "Finding recordings...";
  const values = {station: $("range-station").value, date: $("range-date").value, start: $("range-start").value, end: $("range-end").value};
  try {
    const result = await api("/api/range", values);
    if (version !== generation) return;
    $("range-results").replaceChildren();
    if (!result.recordings.length) { $("range-results").textContent = "Recording unavailable for this time. Try another day or time range."; return; }
    $("range-results").append(node("p", `${names[values.station]} - ${humanDate(values.date)}, ${values.start} to ${values.end}`));
    if (result.gaps.length) $("range-results").append(node("p", "Some audio is missing in this time range. Playback will stop at a missing section."));
    const button = node("button", "\u25b6 PLAY THIS TIME RANGE", "primary");
    button.addEventListener("click", () => {
      invalidate(); clearMessage(); rangeEnd = result.end; $("continuous").checked = true;
      load(result.recordings[0], result.offset, true);
      $("player-message").textContent = `Playing your chosen range. Playback stops at ${values.end}.`;
    });
    $("range-results").append(button);
  } catch (error) { if (version === generation) $("range-results").textContent = error.message; }
});
$("search").addEventListener("submit", findPlay);
for (const id of ["today", "yesterday"]) $(id).addEventListener("click", () => { $("date").value = root.dataset[id]; $("custom-date").hidden = true; formChanged(); });
$("choose-date").addEventListener("click", () => {
  $("custom-date").hidden = false; $("date").focus();
  try { $("date").showPicker(); } catch (_) { /* Native date control remains available. */ }
});
$("search").addEventListener("change", formChanged);
$("another-time").addEventListener("click", () => {
  if (current) setForm({station: current.station, date: current.date, time: shortTime(current.timestamp + audio.currentTime)});
  $("finder").open = true; $("finder").scrollIntoView({block: "start"}); $("hour").focus({preventScroll: true});
});
$("play-pause").addEventListener("click", () => { if (!current) return; if (audio.paused) play(); else audio.pause(); });
$("previous").addEventListener("click", () => adjacent("previous"));
$("next").addEventListener("click", () => adjacent("next"));
async function jump(seconds) {
  if (!current || loading || moving || !Number.isFinite(audio.duration)) return;
  const offset = audio.currentTime + seconds;
  if (offset >= 0 && offset < audio.duration) { audio.currentTime = offset; return; }
  const version = loadGeneration, station = current.station, wasPlaying = !audio.paused;
  const target = Math.floor(current.timestamp + offset);
  const date = new Intl.DateTimeFormat("en-CA", {timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit"}).format(new Date(target * 1000));
  const time = new Intl.DateTimeFormat("en-GB", {timeZone: zone, hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"}).format(new Date(target * 1000));
  moving = true;
  try {
    const result = await api("/api/resolve", {station, date, time});
    if (version === loadGeneration) load(result.recording, result.offset, false, true, wasPlaying);
  } catch (_) {
    if (version === loadGeneration) $("player-message").textContent = "Recording unavailable for this time. Try Previous or Next 10 Minutes.";
  } finally { if (version === loadGeneration) moving = false; }
}
document.querySelectorAll("[data-skip]").forEach(button => button.addEventListener("click", () => jump(Number(button.dataset.skip))));
$("seek").addEventListener("input", () => { if (current && !loading) { audio.currentTime = Number($("seek").value); updatePosition(); } });
$("volume").addEventListener("input", () => { audio.volume = Number($("volume").value); });
$("speed").addEventListener("change", () => { audio.playbackRate = Number($("speed").value); });
for (const event of ["play", "pause"]) audio.addEventListener(event, playbackState);
audio.addEventListener("timeupdate", updatePosition);
audio.addEventListener("waiting", () => { $("play-state").textContent = "Please wait - loading audio..."; });
audio.addEventListener("playing", () => { loading = false; playbackState(); $("player-message").textContent = ""; });
audio.addEventListener("error", () => { loading = false; playbackState(); $("player-message").textContent = "This recording could not be played. Try Find & Play again, or choose another time."; });
audio.addEventListener("ended", () => { if ($("continuous").checked && !rangeFinished) adjacent("next", true); });
document.querySelectorAll("[data-today-station]").forEach(button => button.addEventListener("click", () => showToday(button.dataset.todayStation)));
window.addEventListener("popstate", () => {
  invalidate(); loadGeneration++; audio.pause(); audio.removeAttribute("src"); audio.load(); current = null; rangeEnd = null;
  $("player").hidden = true; $("finder").open = true; clearMessage();
  const match = location.pathname.match(/^\/archive\/([^/]+)\/(\d{4}-\d{2}-\d{2})$/);
  if (match && names[match[1]]) setForm({station: match[1], date: match[2], time: new URLSearchParams(location.search).get("time")});
  else setForm({station: selectedStation(), date: root.dataset.today});
});
let initial = JSON.parse(root.dataset.initial);
if (!initial) {
  let station = "radio-one";
  try { station = localStorage.getItem("radio.lastStation") || station; } catch (_) { /* Optional preference. */ }
  initial = {station, date: root.dataset.today};
}
setForm(initial); refreshStatus(); setInterval(refreshStatus, 30000);
// A bookmark prepares the controls; only an explicit user action starts audio.
