"use strict";
const $ = id => document.getElementById(id);
const zone = "Africa/Dar_es_Salaam";
const bytes = n => n == null ? "Not available" : `${(n / 1024 ** 3).toFixed(1)} GB`;
function el(tag, text, className) { const element = document.createElement(tag); element.textContent = text; if (className) element.className = className; return element; }
async function api(path, params) {
  const response = await fetch(path + (params ? "?" + new URLSearchParams(params) : ""), {cache: "no-store"});
  if (response.status === 401) { location.assign("/login"); throw new Error("Please sign in again."); }
  const data = await response.json(); if (!response.ok) throw new Error(data.error || "Diagnostics unavailable."); return data;
}
function line(parent, label, value) { const p = el("p", ""); p.append(el("strong", label + ": "), document.createTextNode(String(value))); parent.append(p); }
async function refresh() {
  try {
    const data = await api("/api/admin/status");
    $("admin-message").textContent = "Updated " + new Intl.DateTimeFormat("en-GB", {timeZone: zone, hour: "2-digit", minute: "2-digit"}).format(new Date(data.time));
    $("admin-stations").replaceChildren(); $("admin-counts").replaceChildren();
    for (const station of data.stations) {
      const card = el("article", "", "panel"); card.append(el("h2", station.name));
      line(card, "Status", station.status);
      line(card, "Last successful recording", station.last_successful ? new Intl.DateTimeFormat("en-GB", {timeZone: zone, dateStyle: "long", timeStyle: "short"}).format(new Date(station.last_successful)) : "Not available");
      line(card, "Last file size", station.last_file_size == null ? "Not available" : `${(station.last_file_size / 1024 ** 2).toFixed(1)} MB`);
      line(card, "Latest file", station.latest_file || "Not available");
      if (station.error) line(card, "Diagnostic", station.error);
      $("admin-stations").append(card);
      const count = el("article", "", "count-row"); count.append(el("h3", station.name));
      const s = station.today;
      line(count, "Expected / recorded / missing", `${s.expected} / ${s.recorded} / ${s.missing.length}`);
      for (const [key, label] of [["missing", "Missing"], ["partial", "Partial"], ["unverified", "Unverified"]]) {
        if (s[key].length) { const details = document.createElement("details"); details.append(el("summary", `${label}: ${s[key].length} intervals`), el("p", s[key].join(", "))); count.append(details); }
      }
      $("admin-counts").append(count);
    }
    const storage = data.storage; $("admin-storage").replaceChildren();
    if (storage.error) $("admin-storage").append(el("p", storage.error, "error"));
    else {
      line($("admin-storage"), "Used / free / total", `${bytes(storage.used)} / ${bytes(storage.free)} / ${bytes(storage.total)}`);
      line($("admin-storage"), "Usage", `${storage.percent}% - ${storage.status}`);
      const meter = document.createElement("meter"); meter.min = 0; meter.max = 100; meter.low = 80; meter.high = 90; meter.optimum = 0; meter.value = storage.percent; meter.setAttribute("aria-label", "Storage used"); $("admin-storage").append(meter);
    }
    $("admin-system").replaceChildren();
    line($("admin-system"), "Segment", `${data.system.segment_minutes} minutes`);
    line($("admin-system"), "Timezone", data.system.timezone);
    line($("admin-system"), "Retention", `${data.system.retention_days} days`);
    line($("admin-system"), "Recording access", data.system.recordings_access);
    for (const [station, url] of Object.entries(data.system.stream_urls)) line($("admin-system"), station, url);
  } catch (error) { $("admin-message").textContent = error.message; }
}
$("gap-search").addEventListener("submit", async event => {
  event.preventDefault(); $("gap-results").textContent = "Checking recordings...";
  try {
    const data = await api("/api/recordings", {station: $("gap-station").value, date: $("gap-date").value});
    $("gap-results").replaceChildren();
    for (const [key, label] of [["missing", "Missing"], ["partial", "Partial"], ["unverified", "Unverified"]]) line($("gap-results"), label, data.summary[key].join(", ") || "None");
    line($("gap-results"), "File count", data.recordings.length);
  } catch (error) { $("gap-results").textContent = error.message; }
});
refresh(); setInterval(refresh, 30000);
