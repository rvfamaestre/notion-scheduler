"""Local browser UI for editing scheduler behavior."""

from __future__ import annotations

import html
import json
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import available_timezones

from . import config as config_module
from .config import AppConfig, load_config


LOOK_AHEAD_OPTIONS: tuple[int, ...] = (1, 2, 3, 5, 7, 10, 14, 21, 30, 45, 60, 90)
TIME_OPTIONS: tuple[str, ...] = tuple(
    f"{hour:02d}:{minute:02d}"
    for hour in range(24)
    for minute in (0, 15, 30, 45)
)
COMMON_TIMEZONES: tuple[str, ...] = (
    "UTC",
    "Europe/Madrid",
    "Europe/Paris",
    "Europe/London",
    "Europe/Berlin",
    "Europe/Rome",
    "Europe/Amsterdam",
    "Europe/Zurich",
    "Europe/Lisbon",
    "Europe/Dublin",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Phoenix",
    "America/Mexico_City",
    "America/Bogota",
    "America/Sao_Paulo",
    "Asia/Dubai",
    "Asia/Kolkata",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Asia/Seoul",
    "Asia/Hong_Kong",
    "Asia/Shanghai",
    "Australia/Sydney",
    "Pacific/Auckland",
)
URGENCY_LABELS: Mapping[str, str] = {
    "overdue": "Overdue",
    "due_today": "Due today",
    "due_tomorrow": "Due tomorrow",
    "due_this_week": "Due this week",
    "future": "Future",
    "no_due_date": "No due date",
}


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Scheduler Studio</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f3ee;
      --panel: rgba(255, 255, 255, 0.88);
      --panel-strong: rgba(255, 255, 255, 0.96);
      --line: rgba(16, 16, 16, 0.13);
      --line-strong: rgba(16, 16, 16, 0.2);
      --text: #161616;
      --muted: rgba(22, 22, 22, 0.62);
      --soft: rgba(22, 22, 22, 0.05);
      --shadow: 0 18px 40px rgba(17, 17, 17, 0.08);
      --radius-xl: 24px;
      --radius-lg: 18px;
      --radius-md: 14px;
      --radius-sm: 10px;
      --sidebar-width: 360px;
      --sidebar-min-width: 320px;
      --sidebar-max-width: 480px;
    }

    * {
      box-sizing: border-box;
    }

    html, body {
      margin: 0;
      min-height: 100%;
      background:
        radial-gradient(circle at top left, rgba(0, 0, 0, 0.035), transparent 32%),
        linear-gradient(180deg, #f8f6f1 0%, var(--bg) 100%);
      color: var(--text);
      font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    }

    body {
      padding: 28px;
    }

    .shell {
      display: grid;
      grid-template-columns: minmax(var(--sidebar-min-width), var(--sidebar-width)) minmax(0, 1fr);
      gap: 24px;
      align-items: start;
    }

    .sidebar,
    .content {
      min-width: 0;
    }

    .sidebar {
      display: flex;
      flex-direction: column;
      gap: 18px;
      position: sticky;
      top: 28px;
      width: min(100%, var(--sidebar-width));
    }

    .card {
      width: 100%;
      min-width: 0;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius-xl);
      box-shadow: var(--shadow);
      padding: 22px;
      overflow: hidden;
      backdrop-filter: blur(16px);
    }

    .hero-card h1 {
      margin: 4px 0 8px;
      font-family: Georgia, "Times New Roman", serif;
      font-size: clamp(2rem, 4vw, 2.8rem);
      line-height: 0.98;
      letter-spacing: -0.03em;
    }

    .eyebrow {
      margin: 0;
      color: var(--muted);
      font-size: 0.78rem;
      letter-spacing: 0.16em;
      text-transform: uppercase;
    }

    .hero-card p:last-child {
      margin: 0;
      color: var(--muted);
      line-height: 1.6;
    }

    .info-stack {
      display: flex;
      flex-direction: column;
      gap: 12px;
      width: 100%;
      min-width: 0;
    }

    .info-box {
      width: 100%;
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: var(--radius-lg);
      background: var(--panel-strong);
      padding: 16px;
    }

    .info-box h2 {
      margin: 0 0 10px;
      font-size: 1.08rem;
      line-height: 1.2;
    }

    .info-grid {
      display: grid;
      grid-template-columns: minmax(92px, 112px) minmax(0, 1fr);
      gap: 10px 12px;
      width: 100%;
      min-width: 0;
      align-items: start;
    }

    .info-grid dt {
      margin: 0;
      color: var(--muted);
      font-size: 0.86rem;
      line-height: 1.45;
    }

    .info-grid dd {
      margin: 0;
      min-width: 0;
      font-size: 0.92rem;
      line-height: 1.45;
      overflow-wrap: anywhere;
      word-break: break-word;
    }

    .mono {
      font-family: "Cascadia Code", "SFMono-Regular", Consolas, monospace;
      font-size: 0.84rem;
    }

    .content {
      display: flex;
      flex-direction: column;
      gap: 18px;
    }

    .topbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      padding: 18px 22px;
      background: var(--panel-strong);
      border: 1px solid var(--line);
      border-radius: var(--radius-xl);
      box-shadow: var(--shadow);
      min-width: 0;
    }

    .topbar-copy {
      min-width: 0;
    }

    .topbar-copy h2 {
      margin: 0 0 6px;
      font-size: 1.22rem;
      line-height: 1.15;
    }

    .topbar-copy p {
      margin: 0;
      color: var(--muted);
      line-height: 1.5;
    }

    .topbar-actions {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }

    .status-pill {
      min-height: 40px;
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      border: 1px solid var(--line);
      padding: 0 14px;
      color: var(--muted);
      background: rgba(255, 255, 255, 0.72);
      white-space: nowrap;
    }

    .status-pill.error {
      color: #761d1d;
      border-color: rgba(118, 29, 29, 0.24);
      background: rgba(118, 29, 29, 0.08);
    }

    .status-pill.success {
      color: #1d4c1d;
      border-color: rgba(29, 76, 29, 0.24);
      background: rgba(29, 76, 29, 0.08);
    }

    button,
    select,
    input,
    textarea {
      font: inherit;
    }

    .button {
      appearance: none;
      border: 1px solid var(--line-strong);
      border-radius: 999px;
      background: #151515;
      color: #fff;
      padding: 0 16px;
      height: 44px;
      cursor: pointer;
      transition: transform 120ms ease, opacity 120ms ease, background 120ms ease;
    }

    .button:hover {
      transform: translateY(-1px);
    }

    .button.secondary {
      background: transparent;
      color: var(--text);
      border-color: var(--line);
    }

    .button.inline {
      height: 36px;
      padding: 0 14px;
      font-size: 0.92rem;
    }

    .section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius-xl);
      box-shadow: var(--shadow);
      overflow: hidden;
    }

    .section-head {
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 14px;
      padding: 22px 22px 0;
    }

    .section-head h3 {
      margin: 0;
      font-size: 1.18rem;
      line-height: 1.15;
    }

    .section-head p {
      margin: 6px 0 0;
      color: var(--muted);
      line-height: 1.5;
    }

    .section-body {
      padding: 20px 22px 22px;
    }

    .field-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }

    .field {
      display: flex;
      flex-direction: column;
      gap: 8px;
      min-width: 0;
    }

    .field.span-2 {
      grid-column: 1 / -1;
    }

    .field label {
      font-weight: 600;
      font-size: 0.93rem;
    }

    .field small {
      color: var(--muted);
      line-height: 1.45;
    }

    .input,
    .select,
    .textarea {
      width: 100%;
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      background: rgba(255, 255, 255, 0.92);
      color: var(--text);
      padding: 12px 14px;
      outline: none;
      transition: border-color 120ms ease, box-shadow 120ms ease;
    }

    .textarea {
      min-height: 102px;
      resize: vertical;
      line-height: 1.5;
    }

    .input:focus,
    .select:focus,
    .textarea:focus {
      border-color: rgba(17, 17, 17, 0.28);
      box-shadow: 0 0 0 4px rgba(17, 17, 17, 0.06);
    }

    .toggle {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      background: rgba(255, 255, 255, 0.88);
    }

    .toggle input {
      width: 18px;
      height: 18px;
      margin: 0;
    }

    .subtle {
      color: var(--muted);
      line-height: 1.45;
    }

    .stack {
      display: flex;
      flex-direction: column;
      gap: 14px;
    }

    .list-stack {
      display: flex;
      flex-direction: column;
      gap: 14px;
      min-width: 0;
    }

    .item-card {
      border: 1px solid var(--line);
      border-radius: var(--radius-lg);
      background: var(--panel-strong);
      padding: 16px;
      min-width: 0;
    }

    .item-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 14px;
      min-width: 0;
    }

    .item-head h4 {
      margin: 0;
      font-size: 1rem;
      line-height: 1.15;
      min-width: 0;
      overflow-wrap: anywhere;
    }

    .item-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      min-width: 0;
    }

    .item-grid .full {
      grid-column: 1 / -1;
    }

    .chip-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .mini-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }

    .row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 120px auto;
      gap: 10px;
      align-items: center;
      min-width: 0;
    }

    .row + .row {
      margin-top: 10px;
    }

    .row.energy-row {
      grid-template-columns: minmax(0, 1fr) 1fr 1fr auto;
    }

    .window-stack {
      display: flex;
      flex-direction: column;
      gap: 10px;
      margin-top: 12px;
    }

    .pill-note {
      display: inline-flex;
      align-items: center;
      padding: 6px 10px;
      border-radius: 999px;
      background: var(--soft);
      color: var(--muted);
      border: 1px solid var(--line);
      font-size: 0.82rem;
    }

    .empty-state {
      border: 1px dashed var(--line-strong);
      border-radius: var(--radius-lg);
      padding: 20px;
      color: var(--muted);
      background: rgba(255, 255, 255, 0.46);
      text-align: center;
    }

    .hidden {
      display: none !important;
    }

    @media (max-width: 1120px) {
      .shell {
        grid-template-columns: 1fr;
      }

      .sidebar {
        position: static;
      }
    }

    @media (max-width: 760px) {
      body {
        padding: 18px;
      }

      .field-grid,
      .item-grid,
      .mini-grid,
      .row,
      .row.energy-row {
        grid-template-columns: 1fr;
      }

      .topbar {
        flex-direction: column;
        align-items: stretch;
      }

      .topbar-actions {
        justify-content: stretch;
      }

      .topbar-actions .button,
      .topbar-actions .status-pill {
        width: 100%;
        justify-content: center;
      }
    }
  </style>
