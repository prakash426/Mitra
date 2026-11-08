"use strict";

const $ = (sel) => document.querySelector(sel);
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function stored(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (err) {
    return null;
  }
}

function store(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (err) {
    /* storage unavailable */
  }
}

function storedFloat(key, fallback) {
  const raw = stored(key);
  const num = raw === null ? NaN : parseFloat(raw);
  if (Number.isNaN(num) || num < 0.5 || num > 2) {
    return fallback;
  }
  return num;
}

const state = {
  project: stored("mitra-project") || "default",
  voiceOn: true,
  soundOn: true,
  energy: 0.25,
  energyTarget: 0.25,
  pendingSeen: null,
  voices: [],
  rate: storedFloat("mitra-rate", 0.98),
  pitch: storedFloat("mitra-pitch", 1.0),
  persona: stored("mitra-persona") || "auto",
  personaPresets: {},
  activeConnector: stored("mitra-connector") || "",
  autostart: false,
};

function saveProject() {
  store("mitra-project", state.project);
}

let audioCtx = null;

function blip(f0, f1, ms) {
  if (!state.soundOn) {
    return;
  }
  try {
    if (!audioCtx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      audioCtx = new AC();
    }
    if (audioCtx.state === "suspended") {
      audioCtx.resume();
    }
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    const now = audioCtx.currentTime;
    osc.type = "sine";
    osc.frequency.setValueAtTime(f0, now);
    osc.frequency.exponentialRampToValueAtTime(Math.max(f1, 1), now + ms / 1000);
    gain.gain.setValueAtTime(0.06, now);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + ms / 1000);
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start(now);
    osc.stop(now + ms / 1000 + 0.02);
  } catch (err) {
    /* audio unavailable */
  }
}

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (err) {
      /* keep status text */
    }
    throw new Error(detail);
  }
  return res.json();
}

function post(path, data) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

const transcriptEl = $("#transcript");
const inputEl = $("#input");