</head>
<body>
  <script id="editor-state" type="application/json">__EDITOR_STATE_JSON__</script>
  <div class="shell" id="shell">
    <aside class="sidebar" id="sidebar">
      <section class="card hero-card">
        <p class="eyebrow">Low-Code Control Room</p>
        <h1>Scheduler Studio</h1>
        <p>Edit how the automation behaves without touching Python. Changes are written back into your config and applied on the next run.</p>
      </section>

      <section class="card">
        <div class="info-stack">
          <div class="info-box">
            <h2>Config surface</h2>
            <dl class="info-grid">
              <dt>Config file</dt>
              <dd class="mono">__CONFIG_FILE__</dd>
              <dt>Local overlay</dt>
              <dd class="mono">__LOCAL_OVERLAY__</dd>
              <dt>Command</dt>
              <dd class="mono">__COMMAND__</dd>
            </dl>
          </div>
          <div class="info-box">
            <h2>What this edits</h2>
            <p class="subtle">Scheduling rules, routine blocks, calendar behavior, duration presets, and scoring maps. Secrets stay out of this editor.</p>
          </div>
          <div class="info-box">
            <h2>Secret strategy</h2>
            <p class="subtle">Use a committed base config plus a git-ignored <span class="mono">*.local.json</span> overlay, then let environment variables override both in CI.</p>
          </div>
        </div>
      </section>
    </aside>

    <main class="content">
      <section class="topbar">
        <div class="topbar-copy">
          <h2>Behavior editor</h2>
          <p>Finite-choice fields use menus. Timezones are searchable. Scoring now has dedicated forms instead of raw JSON.</p>
        </div>
        <div class="topbar-actions">
          <div id="status-pill" class="status-pill">Ready</div>
          <button id="save-button" class="button" type="button">Save changes</button>
        </div>
      </section>

      <section class="section">
        <div class="section-head">
          <div>
            <h3>General scheduling</h3>
            <p>Core planner horizon, timezone, workday limits, and base durations.</p>
          </div>
          <span class="pill-note">Low-code only</span>
        </div>
        <div class="section-body" id="general-section"></div>
      </section>

      <section class="section">
        <div class="section-head">
          <div>
            <h3>Routine blocks</h3>
            <p>Protected recurring blocks such as lunch, dinner, gym, shutdown, or commute.</p>
          </div>
          <button id="add-routine-block" class="button secondary inline" type="button">Add block</button>
        </div>
        <div class="section-body">
          <div id="routine-blocks" class="list-stack"></div>
        </div>
      </section>

      <section class="section">
        <div class="section-head">
          <div>
            <h3>Calendar behavior</h3>
            <p>Choose whether the scheduler writes back to Google Calendar and which calendars count as busy.</p>
          </div>
        </div>
        <div class="section-body" id="calendar-section"></div>
      </section>

      <section class="section">
        <div class="section-head">
          <div>
            <h3>Duration presets</h3>
            <p>Map Notion duration labels to minutes without touching JSON.</p>
          </div>
          <button id="add-duration-row" class="button secondary inline" type="button">Add preset</button>
        </div>
        <div class="section-body">
          <div id="task-durations" class="list-stack"></div>
        </div>
      </section>

      <section class="section">
        <div class="section-head">
          <div>
            <h3>Priority weights</h3>
            <p>Visual controls for how strongly each priority label should influence scheduling.</p>
          </div>
          <button id="add-priority-row" class="button secondary inline" type="button">Add weight</button>
        </div>
        <div class="section-body">
          <div id="priority-weights" class="stack"></div>
        </div>
      </section>

      <section class="section">
        <div class="section-head">
          <div>
            <h3>Urgency rules</h3>
            <p>Adjust how due dates and missing deadlines affect ranking.</p>
          </div>
        </div>
        <div class="section-body" id="urgency-section"></div>
      </section>

      <section class="section">
        <div class="section-head">
          <div>
            <h3>Energy windows</h3>
            <p>Define when deep, moderate, and light work should be preferred.</p>
          </div>
          <button id="add-energy-profile" class="button secondary inline" type="button">Add profile</button>
        </div>
        <div class="section-body">
          <div id="energy-windows" class="list-stack"></div>
        </div>
      </section>
    </main>
  </div>

  <script>
    let state = JSON.parse(document.getElementById("editor-state").textContent);
    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    function buildUiState(source) {
      const routineBlocks = Array.isArray(source.scheduling.routine_blocks)
        ? source.scheduling.routine_blocks.map((block) => ({ ...block }))
        : [];
      const durationRows = Object.entries(source.task_durations || {}).map(([label, minutes]) => ({
        label,
        minutes,
      }));
      const priorityRows = Object.entries(source.scheduling.priority_weights || {}).map(([label, weight]) => ({
        label,
        weight,
      }));
      const energyProfiles = Object.entries(source.scheduling.energy_windows || {}).map(([label, windows]) => ({
        label,
        windows: Array.isArray(windows) ? windows.map((window) => ({ ...window })) : [],
      }));
      return { routineBlocks, durationRows, priorityRows, energyProfiles };
    }

    let ui = buildUiState(state);
    const meta = state.metadata;
    const generalSection = document.getElementById("general-section");
    const routineBlocksRoot = document.getElementById("routine-blocks");
    const calendarSection = document.getElementById("calendar-section");
    const taskDurationsRoot = document.getElementById("task-durations");
    const priorityWeightsRoot = document.getElementById("priority-weights");
    const urgencySection = document.getElementById("urgency-section");
    const energyWindowsRoot = document.getElementById("energy-windows");
    const shellRoot = document.getElementById("shell");
    const sidebarRoot = document.getElementById("sidebar");
    const statusPill = document.getElementById("status-pill");
    const saveButton = document.getElementById("save-button");

    function selectOptions(options, currentValue, placeholder = "") {
      const rows = [];
      if (placeholder) {
        rows.push(`<option value="">${escapeHtml(placeholder)}</option>`);
      }
      for (const option of options) {
        const selected = String(option) === String(currentValue) ? "selected" : "";
        rows.push(`<option value="${escapeHtml(option)}" ${selected}>${escapeHtml(option)}</option>`);
      }
      return rows.join("");
    }

    function timeOptions(currentValue) {
      return selectOptions(meta.time_options, currentValue);
    }

    function hourOptions(currentValue) {
      return selectOptions(meta.hour_options, currentValue);
    }

    function lookAheadOptions(currentValue) {
      return selectOptions(meta.look_ahead_options, currentValue);
    }

    function priorityOptions(currentValue, inputClass) {
      const isCustom = currentValue && !meta.priority_options.includes(currentValue);
      const options = meta.priority_options.map((option) => {
        const selected = option === currentValue ? "selected" : "";
        return `<option value="${escapeHtml(option)}" ${selected}>${escapeHtml(option)}</option>`;
      });
      options.push(`<option value="__custom__" ${isCustom ? "selected" : ""}>Custom label</option>`);
      return `
        <div class="stack">
          <select class="select ${inputClass}-select">
            ${options.join("")}
          </select>
          <input class="input ${inputClass}-custom ${isCustom ? "" : "hidden"}" value="${escapeHtml(isCustom ? currentValue : "")}" placeholder="Custom label" />
        </div>
      `;
    }

    function energyOptions(currentValue, inputClass) {
      const isCustom = currentValue && !meta.energy_options.includes(currentValue);
      const options = meta.energy_options.map((option) => {
        const selected = option === currentValue ? "selected" : "";
        return `<option value="${escapeHtml(option)}" ${selected}>${escapeHtml(option)}</option>`;
      });
      options.push(`<option value="__custom__" ${isCustom ? "selected" : ""}>Custom profile</option>`);
      return `
        <div class="stack">
          <select class="select ${inputClass}-select">
            ${options.join("")}
          </select>
          <input class="input ${inputClass}-custom ${isCustom ? "" : "hidden"}" value="${escapeHtml(isCustom ? currentValue : "")}" placeholder="Custom profile label" />
        </div>
      `;
    }

    function renderGeneralSection() {
      generalSection.innerHTML = `
        <div class="field-grid">
          <div class="field">
            <label for="look-ahead">Look-ahead window</label>
            <select id="look-ahead" class="select">
              ${lookAheadOptions(state.scheduling.look_ahead_days)}
            </select>
            <small>How many days the planner scans ahead when deciding what to schedule.</small>
          </div>
          <div class="field">
            <label for="timezone">Timezone</label>
            <input id="timezone" class="input" list="timezone-options" value="${escapeHtml(state.scheduling.timezone)}" placeholder="Search timezones" />
            <small>Search and pick a valid IANA timezone such as <span class="mono">Europe/Madrid</span>.</small>
          </div>
          <div class="field">
            <label for="work-start">Workday starts</label>
            <select id="work-start" class="select">
              ${hourOptions(state.scheduling.work_hours_start)}
            </select>
          </div>
          <div class="field">
            <label for="work-end">Workday ends</label>
            <select id="work-end" class="select">
              ${hourOptions(state.scheduling.work_hours_end)}
            </select>
          </div>
          <div class="field">
            <label for="default-duration">Default task duration</label>
            <input id="default-duration" class="input" type="number" min="5" step="5" value="${escapeHtml(state.scheduling.default_task_duration_minutes)}" />
          </div>
          <div class="field">
            <label for="buffer-minutes">Buffer minutes</label>
            <input id="buffer-minutes" class="input" type="number" min="0" step="5" value="${escapeHtml(state.scheduling.buffer_minutes)}" />
          </div>
        </div>
        <datalist id="timezone-options">
          ${meta.timezone_options.map((timezone) => `<option value="${escapeHtml(timezone)}"></option>`).join("")}
        </datalist>
      `;
    }

    function renderRoutineBlocks() {
      if (!ui.routineBlocks.length) {
        routineBlocksRoot.innerHTML = `<div class="empty-state">No routine blocks yet. Add meals, gym, commute, reading, or any other fixed ritual.</div>`;
        return;
      }
      routineBlocksRoot.innerHTML = ui.routineBlocks.map((block, index) => `
        <article class="item-card routine-card" data-index="${index}">
          <div class="item-head">
            <h4>${escapeHtml(block.label || `Routine block ${index + 1}`)}</h4>
            <button class="button secondary inline" type="button" data-action="remove-routine-block" data-index="${index}">Remove</button>
          </div>
          <div class="item-grid">
            <div class="field">
              <label>Label</label>
              <input class="input block-label" value="${escapeHtml(block.label || "")}" />
            </div>
            <div class="field">
              <label>ID</label>
              <input class="input block-id" value="${escapeHtml(block.id || "")}" />
            </div>
            <div class="field full">
              <label>Calendar summary</label>
              <input class="input block-summary" value="${escapeHtml(block.summary || "")}" placeholder="Lunch Break (auto)" />
            </div>
            <div class="field">
              <label>Default start</label>
              <select class="select block-default-start">
                ${timeOptions(block.default_start || "12:00")}
              </select>
            </div>
            <div class="field">
              <label>Earliest start</label>
              <select class="select block-earliest">
                ${timeOptions(block.earliest || "11:00")}
              </select>
            </div>
            <div class="field">
              <label>Latest start</label>
              <select class="select block-latest">
                ${timeOptions(block.latest || "14:00")}
              </select>
            </div>
            <div class="field">
              <label>Duration (minutes)</label>
              <input class="input block-duration" type="number" min="5" step="5" value="${escapeHtml(block.duration_minutes || 45)}" />
            </div>
            <div class="field">
              <label>Priority</label>
              <select class="select block-priority">
                ${selectOptions(meta.priority_options, block.priority || "Critical")}
              </select>
            </div>
            <div class="field">
              <label>Enabled</label>
              <label class="toggle"><input class="block-enabled" type="checkbox" ${block.enabled ? "checked" : ""} /><span>Include this block during scheduling</span></label>
            </div>
            <div class="field full">
              <label>Description</label>
              <textarea class="textarea block-description" placeholder="Short note about what this block protects">${escapeHtml(block.description || "")}</textarea>
            </div>
          </div>
        </article>
      `).join("");
    }

    function renderCalendarSection() {
      calendarSection.innerHTML = `
        <div class="field-grid">
          <div class="field span-2">
            <label>Calendar write-back</label>
            <label class="toggle">
              <input id="calendar-writeback" type="checkbox" ${state.google_calendar.create_events ? "checked" : ""} />
              <span>Create or update Google Calendar events for scheduled tasks</span>
            </label>
          </div>
          <div class="field">
            <label for="event-calendar-id">Event calendar ID</label>
            <input id="event-calendar-id" class="input" value="${escapeHtml(state.google_calendar.event_calendar_id || "")}" placeholder="primary" />
          </div>
          <div class="field">
            <label for="busy-calendar-ids">Busy calendar IDs</label>
            <textarea id="busy-calendar-ids" class="textarea" placeholder="primary&#10;team@example.com">${escapeHtml((state.google_calendar.busy_calendar_ids || []).join("\\n"))}</textarea>
          </div>
        </div>
      `;
    }

    function renderTaskDurations() {
      if (!ui.durationRows.length) {
        taskDurationsRoot.innerHTML = `<div class="empty-state">No duration presets configured.</div>`;
        return;
      }
      taskDurationsRoot.innerHTML = ui.durationRows.map((row, index) => `
        <article class="item-card duration-card" data-index="${index}">
          <div class="row">
            <input class="input duration-label" value="${escapeHtml(row.label || "")}" placeholder="Label" />
            <input class="input duration-minutes" type="number" min="5" step="5" value="${escapeHtml(row.minutes || 30)}" />
            <button class="button secondary inline" type="button" data-action="remove-duration-row" data-index="${index}">Remove</button>
          </div>
        </article>
      `).join("");
    }

    function renderPriorityWeights() {
      if (!ui.priorityRows.length) {
        priorityWeightsRoot.innerHTML = `<div class="empty-state">No priority weights configured.</div>`;
        return;
      }
      priorityWeightsRoot.innerHTML = ui.priorityRows.map((row, index) => `
        <article class="item-card priority-card" data-index="${index}">
          <div class="row">
            ${priorityOptions(row.label, "priority-label")}
            <input class="input priority-weight" type="number" step="1" value="${escapeHtml(row.weight || 0)}" />
            <button class="button secondary inline" type="button" data-action="remove-priority-row" data-index="${index}">Remove</button>
          </div>
        </article>
      `).join("");
    }

    function renderUrgencySection() {
      const rows = meta.urgency_rule_order.map((rule) => `
        <div class="field">
          <label for="urgency-${escapeHtml(rule)}">${escapeHtml(meta.urgency_labels[rule] || rule)}</label>
          <input id="urgency-${escapeHtml(rule)}" class="input urgency-input" data-rule="${escapeHtml(rule)}" type="number" step="1" value="${escapeHtml(state.scheduling.urgency_scores?.[rule] ?? 0)}" />
          <small>${escapeHtml(rule)}</small>
        </div>
      `).join("");
      urgencySection.innerHTML = `<div class="field-grid">${rows}</div>`;
    }

    function renderEnergyWindows() {
      if (!ui.energyProfiles.length) {
        energyWindowsRoot.innerHTML = `<div class="empty-state">No energy profiles configured.</div>`;
        return;
      }
      energyWindowsRoot.innerHTML = ui.energyProfiles.map((profile, profileIndex) => `
        <article class="item-card energy-card" data-index="${profileIndex}">
          <div class="item-head">
            <h4>${escapeHtml(profile.label || `Energy profile ${profileIndex + 1}`)}</h4>
            <div class="chip-row">
              <button class="button secondary inline" type="button" data-action="add-energy-window" data-profile-index="${profileIndex}">Add window</button>
              <button class="button secondary inline" type="button" data-action="remove-energy-profile" data-profile-index="${profileIndex}">Remove</button>
            </div>
          </div>
          <div class="stack">
            ${energyOptions(profile.label, "energy-label")}
            <div class="window-stack">
              ${(profile.windows || []).map((window, windowIndex) => `
                <div class="row energy-row" data-window-index="${windowIndex}">
                  <span class="pill-note">Window ${windowIndex + 1}</span>
                  <select class="select energy-start">
                    ${timeOptions(window.start || "08:00")}
                  </select>
                  <select class="select energy-end">
                    ${timeOptions(window.end || "12:00")}
                  </select>
                  <button class="button secondary inline" type="button" data-action="remove-energy-window" data-profile-index="${profileIndex}" data-window-index="${windowIndex}">Remove</button>
                </div>
              `).join("")}
            </div>
          </div>
        </article>
      `).join("");
    }

    function syncRoutineBlocksFromDom() {
      ui.routineBlocks = Array.from(routineBlocksRoot.querySelectorAll(".routine-card")).map((card) => ({
        label: card.querySelector(".block-label").value.trim(),
        id: card.querySelector(".block-id").value.trim(),
        summary: card.querySelector(".block-summary").value.trim(),
        default_start: card.querySelector(".block-default-start").value,
        earliest: card.querySelector(".block-earliest").value,
        latest: card.querySelector(".block-latest").value,
        duration_minutes: Number(card.querySelector(".block-duration").value || 0),
        priority: card.querySelector(".block-priority").value,
        enabled: card.querySelector(".block-enabled").checked,
        description: card.querySelector(".block-description").value.trim(),
      }));
    }

    function syncDurationRowsFromDom() {
      ui.durationRows = Array.from(taskDurationsRoot.querySelectorAll(".duration-card")).map((card) => ({
        label: card.querySelector(".duration-label").value.trim(),
        minutes: Number(card.querySelector(".duration-minutes").value || 0),
      }));
    }

    function syncPriorityRowsFromDom() {
      ui.priorityRows = Array.from(priorityWeightsRoot.querySelectorAll(".priority-card")).map((card) => {
        const select = card.querySelector(".priority-label-select");
        const customInput = card.querySelector(".priority-label-custom");
        const label = select.value === "__custom__" ? customInput.value.trim() : select.value;
        return {
          label,
          weight: Number(card.querySelector(".priority-weight").value || 0),
        };
      });
    }

    function syncEnergyProfilesFromDom() {
      ui.energyProfiles = Array.from(energyWindowsRoot.querySelectorAll(".energy-card")).map((card) => {
        const select = card.querySelector(".energy-label-select");
        const customInput = card.querySelector(".energy-label-custom");
        const label = select.value === "__custom__" ? customInput.value.trim() : select.value;
        const windows = Array.from(card.querySelectorAll(".energy-row")).map((row) => ({
          start: row.querySelector(".energy-start").value,
          end: row.querySelector(".energy-end").value,
        }));
        return { label, windows };
      });
    }

    function syncAllDynamicSections() {
      syncRoutineBlocksFromDom();
      syncDurationRowsFromDom();
      syncPriorityRowsFromDom();
      syncEnergyProfilesFromDom();
    }

    function defaultRoutineBlock() {
      const firstPriority = meta.priority_options[0] || "Critical";
      return {
        id: `routine-${ui.routineBlocks.length + 1}`,
        label: `Routine block ${ui.routineBlocks.length + 1}`,
        summary: "",
        default_start: "12:00",
        earliest: "11:00",
        latest: "14:00",
        duration_minutes: 45,
        priority: firstPriority,
        enabled: true,
        description: "",
      };
    }

    function defaultDurationRow() {
      return {
        label: `Preset ${ui.durationRows.length + 1}`,
        minutes: 60,
      };
    }

    function defaultPriorityRow() {
      const existing = new Set(ui.priorityRows.map((row) => row.label));
      const candidate = meta.priority_options.find((option) => !existing.has(option)) || "__custom__";
      return {
        label: candidate === "__custom__" ? "" : candidate,
        weight: 50,
      };
    }

    function defaultEnergyProfile() {
      const existing = new Set(ui.energyProfiles.map((profile) => profile.label));
      const candidate = meta.energy_options.find((option) => !existing.has(option)) || "";
      return {
        label: candidate,
        windows: [{ start: "08:00", end: "12:00" }],
      };
    }

    function setStatus(message, variant = "") {
      statusPill.textContent = message;
      statusPill.className = `status-pill${variant ? ` ${variant}` : ""}`;
    }

    function availableSidebarHeight() {
      const bodyStyles = getComputedStyle(document.body);
      const topPadding = Number.parseFloat(bodyStyles.paddingTop || "0") || 0;
      const bottomPadding = Number.parseFloat(bodyStyles.paddingBottom || "0") || 0;
      return window.innerHeight - topPadding - bottomPadding;
    }

    function sidebarFitsViewport() {
      return sidebarRoot.getBoundingClientRect().height <= availableSidebarHeight();
    }

    function autoSizeSidebar() {
      if (!shellRoot || !sidebarRoot) {
        return;
      }
      const root = document.documentElement;
      if (window.innerWidth <= 1120) {
        root.style.removeProperty("--sidebar-width");
        return;
      }

      const minWidth = 320;
      const maxWidth = Math.min(520, Math.floor(window.innerWidth * 0.34));
      let low = minWidth;
      let high = Math.max(minWidth, maxWidth);
      let bestWidth = high;

      root.style.setProperty("--sidebar-width", `${high}px`);
      if (!sidebarFitsViewport()) {
        return;
      }

      while (low <= high) {
        const mid = Math.floor((low + high) / 2);
        root.style.setProperty("--sidebar-width", `${mid}px`);
        if (sidebarFitsViewport()) {
          bestWidth = mid;
          high = mid - 8;
        } else {
          low = mid + 8;
        }
      }

      root.style.setProperty("--sidebar-width", `${bestWidth}px`);
    }

    function parseBusyCalendarIds(rawValue) {
      return rawValue
        .split(/[\\n,]/)
        .map((item) => item.trim())
        .filter(Boolean);
    }

    function ensureTimezoneSelection(timezone) {
      if (!meta.timezone_options.includes(timezone)) {
        throw new Error("Choose a timezone from the searchable list.");
      }
    }

    function collectPayload() {
      syncAllDynamicSections();

      const lookAheadDays = Number(document.getElementById("look-ahead").value || 0);
      const workHoursStart = Number(document.getElementById("work-start").value || 0);
      const workHoursEnd = Number(document.getElementById("work-end").value || 0);
      const timezone = document.getElementById("timezone").value.trim();
      const defaultDuration = Number(document.getElementById("default-duration").value || 0);
      const bufferMinutes = Number(document.getElementById("buffer-minutes").value || 0);
      const createEvents = document.getElementById("calendar-writeback").checked;
      const eventCalendarId = document.getElementById("event-calendar-id").value.trim();
      const busyCalendarIds = parseBusyCalendarIds(document.getElementById("busy-calendar-ids").value);

      ensureTimezoneSelection(timezone);

      if (!lookAheadDays || workHoursEnd <= workHoursStart) {
        throw new Error("Pick a valid look-ahead window and ensure the workday ends after it starts.");
      }
      if (defaultDuration <= 0 || bufferMinutes < 0) {
        throw new Error("Default duration must be positive and buffer minutes cannot be negative.");
      }

      const priorityWeights = {};
      for (const row of ui.priorityRows) {
        if (!row.label) {
          continue;
        }
        priorityWeights[row.label] = Number(row.weight || 0);
      }

      const urgencyScores = {};
      for (const rule of meta.urgency_rule_order) {
        urgencyScores[rule] = Number(document.querySelector(`#urgency-${CSS.escape(rule)}`).value || 0);
      }

      const energyWindows = {};
      for (const profile of ui.energyProfiles) {
        if (!profile.label) {
          continue;
        }
        energyWindows[profile.label] = (profile.windows || [])
          .filter((window) => window.start && window.end)
          .map((window) => ({
            start: window.start,
            end: window.end,
          }));
      }

      const taskDurations = {};
      for (const row of ui.durationRows) {
        if (!row.label) {
          continue;
        }
        if (Number(row.minutes || 0) <= 0) {
          throw new Error("Every duration preset must use a positive number of minutes.");
        }
        taskDurations[row.label] = Number(row.minutes || 0);
      }

      if (!Object.keys(taskDurations).length) {
        throw new Error("Keep at least one duration preset.");
      }

      const routineBlocks = ui.routineBlocks
        .filter((block) => block.label || block.id)
        .map((block, index) => ({
          id: block.id || `routine-${index + 1}`,
          label: block.label || `Routine block ${index + 1}`,
          summary: block.summary,
          default_start: block.default_start,
          earliest: block.earliest,
          latest: block.latest,
          duration_minutes: Number(block.duration_minutes || 0),
          priority: block.priority || (meta.priority_options[0] || "Critical"),
          enabled: Boolean(block.enabled),
          description: block.description,
        }));

      for (const block of routineBlocks) {
        if (block.duration_minutes <= 0) {
          throw new Error(`Routine block "${block.label}" must use a positive duration.`);
        }
      }

      return {
        scheduling: {
          look_ahead_days: lookAheadDays,
          work_hours_start: workHoursStart,
          work_hours_end: workHoursEnd,
          default_task_duration_minutes: defaultDuration,
          buffer_minutes: bufferMinutes,
          timezone,
          routine_blocks: routineBlocks,
          priority_weights: priorityWeights,
          urgency_scores: urgencyScores,
          energy_windows: energyWindows,
        },
        google_calendar: {
          create_events: createEvents,
          event_calendar_id: eventCalendarId,
          busy_calendar_ids: busyCalendarIds,
        },
        task_durations: taskDurations,
      };
    }

    async function saveConfig() {
      try {
        setStatus("Saving…");
        saveButton.disabled = true;
        const payload = collectPayload();
        const response = await fetch("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const result = await response.json();
        if (!response.ok) {
          throw new Error(result.error || "Unable to save config.");
        }
        state = result.state;
        ui = buildUiState(state);
        renderAll();
        setStatus("Saved", "success");
      } catch (error) {
        setStatus(error.message || "Unable to save config.", "error");
      } finally {
        saveButton.disabled = false;
      }
    }

    function renderAll() {
      renderGeneralSection();
      renderRoutineBlocks();
      renderCalendarSection();
      renderTaskDurations();
      renderPriorityWeights();
      renderUrgencySection();
      renderEnergyWindows();
      attachCustomVisibility();
      requestAnimationFrame(autoSizeSidebar);
    }

    function attachCustomVisibility() {
      document.querySelectorAll(".priority-label-select").forEach((select) => {
        const custom = select.closest(".stack").querySelector(".priority-label-custom");
        custom.classList.toggle("hidden", select.value !== "__custom__");
      });
      document.querySelectorAll(".energy-label-select").forEach((select) => {
        const custom = select.closest(".stack").querySelector(".energy-label-custom");
        custom.classList.toggle("hidden", select.value !== "__custom__");
      });
    }

    document.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) {
        return;
      }
      const action = target.dataset.action;

      if (target.id === "add-routine-block") {
        syncRoutineBlocksFromDom();
        ui.routineBlocks.push(defaultRoutineBlock());
        renderRoutineBlocks();
        return;
      }
      if (target.id === "add-duration-row") {
        syncDurationRowsFromDom();
        ui.durationRows.push(defaultDurationRow());
        renderTaskDurations();
        return;
      }
      if (target.id === "add-priority-row") {
        syncPriorityRowsFromDom();
        ui.priorityRows.push(defaultPriorityRow());
        renderPriorityWeights();
        attachCustomVisibility();
        return;
      }
      if (target.id === "add-energy-profile") {
        syncEnergyProfilesFromDom();
        ui.energyProfiles.push(defaultEnergyProfile());
        renderEnergyWindows();
        attachCustomVisibility();
        return;
      }

      if (action === "remove-routine-block") {
        syncRoutineBlocksFromDom();
        ui.routineBlocks.splice(Number(target.dataset.index), 1);
        renderRoutineBlocks();
        return;
      }
      if (action === "remove-duration-row") {
        syncDurationRowsFromDom();
        ui.durationRows.splice(Number(target.dataset.index), 1);
        renderTaskDurations();
        return;
      }
      if (action === "remove-priority-row") {
        syncPriorityRowsFromDom();
        ui.priorityRows.splice(Number(target.dataset.index), 1);
        renderPriorityWeights();
        attachCustomVisibility();
        return;
      }
      if (action === "remove-energy-profile") {
        syncEnergyProfilesFromDom();
        ui.energyProfiles.splice(Number(target.dataset.profileIndex), 1);
        renderEnergyWindows();
        attachCustomVisibility();
        return;
      }
      if (action === "add-energy-window") {
        syncEnergyProfilesFromDom();
        const profile = ui.energyProfiles[Number(target.dataset.profileIndex)];
        if (profile) {
          profile.windows.push({ start: "08:00", end: "12:00" });
        }
        renderEnergyWindows();
        attachCustomVisibility();
        return;
      }
      if (action === "remove-energy-window") {
        syncEnergyProfilesFromDom();
        const profile = ui.energyProfiles[Number(target.dataset.profileIndex)];
        if (profile) {
          profile.windows.splice(Number(target.dataset.windowIndex), 1);
        }
        renderEnergyWindows();
        attachCustomVisibility();
      }
    });

    document.addEventListener("change", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) {
        return;
      }
      if (target.classList.contains("priority-label-select") || target.classList.contains("energy-label-select")) {
        attachCustomVisibility();
      }
    });

    saveButton.addEventListener("click", saveConfig);
    window.addEventListener("resize", autoSizeSidebar);
    renderAll();
  </script>
</body>
</html>
"""


def _ordered_unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _timezone_options(current_timezone: str) -> list[str]:
    available = sorted(available_timezones())
    return _ordered_unique([current_timezone, *COMMON_TIMEZONES, *available])


def _priority_options(config: AppConfig) -> list[str]:
    labels = list(config_module.DEFAULT_PRIORITY_WEIGHTS.keys())
    labels.extend(config.scheduling.priority_weights.keys())
    labels.extend(block.priority for block in (config.scheduling.routine_blocks or []))
    return _ordered_unique(labels)


def _energy_options(config: AppConfig) -> list[str]:
    labels = list(config_module.DEFAULT_ENERGY_WINDOWS.keys())
    labels.extend(config.scheduling.energy_windows.keys())
    return _ordered_unique(labels)


def _clock_to_str(raw_time: Any) -> str:
    if hasattr(raw_time, "strftime"):
        return raw_time.strftime("%H:%M")
    return str(raw_time)


def _minutes_to_clock(minutes: int) -> str:
    minutes = max(0, min(24 * 60, int(minutes)))
    hours, remainder = divmod(minutes, 60)
    hours = min(hours, 23)
    return f"{hours:02d}:{remainder:02d}"


def _clock_to_hour_value(raw_value: Any) -> float:
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    value = str(raw_value).strip()
    if not value:
        raise ValueError("Time value cannot be empty")
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time: {raw_value!r}")
    hour = int(parts[0])
    minute = int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time: {raw_value!r}")
    return hour + (minute / 60)


def _serialize_routine_blocks(raw_blocks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for index, raw_block in enumerate(raw_blocks):
        label = str(raw_block.get("label") or "").strip()
        block_id = str(raw_block.get("id") or "").strip() or f"routine-{index + 1}"
        if not label:
            label = block_id.replace("-", " ").title()
        block: dict[str, Any] = {
            "id": block_id,
            "label": label,
            "default_start": str(raw_block.get("default_start") or "12:00"),
            "earliest": str(raw_block.get("earliest") or "11:00"),
            "latest": str(raw_block.get("latest") or "14:00"),
            "duration_minutes": int(raw_block.get("duration_minutes") or 45),
            "enabled": bool(raw_block.get("enabled", True)),
            "priority": str(raw_block.get("priority") or "Critical").strip() or "Critical",
        }
        summary = str(raw_block.get("summary") or "").strip()
        description = str(raw_block.get("description") or "").strip()
        if summary:
            block["summary"] = summary
        if description:
            block["description"] = description
        blocks.append(block)
    return blocks


def _serialize_numeric_map(raw_value: Any) -> dict[str, int]:
    if not isinstance(raw_value, Mapping):
        return {}
    result: dict[str, int] = {}
    for key, value in raw_value.items():
        label = str(key).strip()
        if not label:
            continue
        result[label] = int(value)
    return result


def _serialize_energy_windows(raw_value: Any) -> dict[str, list[list[float]]]:
    if not isinstance(raw_value, Mapping):
        return {}
    normalized: dict[str, list[list[float]]] = {}
    for label, windows in raw_value.items():
        profile = str(label).strip()
        if not profile:
            continue
        rows: list[list[float]] = []
        for window in windows or []:
            if isinstance(window, Mapping):
                start = window.get("start")
                end = window.get("end")
            elif isinstance(window, (list, tuple)) and len(window) == 2:
                start, end = window
            else:
                continue
            rows.append([_clock_to_hour_value(start), _clock_to_hour_value(end)])
        if rows:
            normalized[profile] = rows
    return normalized


def _split_lines(raw_value: str) -> list[str]:
    return [item for item in (part.strip() for part in raw_value.replace(",", "\n").splitlines()) if item]


def _validate_config_payload(payload: Mapping[str, Any]) -> None:
    scheduling = payload.get("scheduling")
    calendar = payload.get("google_calendar")
    durations = payload.get("task_durations")
    if not isinstance(scheduling, Mapping):
        raise ValueError("Missing scheduling section")
    if not isinstance(calendar, Mapping):
        raise ValueError("Missing google_calendar section")
    if not isinstance(durations, Mapping) or not durations:
        raise ValueError("Keep at least one task duration preset")
    if int(scheduling.get("look_ahead_days", 0)) <= 0:
        raise ValueError("Look-ahead days must be positive")
    if int(scheduling.get("default_task_duration_minutes", 0)) <= 0:
        raise ValueError("Default task duration must be positive")
    if int(scheduling.get("work_hours_end", 0)) <= int(scheduling.get("work_hours_start", 0)):
        raise ValueError("Workday end must be after workday start")
    for block in scheduling.get("routine_blocks", []):
        if int(block.get("duration_minutes", 0)) <= 0:
            raise ValueError(f"Routine block '{block.get('label') or block.get('id')}' must use a positive duration")
    for label, minutes in durations.items():
        if not str(label).strip() or int(minutes) <= 0:
            raise ValueError("Task durations must have non-empty labels and positive minutes")
    busy_calendar_ids = calendar.get("busy_calendar_ids", [])
    if not isinstance(busy_calendar_ids, list):
        raise ValueError("Busy calendar IDs must be a list")


def _build_editor_state(initial: AppConfig, config_path: Path | str) -> dict[str, Any]:
    resolved_path = Path(config_path).resolve()
    local_overlay_path = config_module._local_overlay_path(resolved_path).resolve()
    look_ahead_options = _ordered_unique(
        [str(initial.scheduling.look_ahead_days), *(str(option) for option in LOOK_AHEAD_OPTIONS)]
    )
    return {
        "config_file": str(resolved_path),
        "local_overlay_file": str(local_overlay_path),
        "command": f"python main.py config-ui --config {resolved_path} --open-browser",
        "scheduling": {
            "work_hours_start": initial.scheduling.work_hours_start,
            "work_hours_end": initial.scheduling.work_hours_end,
            "default_task_duration_minutes": initial.scheduling.default_task_duration_minutes,
            "buffer_minutes": initial.scheduling.buffer_minutes,
            "timezone": initial.scheduling.timezone,
            "look_ahead_days": initial.scheduling.look_ahead_days,
            "routine_blocks": [
                {
                    "id": block.id,
                    "label": block.label,
                    "summary": block.summary or "",
                    "default_start": _clock_to_str(block.default_start),
                    "earliest": _clock_to_str(block.earliest),
                    "latest": _clock_to_str(block.latest),
                    "duration_minutes": block.duration_minutes,
                    "enabled": block.enabled,
                    "priority": block.priority,
                    "description": block.description or "",
                }
                for block in (initial.scheduling.routine_blocks or [])
            ],
            "priority_weights": dict(initial.scheduling.priority_weights),
            "urgency_scores": dict(initial.scheduling.urgency_scores),
            "energy_windows": {
                label: [
                    {"start": _minutes_to_clock(start), "end": _minutes_to_clock(end)}
                    for start, end in intervals
                ]
                for label, intervals in initial.scheduling.energy_windows.items()
            },
        },
        "google_calendar": {
            "busy_calendar_ids": list(initial.google_calendar.busy_calendar_ids),
            "create_events": initial.google_calendar.create_events,
            "event_calendar_id": initial.google_calendar.event_calendar_id or "",
        },
        "task_durations": dict(initial.task_durations.durations),
        "metadata": {
            "look_ahead_options": [int(option) for option in look_ahead_options],
            "hour_options": list(range(24)),
            "time_options": list(TIME_OPTIONS),
            "priority_options": _priority_options(initial),
            "timezone_options": _timezone_options(initial.scheduling.timezone),
            "urgency_rule_order": list(config_module.DEFAULT_URGENCY_RULE_ORDER),
            "urgency_labels": dict(URGENCY_LABELS),
            "energy_options": _energy_options(initial),
        },
    }


def _apply_editor_state(config_path: Path | str, editor_state: Mapping[str, Any]) -> None:
    path = Path(config_path)
    original_text = path.read_text(encoding="utf-8")
    raw = json.loads(original_text)

    scheduling_state = editor_state.get("scheduling", {})
    calendar_state = editor_state.get("google_calendar", {})
    task_durations_state = editor_state.get("task_durations", {})

    scheduling = raw.setdefault("scheduling", {})
    scheduling["work_hours_start"] = int(scheduling_state.get("work_hours_start", scheduling.get("work_hours_start", 8)))
    scheduling["work_hours_end"] = int(scheduling_state.get("work_hours_end", scheduling.get("work_hours_end", 18)))
    scheduling["default_task_duration_minutes"] = int(
        scheduling_state.get("default_task_duration_minutes", scheduling.get("default_task_duration_minutes", 60))
    )
    scheduling["buffer_minutes"] = int(scheduling_state.get("buffer_minutes", scheduling.get("buffer_minutes", 10)))
    scheduling["timezone"] = str(scheduling_state.get("timezone") or scheduling.get("timezone") or "UTC").strip()
    scheduling["look_ahead_days"] = int(scheduling_state.get("look_ahead_days", scheduling.get("look_ahead_days", 7)))
    scheduling["routine_blocks"] = _serialize_routine_blocks(scheduling_state.get("routine_blocks", []))
    scheduling["priority_weights"] = _serialize_numeric_map(scheduling_state.get("priority_weights", {}))
    scheduling["urgency_scores"] = _serialize_numeric_map(scheduling_state.get("urgency_scores", {}))
    scheduling["energy_windows"] = _serialize_energy_windows(scheduling_state.get("energy_windows", {}))

    calendar = raw.setdefault("google_calendar", {})
    busy_calendar_ids = calendar_state.get("busy_calendar_ids", calendar.get("busy_calendar_ids", []))
    if isinstance(busy_calendar_ids, str):
        busy_calendar_ids = _split_lines(busy_calendar_ids)
    calendar["busy_calendar_ids"] = [str(item).strip() for item in busy_calendar_ids if str(item).strip()]
    calendar["create_events"] = bool(calendar_state.get("create_events", calendar.get("create_events", False)))
    event_calendar_id = str(calendar_state.get("event_calendar_id") or "").strip()
    if event_calendar_id:
        calendar["event_calendar_id"] = event_calendar_id
    else:
        calendar.pop("event_calendar_id", None)

    raw["task_durations"] = _serialize_numeric_map(task_durations_state)
    _validate_config_payload(raw)

    backup_path = path.with_suffix(path.suffix + ".bak")
    backup_path.write_text(original_text, encoding="utf-8")
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        load_config(path)
    except Exception:
        path.write_text(original_text, encoding="utf-8")
        raise


def _json_for_html(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def _render_html(config_path: Path) -> str:
    editor_state = _build_editor_state(load_config(config_path), config_path)
    page = HTML_TEMPLATE.replace("__EDITOR_STATE_JSON__", _json_for_html(editor_state))
    page = page.replace("__CONFIG_FILE__", html.escape(editor_state["config_file"]))
    page = page.replace("__LOCAL_OVERLAY__", html.escape(editor_state["local_overlay_file"]))
    page = page.replace("__COMMAND__", html.escape(editor_state["command"]))
    return page


def launch_config_ui(
    config_path: Path | str,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
) -> None:
    """Launch the local browser UI for editing scheduler behavior."""

    path = Path(config_path).resolve()
    load_config(path)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            payload = _render_html(path).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/config":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length)
                payload = json.loads(raw_body.decode("utf-8"))
                _apply_editor_state(path, payload)
                fresh_state = _build_editor_state(load_config(path), path)
                self._send_json(HTTPStatus.OK, {"ok": True, "state": fresh_state})
            except Exception as exc:  # pragma: no cover - defensive server boundary
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def _send_json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"Scheduler Studio running at {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