function scrollDown() {
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

function addMsg(who, text) {
  const wrap = document.createElement("div");
  wrap.className = "msg " + who;
  if (who !== "sys") {
    const tag = document.createElement("span");
    tag.className = "who";
    tag.textContent = who === "user" ? "YOU" : "MITRA";
    wrap.appendChild(tag);
  }
  const body = document.createElement("span");
  body.textContent = text;
  wrap.appendChild(body);
  transcriptEl.appendChild(wrap);
  scrollDown();
  return body;
}

function typeInto(el, text) {
  if (reducedMotion || text.length < 3) {
    el.textContent = text;
    scrollDown();
    return;
  }
  let pos = 0;
  const step = Math.max(1, Math.floor(text.length / 220));
  const timer = window.setInterval(() => {
    pos += step;
    el.textContent = text.slice(0, pos);
    scrollDown();
    if (pos >= text.length) {
      window.clearInterval(timer);
      el.textContent = text;
      scrollDown();
    }
  }, 12);
}

function personaWants() {
  const preset = state.personaPresets[state.persona];
  if (!preset || !preset.voices) {
    return [];
  }
  return preset.voices.map((n) => String(n).toLowerCase());
}

function voiceScore(v) {
  const name = (v.name || "").toLowerCase();
  const lang = (v.lang || "").toLowerCase();
  let score = 0;
  const wants = personaWants();
  const wantPos = wants.indexOf(name);
  if (wantPos >= 0) {
    score += 200 - wantPos * 20;
  }
  if (state.persona === "jarvis" && (lang === "en-gb" || name.indexOf("daniel") >= 0)) {
    score += 25;
  }
  if (state.persona === "friday" && (lang === "en-ie" || name.indexOf("moira") >= 0)) {
    score += 25;
  }
  if (name.indexOf("siri") >= 0) {
    score += 100;
  }
  if (name.indexOf("premium") >= 0) {
    score += 80;
  }
  if (name.indexOf("enhanced") >= 0 || name.indexOf("natural") >= 0) {
    score += 60;
  }
  if (name.indexOf("neural") >= 0) {
    score += 70;
  }
  if (name.indexOf("google us english") >= 0) {
    score += 40;
  }
  if (name.indexOf("compact") >= 0) {
    score -= 40;
  }
  if (lang === "en-us" || lang === "en-gb") {
    score += 10;
  }
  if (v.default) {
    score += 5;
  }
  return score;
}

function loadVoices() {
  if (!("speechSynthesis" in window)) {
    return;
  }
  const all = window.speechSynthesis.getVoices();
  if (!all.length) {
    return;
  }
  const english = all.filter((v) => v.lang && v.lang.toLowerCase().startsWith("en"));
  state.voices = (english.length ? english : all).slice().sort((a, b) => {
    return voiceScore(b) - voiceScore(a);
  });
  const select = $("#voice-select");
  const current = select.value || stored("mitra-voice") || "";
  select.textContent = "";
  state.voices.forEach((v) => {
    const opt = document.createElement("option");
    opt.value = v.voiceURI;
    opt.textContent = v.name + " (" + v.lang + ")";
    select.appendChild(opt);
  });
  const kept = state.voices.find((v) => v.voiceURI === current);
  select.value = (kept || state.voices[0]).voiceURI;
}

function shapeForSpeech(text) {
  let out = text
    .replace(/https?:\S+/g, "link")
    .replace(/[`*_#>|]/g, "")
    .replace(/\n+/g, ". ")
    .replace(/\s+/g, " ")
    .trim();
  out = out.replace(/\bI will\b/g, "I'll").replace(/\bI have\b/g, "I've");
  out = out.replace(/\bdo not\b/g, "don't").replace(/\bcannot\b/g, "can't");
  out = out.replace(/\bType 'help' to see examples\./g, "Say help any time for ideas.");
  out = out.replace(/([.!?])\s+([A-Z])/g, "$1 ... $2");
  return out.slice(0, 1500);
}

async function fetchPersonas() {
  try {
    const items = await api("/voices");
    const presets = {};
    items.forEach((item) => {
      presets[item.id] = item;
    });
    state.personaPresets = presets;
    const select = $("#persona-select");
    if (select && presets[state.persona]) {
      select.value = state.persona;
    }
    applyPersona(false);
  } catch (err) {
    /* keep local defaults */
  }
}

function applyPersona(announce) {
  const preset = state.personaPresets[state.persona];
  const caption = $("#persona-line");
  if (preset && caption) {
    caption.textContent = preset.line || "Systems nominal.";
  }
  if (preset && preset.id !== "auto") {
    const pace = preset.id === "jarvis" ? 0.94 : 1.02;
    const tone = preset.pitch || 1.0;
    state.rate = pace;
    state.pitch = tone;
    const rateEl = $("#rate");
    const pitchEl = $("#pitch");
    if (rateEl) {
      rateEl.value = String(pace);
    }
    if (pitchEl) {
      pitchEl.value = String(tone);
    }
    store("mitra-rate", String(pace));
    store("mitra-pitch", String(tone));
    loadVoices();
  } else if (preset && caption) {
    loadVoices();
  }
  if (announce && preset) {
    addMsg("sys", "Persona set to " + preset.label + ".");
  }
}

function stopSpeak() {
  try {
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
  } catch (err) {
    /* speech unavailable */
  }
  state.energyTarget = 0.25;
}

function speak(text) {
  if (!state.voiceOn || !("speechSynthesis" in window)) {
    return;
  }
  try {
    const clean = shapeForSpeech(text);
    if (!clean) {
      return;
    }
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(clean);
    const picked = state.voices.find((v) => v.voiceURI === $("#voice-select").value);
    if (picked) {
      utter.voice = picked;
    }
    utter.rate = state.rate;
    utter.pitch = state.pitch;
    utter.onstart = () => {
      state.energyTarget = 1.0;
    };
    utter.onend = () => {
      state.energyTarget = 0.25;
    };
    utter.onerror = () => {
      state.energyTarget = 0.25;
    };
    window.speechSynthesis.speak(utter);
  } catch (err) {
    /* speech unavailable */
  }
}

function addMitra(text, withVoice) {
  const el = addMsg("mitra", "");
  typeInto(el, text);
  blip(660, 880, 130);
  if (withVoice !== false) {
    speak(text);
  }
}

function approvalCard(data) {
  const card = document.createElement("div");
  card.className = "approval-card";
  const head = document.createElement("div");
  head.className = "ahead";
  const left = document.createElement("span");
  left.textContent = data.action + " " + data.proposal_id;
  const right = document.createElement("span");
  right.textContent = "AWAITING DECISION";
  head.appendChild(left);
  head.appendChild(right);
  const target = document.createElement("div");
  target.className = "atarget";
  target.textContent = data.target;
  const preview = document.createElement("pre");
  preview.textContent = data.preview || data.details || "";
  const btns = document.createElement("div");
  btns.className = "abtns";
  const okBtn = document.createElement("button");
  okBtn.type = "button";
  okBtn.className = "ok";
  okBtn.textContent = "APPROVE";
  const noBtn = document.createElement("button");
  noBtn.type = "button";
  noBtn.className = "danger";
  noBtn.textContent = "DENY";
  okBtn.addEventListener("click", () => decide(data.proposal_id, true, card));
  noBtn.addEventListener("click", () => decide(data.proposal_id, false, card));
  btns.appendChild(okBtn);
  btns.appendChild(noBtn);
  card.appendChild(head);
  card.appendChild(target);
  card.appendChild(preview);
  card.appendChild(btns);
  return card;
}

async function decide(proposalId, approve, card) {
  try {
    const body = await post("/approvals/" + proposalId + "/decision", {
      project: state.project,
      approve: approve,
    });
    if (card && card.parentNode) {
      card.parentNode.removeChild(card);
    }
    addMsg("sys", body.message);
    blip(approve ? 520 : 220, approve ? 780 : 180, 140);
    refreshAll();
  } catch (err) {
    addMsg("sys", "Decision failed: " + err.message);
  }
}

async function send() {
  const text = inputEl.value.trim();
  if (!text) {
    return;
  }
  inputEl.value = "";
  stopSpeak();
  addMsg("user", text);
  blip(880, 990, 70);
  state.energyTarget = 0.65;
  try {
    const body = await post("/chat", { project: state.project, message: text });
    addMitra(body.reply);
    if (body.approval) {
      const holder = document.createElement("div");
      holder.className = "msg mitra";
      const tag = document.createElement("span");
      tag.className = "who";
      tag.textContent = "APPROVAL";
      holder.appendChild(tag);
      holder.appendChild(approvalCard(body.approval));
      transcriptEl.appendChild(holder);
      scrollDown();
    }
    refreshAll();
  } catch (err) {
    addMsg("sys", "Core unreachable: " + err.message);
  } finally {
    if (!("speechSynthesis" in window) || !state.voiceOn) {
      state.energyTarget = 0.25;
    }
  }
}

function row(dt, dd, cls) {
  const wrap = document.createElement("div");
  wrap.style.display = "contents";
  const term = document.createElement("dt");
  term.textContent = dt;
  const def = document.createElement("dd");
  def.textContent = dd;
  if (cls) {
    def.className = cls;
  }
  wrap.appendChild(term);
  wrap.appendChild(def);
  return wrap;
}

async function refreshStatus() {
  try {
    const s = await api("/status?project=" + encodeURIComponent(state.project));
    const list = $("#system-list");
    list.textContent = "";
    list.appendChild(row("CORE", s.status === "ok" ? "ONLINE" : "DEGRADED", s.status === "ok" ? "good" : "warn"));
    list.appendChild(row("PROVIDER", s.provider || "none"));
    list.appendChild(row("MODEL", s.model || "none"));
    list.appendChild(row("PROFILE", s.profile || "default"));
    list.appendChild(row("MEMORIES", String(s.memory_count)));
    list.appendChild(row("STANDING", String(s.standing_count)));
    list.appendChild(row("TASKS OPEN", String(s.tasks_open)));
    list.appendChild(row("PENDING", String(s.pending_count), s.pending_count ? "warn" : "good"));
    list.appendChild(row("SHELL", s.shell_enabled ? "ENABLED" : "DISABLED", s.shell_enabled ? "warn" : ""));
  } catch (err) {
    const list = $("#system-list");
    list.textContent = "";
    list.appendChild(row("CORE", "OFFLINE", "warn"));
  }
}

async function refreshTasks() {
  try {
    const items = await api("/tasks?project=" + encodeURIComponent(state.project));
    const ul = $("#task-list");
    ul.textContent = "";
    if (!items.length) {
      const li = document.createElement("li");
      li.className = "empty-note";
      li.textContent = "No tasks yet.";
      ul.appendChild(li);
      return;
    }
    items.forEach((t) => {
      const li = document.createElement("li");
      if (t.state === "done") {
        li.className = "done";
      }
      const st = document.createElement("span");
      st.className = "tstate";
      st.textContent = "[" + t.state.toUpperCase() + "]";
      const title = document.createElement("span");
      title.className = "ttitle";
      title.textContent = "#" + t.id + " " + t.title;
      li.appendChild(st);
      li.appendChild(title);
      if (t.state !== "done") {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = "DONE";
        btn.addEventListener("click", async () => {
          try {
            const body = await post("/chat", {
              project: state.project,
              message: "done " + t.id,
            });
            addMitra(body.reply, false);
            refreshAll();
          } catch (err) {
            addMsg("sys", "Task update failed: " + err.message);
          }
        });
        li.appendChild(btn);
      }
      ul.appendChild(li);
    });
  } catch (err) {
    /* keep previous list */
  }
}

async function refreshApprovals() {
  try {
    const items = await api(
      "/approvals/pending?project=" + encodeURIComponent(state.project)
    );
    const box = $("#approvals");
    box.textContent = "";
    if (state.pendingSeen === null) {
      state.pendingSeen = new Set(items.map((p) => p.id));
    } else {
      items.forEach((p) => {
        if (!state.pendingSeen.has(p.id)) {
          state.pendingSeen.add(p.id);
          addMsg("sys", "New approval request: " + p.id + " " + p.action);
          blip(440, 660, 160);
        }
      });
      state.pendingSeen = new Set(items.map((p) => p.id));
    }
    if (!items.length) {
      const note = document.createElement("div");
      note.className = "empty-note";
      note.textContent = "No pending approvals.";
      box.appendChild(note);
      return;
    }
    items.forEach((p) => {
      box.appendChild(
        approvalCard({
          proposal_id: p.id,
          action: p.action,
          target: p.target,
          preview: p.id + " staged " + p.created_at,
        })
      );
    });
  } catch (err) {
    /* keep previous list */
  }
}

async function refreshMemory() {
  try {
    const items = await api("/memories?project=" + encodeURIComponent(state.project));
    const box = $("#memory");
    box.textContent = "";
    if (!items.length) {
      const note = document.createElement("div");
      note.className = "empty-note";
      note.textContent = "Nothing stored yet.";
      box.appendChild(note);
      return;
    }
    items.slice(0, 30).forEach((m) => {
      const wrap = document.createElement("div");
      wrap.className = "mem-item";
      const kind = document.createElement("span");
      kind.className = "mkind";
      kind.textContent = m.kind.toUpperCase();
      const text = document.createElement("span");
      text.className = "mtext";
      text.textContent = m.text;
      const src = document.createElement("span");
      src.className = "msrc";
      src.textContent = "src " + m.source + " " + m.updated_at;
      wrap.appendChild(kind);
      wrap.appendChild(text);
      wrap.appendChild(src);
      box.appendChild(wrap);
    });
  } catch (err) {
    /* keep previous list */
  }
}

function starterFor(id) {
  if (id === "files") {
    return "list ";
  }
  if (id === "web") {
    return "fetch ";
  }
  if (id === "shell") {
    return "run ";
  }
  if (id === "github") {
    return "github read ";
  }
  if (id === "macos") {
    return "what is on my calendar?";
  }
  if (id.indexOf("mcp:") === 0) {
    return "mcp " + id.slice(4) + " ";
  }
  return "";
}

function connectorCard(card) {
  const wrap = document.createElement("div");
  wrap.className = "connector-card" + (card.enabled ? "" : " off");
  if (state.activeConnector === card.id) {
    wrap.className += " active";
  }
  const head = document.createElement("div");
  head.className = "chead";
  const label = document.createElement("span");
  label.className = "clabel";
  label.textContent = card.label;
  const dot = document.createElement("span");
  dot.className = "cdot";
  head.appendChild(label);
  head.appendChild(dot);
  const hint = document.createElement("div");
  hint.className = "chint";
  hint.textContent = card.hint + " " + card.tools.length + " tools.";
  const btns = document.createElement("div");
  btns.className = "cbtns";
  const connectBtn = document.createElement("button");
  connectBtn.type = "button";
  connectBtn.textContent = "CONNECT";
  connectBtn.addEventListener("click", () => {
    state.activeConnector = card.id;
    store("mitra-connector", card.id);
    inputEl.value = starterFor(card.id);
    inputEl.focus();
    addMsg("sys", "Connector ready: " + card.label + ". Starter placed below.");
    refreshConnectors();
  });
  const startBtn = document.createElement("button");
  startBtn.type = "button";
  startBtn.className = "ok";
  startBtn.textContent = "START TASK";
  if (!card.enabled) {
    startBtn.disabled = true;
  }
  startBtn.addEventListener("click", () => {
    state.activeConnector = card.id;
    store("mitra-connector", card.id);
    inputEl.value = starterFor(card.id);
    send();
    refreshConnectors();
  });
  btns.appendChild(connectBtn);
  btns.appendChild(startBtn);
  wrap.appendChild(head);
  wrap.appendChild(hint);
  wrap.appendChild(btns);
  return wrap;
}

async function refreshConnectors() {
  try {
    const items = await api("/connectors?project=" + encodeURIComponent(state.project));
    const box = $("#connectors");
    if (!box) {
      return;
    }
    box.textContent = "";
    items.forEach((card) => {
      box.appendChild(connectorCard(card));
    });
  } catch (err) {
    /* keep previous list */
  }
}

async function refreshBriefing() {
  try {
    const data = await api("/briefing?project=" + encodeURIComponent(state.project));
    const box = $("#briefing");
    if (!box) {
      return data;
    }
    box.textContent = "";
    const recap = document.createElement("div");
    recap.className = "brecap";
    recap.textContent = data.recap || "No earlier chat yet.";
    box.appendChild(recap);
    (data.suggestions || []).slice(0, 4).forEach((tip) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "bsug";
      btn.textContent = "> " + tip;
      btn.addEventListener("click", () => {
        inputEl.value = tip;
        inputEl.focus();
      });
      box.appendChild(btn);
    });
    return data;
  } catch (err) {
    return null;
  }
}

async function refreshAutostart() {
  try {
    const data = await api("/autostart");
    state.autostart = Boolean(data.enabled);
    paintAutostart();
  } catch (err) {
    /* keep previous state */
  }
}

function paintAutostart() {
  const btn = $("#autostart-toggle");
  if (!btn) {
    return;
  }
  btn.textContent = state.autostart ? "AUTOSTART ON" : "AUTOSTART OFF";
  btn.classList.toggle("on", state.autostart);
}

async function loadProfile() {
  try {
    const res = await fetch("/profile.json");
    if (!res.ok) {
      return;
    }
    const data = await res.json();
    const words = $("#orbit-words");
    if (words && data.details && data.details.length) {
      words.textContent = data.details.join(" + ") + " + ";
    }
  } catch (err) {
    /* keep default orbit words */
  }
}

function refreshAll() {
  refreshStatus();
  refreshTasks();
  refreshApprovals();
  refreshMemory();
  refreshConnectors();
  refreshBriefing();
}

const canvas = $("#reactor");
const paint = canvas.getContext("2d");

function sizeCanvas() {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
}

function ring(cx, cy, r, rot, width, color, dashA, dashB) {
  paint.beginPath();
  paint.lineWidth = width;
  paint.strokeStyle = color;
  paint.setLineDash([dashA, dashB]);
  paint.lineDashOffset = rot;
  paint.arc(cx, cy, r, 0, Math.PI * 2);
  paint.stroke();
  paint.setLineDash([]);
}

function drawReactor(t) {
  const w = canvas.width;
  const h = canvas.height;
  const cx = w / 2;
  const cy = h / 2;
  const base = Math.min(w, h) / 2 - 8;
  const e = state.energy;
  paint.clearRect(0, 0, w, h);

  const glow = paint.createRadialGradient(cx, cy, 0, cx, cy, base);
  glow.addColorStop(0, "rgba(70, 230, 255, " + (0.25 + e * 0.45).toFixed(3) + ")");
  glow.addColorStop(1, "rgba(70, 230, 255, 0)");
  paint.fillStyle = glow;
  paint.fillRect(0, 0, w, h);

  ring(cx, cy, base, t * 0.00012, 2, "rgba(70, 230, 255, 0.5)", base * 0.42, base * 0.1);
  ring(cx, cy, base * 0.8, 0 - t * 0.0002, 5, "rgba(70, 230, 255, 0.28)", base * 0.2, base * 0.34);
  ring(cx, cy, base * 0.62, t * 0.00035, 1.5, "rgba(150, 245, 255, 0.65)", base * 0.6, base * 0.08);

  paint.save();
  paint.translate(cx, cy);
  paint.strokeStyle = "rgba(70, 230, 255, 0.55)";
  paint.lineWidth = 2;
  for (let i = 0; i < 72; i += 1) {
    const a = (i / 72) * Math.PI * 2 + t * 0.00006;
    const long = i % 6 === 0;
    const r1 = base * 0.9;
    const r2 = base * (long ? 0.99 : 0.95);
    paint.beginPath();
    paint.moveTo(Math.cos(a) * r1, Math.sin(a) * r1);
    paint.lineTo(Math.cos(a) * r2, Math.sin(a) * r2);
    paint.stroke();
  }
  paint.restore();

  for (let k = 0; k < 3; k += 1) {
    const a = t * 0.0009 + (k / 3) * Math.PI * 2;
    const px = cx + Math.cos(a) * base * 0.8;
    const py = cy + Math.sin(a) * base * 0.8;
    paint.beginPath();
    paint.fillStyle = "#bdf6ff";
    paint.arc(px, py, 2.5 + e * 2, 0, Math.PI * 2);
    paint.fill();
  }

  const pulse = 1 + Math.sin(t * 0.004) * 0.06 + e * 0.25;
  const coreR = base * 0.3 * pulse;
  paint.beginPath();
  paint.fillStyle = "#eafcff";
  paint.arc(cx, cy, coreR, 0, Math.PI * 2);
  paint.fill();
  paint.beginPath();
  paint.strokeStyle = "rgba(234, 252, 255, 0.9)";
  paint.lineWidth = 2;
  paint.arc(cx, cy, coreR + 7, 0, Math.PI * 2);
  paint.stroke();
}

function frame(t) {
  state.energy += (state.energyTarget - state.energy) * 0.05;
  drawReactor(t || 0);
  if (!reducedMotion) {
    window.requestAnimationFrame(frame);
  }
}

function tickClock() {
  const now = new Date();
  $("#clock").textContent = now.toLocaleTimeString("en-GB");
  $("#date").textContent = now.toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

function setupMic() {
  const btn = $("#mic");
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const canRecord = !!(
    navigator.mediaDevices &&
    navigator.mediaDevices.getUserMedia &&
    window.MediaRecorder
  );
  if (!canRecord && !SR) {
    btn.disabled = true;
    btn.title = "Voice input not supported in this browser";
    return;
  }

  function setIdle() {
    btn.classList.remove("rec");
    btn.textContent = "MIC";
  }

  function startBrowserRec() {
    if (!SR) {
      addMsg("sys", "Microphone unavailable in this browser.");
      return;
    }
    let rec = null;
    try {
      rec = new SR();
    } catch (err) {
      addMsg("sys", "Microphone unavailable in this browser.");
      return;
    }
    rec.lang = "en-US";
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    btn.classList.add("rec");
    btn.textContent = "STOP";
    browserRec = rec;
    rec.onresult = (event) => {
      let interim = "";
      let final = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const part = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          final += part;
        } else {
          interim += part;
        }
      }
      inputEl.value = (final || interim).trim();
      if (final.trim()) {
        rec.stop();
        send();
      }
    };
    rec.onend = () => {
      browserRec = null;
      setIdle();
    };
    rec.onerror = (event) => {
      browserRec = null;
      setIdle();
      addMsg("sys", "Microphone error: " + (event.error || "unknown"));
    };
    try {
      rec.start();
    } catch (err) {
      browserRec = null;
      setIdle();
    }
  }

  async function uploadRecording(blob) {
    setIdle();
    if (!blob.size) {
      return;
    }
    addMsg("sys", "Transcribing...");
    try {
      const res = await fetch(
        "/stt?project=" + encodeURIComponent(state.project) + "&suffix=webm",
        { method: "POST", body: blob }
      );
      if (!res.ok) {
        throw new Error("server " + res.status);
      }
      const data = await res.json();
      inputEl.value = (data.text || "").trim();
      if (inputEl.value) {
        send();
      }
    } catch (err) {
      addMsg("sys", "Server transcription failed, trying browser mic.");
      startBrowserRec();
    }
  }

  async function startServerRec() {
    let stream = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      return false;
    }
    const chunks = [];
    let recorder = null;
    try {
      recorder = new MediaRecorder(stream);
    } catch (err) {
      stream.getTracks().forEach((track) => track.stop());
      return false;
    }
    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size) {
        chunks.push(event.data);
      }
    };
    recorder.onstop = () => {
      stream.getTracks().forEach((track) => track.stop());
      serverRec = null;
      uploadRecording(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
    };
    serverRec = recorder;
    btn.classList.add("rec");
    btn.textContent = "STOP";
    recorder.start();
    return true;
  }

  let browserRec = null;
  let serverRec = null;
  let holdTalk = false;

  async function startTalk() {
    if (canRecord) {
      const started = await startServerRec();
      if (!started) {
        startBrowserRec();
      }
      return;
    }
    startBrowserRec();
  }

  function stopTalk() {
    if (serverRec) {
      serverRec.stop();
      return;
    }
    if (browserRec) {
      browserRec.stop();
    }
  }

  btn.title = "Click to talk, or hold Space outside the input box";
  btn.addEventListener("click", async () => {
    if (serverRec || browserRec) {
      stopTalk();
      return;
    }
    startTalk();
  });
  document.addEventListener("keydown", (event) => {
    if (event.code !== "Space" || event.repeat || holdTalk) {
      return;
    }
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "BUTTON") {
      return;
    }
    if (serverRec || browserRec) {
      return;
    }
    event.preventDefault();
    holdTalk = true;
    startTalk();
  });
  document.addEventListener("keyup", (event) => {
    if (event.code !== "Space" || !holdTalk) {
      return;
    }
    holdTalk = false;
    stopTalk();
  });
}

function setupEvents() {
  $("#send").addEventListener("click", send);
  inputEl.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      send();
    }
  });
  const projectEl = $("#project");
  projectEl.value = state.project;
  projectEl.addEventListener("change", () => {
    state.project = projectEl.value.trim() || "default";
    projectEl.value = state.project;
    saveProject();
    addMsg("sys", "Project set to " + state.project + ".");
    refreshAll();
  });
  $("#task-add").addEventListener("click", async () => {
    const el = $("#task-input");
    const title = el.value.trim();
    if (!title) {
      return;
    }
    el.value = "";
    try {
      const body = await post("/chat", {
        project: state.project,
        message: "todo: " + title,
      });
      addMitra(body.reply, false);
      refreshAll();
    } catch (err) {
      addMsg("sys", "Task add failed: " + err.message);
    }
  });
  $("#task-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      $("#task-add").click();
    }
  });
  $("#memory-refresh").addEventListener("click", refreshMemory);
  const voiceBtn = $("#voice-toggle");
  voiceBtn.addEventListener("click", () => {
    state.voiceOn = !state.voiceOn;
    voiceBtn.textContent = state.voiceOn ? "VOICE ON" : "VOICE OFF";
    voiceBtn.classList.toggle("on", state.voiceOn);
    if (!state.voiceOn) {
      stopSpeak();
    }
  });
  const soundBtn = $("#sound-toggle");
  soundBtn.addEventListener("click", () => {
    state.soundOn = !state.soundOn;
    soundBtn.textContent = state.soundOn ? "SOUND ON" : "SOUND OFF";
    soundBtn.classList.toggle("on", state.soundOn);
  });
  const rateEl = $("#rate");
  const pitchEl = $("#pitch");
  rateEl.value = String(state.rate);
  pitchEl.value = String(state.pitch);
  rateEl.addEventListener("input", () => {
    state.rate = parseFloat(rateEl.value);
    store("mitra-rate", rateEl.value);
  });
  pitchEl.addEventListener("input", () => {
    state.pitch = parseFloat(pitchEl.value);
    store("mitra-pitch", pitchEl.value);
  });
  $("#voice-select").addEventListener("change", (event) => {
    store("mitra-voice", event.target.value);
  });
  const personaSelect = $("#persona-select");
  if (personaSelect) {
    personaSelect.value = state.persona;
    personaSelect.addEventListener("change", (event) => {
      state.persona = event.target.value || "auto";
      store("mitra-persona", state.persona);
      applyPersona(true);
    });
  }
  $("#voice-test").addEventListener("click", () => {
    stopSpeak();
    const preset = state.personaPresets[state.persona];
    const line = preset && preset.line ? preset.line : "Systems nominal. This is my voice, at your chosen pace and tone.";
    speak(line);
    blip(660, 880, 130);
  });
  const autoBtn = $("#autostart-toggle");
  if (autoBtn) {
    autoBtn.addEventListener("click", async () => {
      const want = !state.autostart;
      addMsg("sys", want ? "Enabling autostart at login." : "Removing autostart.");
      try {
        const data = await post("/autostart", { enabled: want });
        state.autostart = Boolean(data.enabled);
        paintAutostart();
        addMsg("sys", data.message || "Autostart updated.");
      } catch (err) {
        addMsg("sys", "Autostart change failed: " + err.message);
      }
    });
  }
  window.addEventListener("resize", sizeCanvas);
}

function boot() {
  const lines = [
    "MITRA interface starting",
    "Linking local core",
    "Loading project memory",
    "Voice systems ready",
    "All systems nominal",
  ];
  const box = $("#boot-lines");
  const overlay = $("#boot");
  if (reducedMotion) {
    overlay.parentNode.removeChild(overlay);
    greet();
    return;
  }
  lines.forEach((line, idx) => {
    window.setTimeout(() => {
      const div = document.createElement("div");
      div.textContent = "> " + line;
      box.appendChild(div);
    }, 240 * (idx + 1));
  });
  window.setTimeout(() => {
    overlay.classList.add("done");
    window.setTimeout(() => {
      if (overlay.parentNode) {
        overlay.parentNode.removeChild(overlay);
      }
    }, 500);
    greet();
  }, 240 * (lines.length + 1));
}

async function greet() {
  refreshAll();
  try {
    const data = await api("/briefing?project=" + encodeURIComponent(state.project));
    if (data && data.greeting) {
      addMitra(data.greeting);
    } else {
      addMitra("Systems nominal. I am Mitra, ready on project " + state.project + ".");
    }
  } catch (err) {
    addMitra("Systems nominal. I am Mitra, ready on project " + state.project + ".");
  }
}

if ("speechSynthesis" in window) {
  loadVoices();
  window.speechSynthesis.onvoiceschanged = loadVoices;
}
sizeCanvas();
setupEvents();
setupMic();
fetchPersonas();
refreshAutostart();
loadProfile();
tickClock();
window.setInterval(tickClock, 1000);
window.setInterval(refreshApprovals, 6000);
window.setInterval(refreshStatus, 15000);
if (reducedMotion) {
  drawReactor(0);
} else {
  window.requestAnimationFrame(frame);
}
boot();
