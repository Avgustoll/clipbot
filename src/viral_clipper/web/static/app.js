"use strict";

// ---------------------------------------------------------------------------
// Utilità
// ---------------------------------------------------------------------------

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path, options = {}) {
  const opts = { headers: { "Content-Type": "application/json" }, ...options };
  if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : `Errore ${res.status}`);
  return data;
}

function toast(msg, isError = false) {
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " error" : "");
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), isError ? 6000 : 3000);
}

async function run(action, okMsg) {
  try {
    const r = await action();
    if (okMsg) toast(okMsg);
    pollJobs(true);
    return r;
  } catch (e) {
    toast(e.message, true);
    return null;
  }
}

function compact(n) {
  if (n === null || n === undefined) return "—";
  if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1).replace(".", ",") + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1).replace(".", ",") + "K";
  return String(Math.round(n));
}

function fmtDate(iso, withTime = false) {
  if (!iso) return "—";
  const d = new Date(iso);
  const opts = { day: "numeric", month: "short" };
  if (withTime) Object.assign(opts, { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString("it-IT", opts);
}

function weekLabel(week) {
  const [y, w] = week.split("-W").map(Number);
  const jan4 = new Date(y, 0, 4);
  const monday = new Date(jan4);
  monday.setDate(jan4.getDate() - ((jan4.getDay() + 6) % 7) + (w - 1) * 7);
  const sunday = new Date(monday);
  sunday.setDate(monday.getDate() + 6);
  const f = d => d.toLocaleDateString("it-IT", { day: "numeric", month: "short" });
  return `Settimana ${w} · ${f(monday)} – ${f(sunday)}`;
}

function trendBadge(label) {
  const map = {
    "in crescita": ["good", "↑"], "stabile": ["warn", "→"], "in calo": ["bad", "↓"], "da misurare": ["", "…"],
  };
  const [cls, icon] = map[label] || ["", ""];
  return `<span class="badge ${cls}">${icon} ${esc(label)}</span>`;
}

function statusBadge(status) {
  const map = {
    completato: ["good", "✓"], pronto: ["good", "✓"], caricato: ["good", "✓"],
    in_corso: ["accent", "●"], in_coda: ["", "…"], in_attesa: ["", "…"], errore: ["bad", "!"],
  };
  const [cls, icon] = map[status] || ["", ""];
  return `<span class="badge ${cls}">${icon} ${esc(String(status).replace("_", " "))}</span>`;
}

function markdown(md) {
  if (!md) return "";
  if (window.marked && window.DOMPurify) return DOMPurify.sanitize(marked.parse(md));
  return `<pre style="white-space:pre-wrap">${esc(md)}</pre>`;
}

// ---------------------------------------------------------------------------
// Stato e routing
// ---------------------------------------------------------------------------

const state = {
  page: "analyst",
  status: {},
  analyst: { contextId: null, week: null, selected: {} },
  clipper: { videoId: null },
  stats: { channelId: null, sort: "views", reportId: null },
};

const pages = { analyst: renderAnalyst, clipper: renderClipper, trainer: renderTrainer, statistics: renderStatistics };

function go(page) {
  location.hash = page;
}

function route() {
  const [page, query] = (location.hash.slice(1) || "analyst").split("?");
  state.page = pages[page] ? page : "analyst";
  $$(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.page === state.page));
  if (query) {
    const params = new URLSearchParams(query);
    if (params.get("errore")) toast("YouTube: " + params.get("errore"), true);
    if (params.get("collegato")) toast("Canale YouTube collegato");
    history.replaceState(null, "", "#" + state.page);
  }
  refresh();
}

async function refresh() {
  try {
    await pages[state.page]();
  } catch (e) {
    $("#page").innerHTML = `<div class="empty"><h3>Qualcosa non va</h3><p>${esc(e.message)}</p></div>`;
  }
}

// ---------------------------------------------------------------------------
// ANALYST
// ---------------------------------------------------------------------------

const WEEKDAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"];

async function renderAnalyst() {
  const [contexts, weeks] = await Promise.all([api("/api/contexts"), api("/api/analyst/weeks")]);
  const a = state.analyst;
  // "new" = l'utente ha scelto "+ Nuovo contesto…": non tornare al primo contesto esistente
  if (a.contextId !== "new" && !contexts.find(c => c.id === a.contextId)) a.contextId = contexts[0]?.id ?? "new";
  if (!weeks.find(w => w.week === a.week)) a.week = weeks[0]?.week ?? null;
  const ctx = contexts.find(c => c.id === a.contextId);
  const runs = a.week ? await api(`/api/analyst/runs?week=${encodeURIComponent(a.week)}`) : [];

  if (state.page !== "analyst") return;  // l'utente ha cambiato pagina durante il caricamento
  $("#page").innerHTML = `
    <div class="page-head">
      <div class="grow"><h1>Analyst</h1><div class="page-sub">Trova video lunghi con buon engagement e views in crescita nelle ultime 3 settimane.</div></div>
    </div>
    <div class="card" style="margin-bottom:18px">
      <div class="row">
        <span class="muted small" style="font-weight:600">Contesto</span>
        <select id="ctx-select" style="width:auto;min-width:240px">
          ${contexts.map(c => `<option value="${c.id}" ${c.id === a.contextId ? "selected" : ""}>${esc(c.name)}</option>`).join("")}
          <option value="new" ${ctx ? "" : "selected"}>+ Nuovo contesto…</option>
        </select>
        ${ctx ? `<span class="muted small">${esc(contextSummary(ctx))}</span>` : ""}
        <span style="flex:1"></span>
        <button class="btn primary" id="c-run-top" ${ctx ? "" : "disabled"}>▶ Esegui analisi</button>
      </div>
      <details class="fold" id="ctx-fold" style="margin-top:12px" ${ctx ? "" : "open"}>
        <summary>${ctx ? "Modifica contesto e automazione" : "Crea il contesto"}</summary>
        <div style="margin-top:12px">${contextForm(ctx)}</div>
      </details>
    </div>
    ${weeks.length ? `<div class="tabs">${weeks.map(w => `
      <button class="tab ${w.week === a.week ? "active" : ""}" data-week="${w.week}">${esc(weekLabel(w.week))}<span class="count">${w.runs}</span></button>`).join("")}</div>` : ""}
    <div id="runs">${runs.length ? runs.map(renderRun).join("") : `
      <div class="card empty"><h3>Nessuna analisi ancora</h3><p>Scegli o crea un contesto e premi <b>Esegui analisi</b>. Ogni settimana avrà la sua pagina.</p></div>`}
    </div>`;

  $("#ctx-select").onchange = e => { a.contextId = e.target.value === "new" ? "new" : Number(e.target.value); renderAnalyst(); };
  $$(".tab[data-week]").forEach(t => t.onclick = () => { a.week = t.dataset.week; renderAnalyst(); });
  bindContextForm(ctx);
  bindRuns(runs);
}

function contextSummary(c) {
  const parts = [];
  if (c.queries.length) parts.push(`${c.queries.length} ricerche`);
  if (c.channels.length) parts.push(`${c.channels.length} canali`);
  const auto = { daily: `ogni giorno alle ${c.auto_hour}:00`, weekly: `ogni ${WEEKDAYS[c.auto_weekday]} alle ${c.auto_hour}:00` }[c.auto_freq];
  parts.push(auto ? `automatica ${auto}` : "solo manuale");
  return parts.join(" · ");
}

function contextForm(ctx) {
  const c = ctx || { name: "", brief: "", queries: [], channels: [], use_agent: 1, auto_freq: "off", auto_hour: 8, auto_weekday: 0 };
  const key = state.status.api_key;
  return `<div class="ctx-form">
    <div class="stack">
    <label class="field">Nome del contesto<input type="text" id="c-name" value="${esc(c.name)}" placeholder="Es. Podcast business IT"></label>
    <label class="field">Descrizione per l'agente
      <textarea id="c-brief" rows="3" placeholder="Es. podcast italiani di business, finanza personale e interviste a imprenditori">${esc(c.brief)}</textarea>
    </label>
    <label class="check"><input type="checkbox" id="c-agent" ${c.use_agent && key ? "checked" : ""} ${key ? "" : "disabled"}>
      Usa l'agente Claude ${key ? "" : '<span class="muted small">(serve la chiave Claude)</span>'}</label>
    ${key ? "" : `<div class="notice" style="font-size:12.5px">Modalità <b>senza AI</b>: se non indichi ricerche, vengono ricavate dalla descrizione
      (es. "podcast di business, finanza e interviste" → 3 ricerche). La classifica e la sintesi sono calcolate dalle metriche.</div>`}
    </div>
    <div class="stack">
    <label class="field">Ricerche YouTube <span class="muted small">una per riga</span>
      <textarea id="c-queries" rows="3" placeholder="podcast business italiano&#10;intervista imprenditore">${esc(c.queries.join("\n"))}</textarea>
    </label>
    <label class="field">Canali da includere <span class="muted small">@handle o link, uno per riga</span>
      <textarea id="c-channels" rows="2" placeholder="@TheBSMT">${esc(c.channels.join("\n"))}</textarea>
    </label>
    </div>
    <div class="stack" style="gap:8px">
      <h3 style="margin:0">Automazione</h3>
      <div class="row" style="flex-wrap:nowrap">
        <select id="c-freq">
          <option value="off" ${c.auto_freq === "off" ? "selected" : ""}>Disattivata</option>
          <option value="daily" ${c.auto_freq === "daily" ? "selected" : ""}>Ogni giorno</option>
          <option value="weekly" ${c.auto_freq === "weekly" ? "selected" : ""}>Ogni settimana</option>
        </select>
        <select id="c-weekday" ${c.auto_freq === "weekly" ? "" : "hidden"}>
          ${WEEKDAYS.map((d, i) => `<option value="${i}" ${i === c.auto_weekday ? "selected" : ""}>${d}</option>`).join("")}
        </select>
        <select id="c-hour" ${c.auto_freq === "off" ? "hidden" : ""}>
          ${Array.from({ length: 24 }, (_, h) => `<option value="${h}" ${h === c.auto_hour ? "selected" : ""}>ore ${String(h).padStart(2, "0")}:00</option>`).join("")}
        </select>
      </div>
      <div class="muted small">Le analisi automatiche partono mentre ClipBot Studio è aperto, e aggiornano anche lo storico delle views.</div>
      <div class="row" style="margin-top:auto">
        <button class="btn" id="c-save">${ctx ? "Salva modifiche" : "Crea contesto"}</button>
        ${ctx ? '<button class="btn ghost danger sm" id="c-del" style="margin-left:auto">Elimina contesto</button>' : ""}
      </div>
    </div>
  </div>`;
}

function readContextForm() {
  const lines = id => $(id).value.split("\n").map(s => s.trim()).filter(Boolean);
  return {
    name: $("#c-name").value.trim() || "Senza nome",
    brief: $("#c-brief").value.trim(),
    queries: lines("#c-queries"),
    channels: lines("#c-channels"),
    use_agent: $("#c-agent").checked,
    auto_freq: $("#c-freq").value,
    auto_hour: Number($("#c-hour").value),
    auto_weekday: Number($("#c-weekday").value),
  };
}

function bindContextForm(ctx) {
  $("#c-freq").onchange = e => {
    $("#c-weekday").hidden = e.target.value !== "weekly";
    $("#c-hour").hidden = e.target.value === "off";
  };
  const save = async () => {
    const body = readContextForm();
    if (ctx) {
      await api(`/api/contexts/${ctx.id}`, { method: "PUT", body });
      return ctx.id;
    }
    const r = await api("/api/contexts", { method: "POST", body });
    state.analyst.contextId = r.id;
    return r.id;
  };
  $("#c-save").onclick = async () => { if (await run(save, "Contesto salvato")) renderAnalyst(); };
  $("#c-run-top").onclick = async () => {
    // Se il modulo è aperto salva prima le modifiche, così l'analisi usa il contesto aggiornato
    const id = $("#ctx-fold").open ? await run(save) : ctx.id;
    if (!id) return;
    if (await run(() => api(`/api/analyst/run/${id}`, { method: "POST" }), "Analisi avviata")) renderAnalyst();
  };
  const del = $("#c-del");
  if (del) del.onclick = async () => {
    if (!confirm(`Eliminare il contesto "${ctx.name}"? Le analisi già fatte restano.`)) return;
    await run(() => api(`/api/contexts/${ctx.id}`, { method: "DELETE" }));
    state.analyst.contextId = null;
    renderAnalyst();
  };
}

function renderRun(r) {
  const qualified = r.results.filter(v => v.qualificato).length;
  const sel = state.analyst.selected[r.id] || r.results[0]?.id;
  const selected = r.results.find(v => v.id === sel);
  const head = `
    <div class="run-head">
      <span class="title">${esc(r.context_name)}</span>
      <span class="muted small">${fmtDate(r.started_at, true)}</span>
      ${statusBadge(r.status)}
      <span class="badge">${r.mode === "agente" ? "agente Claude" : "senza AI"}</span>
      ${r.trigger === "automatico" ? '<span class="badge accent">automatica</span>' : ""}
      ${r.status === "completato" ? `<span class="muted small">${r.results.length} video su ${r.total_analyzed} analizzati · <b>${qualified}</b> qualificati</span>` : ""}
      <button class="btn ghost sm" data-del-run="${r.id}" style="margin-left:auto">Elimina</button>
    </div>`;
  if (r.status !== "completato") {
    return `<div class="card run">${head}<div class="muted">${r.status === "in_corso" ? "Analisi in corso: i progressi sono nel riquadro in basso a destra." : "L'analisi non è andata a buon fine: vedi il dettaglio nel riquadro lavori."}</div></div>`;
  }
  return `
    <div class="card run" data-run="${r.id}">
      ${head}
      ${r.commentary ? `<details class="fold" style="margin-bottom:12px" ${r.mode === "agente" ? "" : "open"}><summary>${r.mode === "agente" ? "Analisi dell'agente" : "Sintesi"}</summary><div class="commentary report">${markdown(r.commentary)}</div></details>` : ""}
      <div class="split">
        <div class="video-list">
          ${r.results.map((v, i) => `
            <div class="video-row ${v.id === sel ? "selected" : ""}" data-pick="${r.id}:${esc(v.id)}">
              <div class="rank">${i + 1}</div>
              <img class="thumb" src="${esc(v.thumb)}" alt="" loading="lazy">
              <div>
                <div class="video-title">${esc(v.titolo)}</div>
                <div class="video-meta"><span>${esc(v.canale)}</span><span>${v.durata_min}′</span><span>${compact(v.views)} views</span></div>
                <div class="row" style="margin-top:5px;gap:5px">
                  ${trendBadge(v.trend)}
                  ${v.qualificato ? '<span class="badge good">✓ qualificato</span>' : ""}
                  ${v.in_clipper ? '<span class="badge accent">nel clipper</span>' : ""}
                </div>
              </div>
              <div class="score num">${Math.round(v.punteggio)}<small>punti</small></div>
            </div>`).join("")}
        </div>
        <div class="sticky">${selected ? videoDetail(selected, r.id) : ""}</div>
      </div>
    </div>`;
}

function videoDetail(v, runId) {
  const crit = [
    ["durata_30min", `Durata oltre 30 minuti (${v.durata_min}′)`],
    ["engagement_medio", `Engagement almeno medio (tenuta ${v.retention_stimata || "—"})`],
    ["views_in_crescita", `Views in crescita (3 settimane): ${v.trend}`],
  ];
  const icon = ok => ok === true ? '<span class="icon-ok">✓</span>' : ok === false ? '<span class="icon-no">✕</span>' : '<span class="icon-wait">…</span>';
  return `
    <div class="card" style="padding:14px">
      <img class="detail-thumb" src="${esc(v.thumb.replace("mqdefault", "hqdefault"))}" alt="">
      <h3>${esc(v.titolo)}</h3>
      <div class="row small" style="margin-bottom:12px">
        <a href="${esc(v.url)}" target="_blank" rel="noopener">Apri su YouTube ↗</a>
        <a href="https://www.youtube.com/results?search_query=${encodeURIComponent(v.canale)}" target="_blank" rel="noopener">Canale: ${esc(v.canale)} ↗</a>
      </div>
      <div class="criteria">${crit.map(([k, t]) => `<div class="criterion">${icon(v.criteri[k])}${esc(t)}</div>`).join("")}</div>
      <dl class="kv">
        <dt>Views totali</dt><dd>${compact(v.views)}</dd>
        <dt>Views ultime 3 settimane</dt><dd>${compact(v.views_ultime_3_settimane)}</dd>
        <dt>Views/giorno recenti</dt><dd>${compact(v.views_giorno_recenti)}</dd>
        <dt>Views/giorno dall'uscita</dt><dd>${compact(v.views_giorno_dall_uscita)}</dd>
        <dt>Momentum</dt><dd>${v.momentum ?? "—"}</dd>
        <dt>Tenuta stimata</dt><dd>${v.retention_stimata || "—"}</dd>
        <dt>Like / views</dt><dd>${v.like_rate || "—"}</dd>
        <dt>Commenti / views</dt><dd>${v.comment_rate || "—"}</dd>
        <dt>Iscritti canale</dt><dd>${compact(v.iscritti_canale)}</dd>
        <dt>Uscito</dt><dd>${fmtDate(v.uscito)} · ${v.eta_giorni ?? "?"} gg</dd>
        <dt>Punteggio</dt><dd>${v.punteggio}</dd>
      </dl>
      <div class="muted small" style="margin:10px 0">Trend: ${esc(v.fonte_trend)}</div>
      <button class="btn ${v.in_clipper ? "" : "primary"}" style="width:100%;justify-content:center" data-queue="${runId}:${esc(v.id)}">
        ${v.in_clipper ? "Apri nel Clipper →" : "✂︎ Manda al Clipper"}
      </button>
    </div>`;
}

function bindRuns(runs) {
  $$("[data-pick]").forEach(el => el.onclick = () => {
    const [runId, vid] = el.dataset.pick.split(":");
    state.analyst.selected[runId] = vid;
    const r = runs.find(x => x.id === Number(runId));
    $(`[data-run="${runId}"]`).outerHTML = renderRun(r);
    bindRuns(runs);
  });
  $$("[data-queue]").forEach(el => el.onclick = async () => {
    const [runId, vid] = el.dataset.queue.split(":");
    const r = runs.find(x => x.id === Number(runId));
    const v = r.results.find(x => x.id === vid);
    if (v.in_clipper) {
      const list = await api("/api/clipper/videos");
      state.clipper.videoId = list.find(x => x.video_id === vid)?.id ?? null;
      return go("clipper");
    }
    const res = await run(() => api("/api/clipper/videos", { method: "POST", body: { run_id: r.id, video: v } }), "Aggiunto al Clipper");
    if (res) { v.in_clipper = true; $(`[data-run="${runId}"]`).outerHTML = renderRun(r); bindRuns(runs); updateBadges(); }
  });
  $$("[data-del-run]").forEach(el => el.onclick = async () => {
    if (!confirm("Eliminare questa analisi?")) return;
    await run(() => api(`/api/analyst/runs/${el.dataset.delRun}`, { method: "DELETE" }));
    renderAnalyst();
  });
}

// ---------------------------------------------------------------------------
// CLIPPER
// ---------------------------------------------------------------------------

async function renderClipper() {
  const [videos, channels] = await Promise.all([api("/api/clipper/videos"), api("/api/channels")]);
  const connected = channels.filter(c => c.connected);
  const c = state.clipper;
  if (!videos.find(v => v.id === c.videoId)) c.videoId = videos[0]?.id ?? null;
  const v = videos.find(x => x.id === c.videoId);
  const job = v?.last_job_id ? await api(`/api/jobs/${v.last_job_id}`).catch(() => null) : null;

  if (state.page !== "clipper") return;  // l'utente ha cambiato pagina durante il caricamento
  $("#page").innerHTML = `
    <div class="page-head">
      <div class="grow"><h1>Clipper</h1><div class="page-sub">Clip divise per video. L'agente copywriter legge lo script e sceglie i momenti salienti.</div></div>
    </div>
    <div class="grid-2">
      <div class="card stack sticky">
        <h3 style="margin:0">Video</h3>
        <div class="row" style="flex-wrap:nowrap">
          <input type="text" id="add-url" placeholder="Incolla un link YouTube…">
          <button class="btn sm" id="add-url-btn">Aggiungi</button>
        </div>
        <div class="queue">
          ${videos.length ? videos.map(x => `
            <div class="queue-item ${x.id === c.videoId ? "selected" : ""}" data-video="${x.id}">
              <img class="thumb" src="${esc(x.thumb)}" alt="" loading="lazy">
              <div>
                <div class="t">${esc(x.title)}</div>
                <div class="row" style="gap:5px;margin-top:4px">${statusBadge(x.status)}<span class="muted small">${x.clips.length} clip</span></div>
              </div>
            </div>`).join("") : '<div class="muted small">Seleziona i video dalla pagina <a href="#analyst">Analyst</a> oppure incolla un link qui sopra.</div>'}
        </div>
      </div>
      <div class="stack">${v ? clipperVideo(v, job, connected) : `
        <div class="card empty"><h3>Nessun video selezionato</h3><p>Manda un video dalla pagina Analyst con <b>✂︎ Manda al Clipper</b>.</p></div>`}
      </div>
    </div>`;

  $$("[data-video]").forEach(el => el.onclick = () => { c.videoId = Number(el.dataset.video); renderClipper(); });
  $("#add-url-btn").onclick = async () => {
    const url = $("#add-url").value.trim();
    if (!url) return;
    const r = await run(() => api("/api/clipper/videos/url", { method: "POST", body: { url } }), "Video aggiunto");
    if (r) { c.videoId = r.id; renderClipper(); updateBadges(); }
  };
  if (v) bindClipper(v, connected);
}

function clipperVideo(v, job, connected) {
  const s = v.stats || {};
  const running = v.status === "in_corso";
  const key = state.status.api_key;
  return `
    <div class="card">
      <div class="row" style="align-items:flex-start;flex-wrap:nowrap;gap:14px">
        <img class="thumb" src="${esc(v.thumb)}" alt="" style="width:160px">
        <div style="flex:1">
          <h2 style="margin-bottom:4px">${esc(v.title)}</h2>
          <div class="video-meta">
            <span>${esc(v.channel)}</span><span>${Math.round((v.duration || 0) / 60)}′</span>
            ${s.views ? `<span>${compact(s.views)} views</span>` : ""}
            ${s.retention_stimata ? `<span>tenuta ${esc(s.retention_stimata)}</span>` : ""}
            ${s.trend ? trendBadge(s.trend) : ""}
          </div>
          <div class="row small" style="margin-top:6px">
            <a href="${esc(v.url)}" target="_blank" rel="noopener">Apri su YouTube ↗</a>
            <button class="btn ghost sm danger" id="v-remove">Rimuovi dal clipper</button>
          </div>
        </div>
      </div>
      <div class="clip-settings" style="margin-top:16px">
        <label class="field">Numero di clip<input type="number" id="g-n" min="1" max="20" value="${v.n_clips}"></label>
        <label class="field">Durata minima (s)<input type="number" id="g-min" min="5" max="170" value="${v.min_len}"></label>
        <label class="field">Durata massima (s)<input type="number" id="g-max" min="10" max="180" value="${v.max_len}"></label>
        <div class="row" style="justify-content:flex-end">
          <button class="btn primary" id="g-run" ${running ? "disabled" : ""}>${running ? '<span class="spinner"></span> In lavorazione…' : v.clips.length ? "↻ Rigenera tutte" : "✦ Genera clip"}</button>
        </div>
      </div>
      <label class="field" style="margin-top:12px">Istruzioni per il copywriter <span class="muted small">facoltative</span>
        <textarea id="g-instr" rows="2" placeholder="Es. privilegia le storie sui soldi, evita la parte sulla politica, tono ironico nei titoli">${esc(v.instructions || "")}</textarea>
      </label>
      <div class="notice ${key ? "" : "warn"}" style="margin-top:12px">
        ${key ? "L'<b>agente copywriter</b> (Claude) leggerà la trascrizione e userà come riferimento gli Shorts attivi nel <a href='#trainer'>Trainer</a>."
              : "Senza <code>ANTHROPIC_API_KEY</code> l'agente copywriter non è attivo: le clip vengono scelte con un'euristica (parole chiave + energia audio)."}
      </div>
      ${v.status === "errore" && v.error ? `<div class="notice warn" style="margin-top:10px">Errore: ${esc(v.error)}</div>` : ""}
      ${job && (running || v.status === "errore") ? `<details class="fold" style="margin-top:12px" ${running ? "open" : ""}><summary>Log di lavorazione</summary><div class="log" id="clip-log">${esc(job.log)}</div></details>` : ""}
    </div>
    ${v.clips.length ? `<div class="clip-grid">${v.clips.map(c => clipCard(c, connected)).join("")}</div>` : ""}`;
}

function clipCard(c, connected) {
  const fmtT = t => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  const busy = c.status === "in_corso";
  const trimBtn = (edge, action, secs, label, title) =>
    `<button class="btn sm c-trim" data-edge="${edge}" data-action="${action}" data-secs="${secs}" title="${title}" ${busy ? "disabled" : ""}>${label}</button>`;
  return `
    <div class="card clip-card" data-clip="${c.id}">
      <div class="clip-media">
        ${busy ? '<div class="missing"><span class="spinner"></span>&nbsp;In lavorazione…</div>'
          : c.exists ? `<video src="/api/media/clip/${c.id}?v=${c.version || 1}" controls preload="metadata" playsinline></video>`
          : '<div class="missing">File non trovato: rigenera le clip</div>'}
        <div class="trim" aria-label="Modifica durata">
          <div class="trim-row"><span class="muted small">Inizio</span>
            ${trimBtn("start", "extend_sentence", 0, "◀ frase", "Aggiungi la frase precedente")}
            ${trimBtn("start", "nudge", -1, "◀ 1s", "Inizia 1 secondo prima")}
            ${trimBtn("start", "nudge", 1, "1s ▶", "Inizia 1 secondo dopo")}
            ${trimBtn("start", "shrink_sentence", 0, "frase ▶", "Togli la prima frase")}
          </div>
          <div class="trim-row"><span class="muted small">Fine</span>
            ${trimBtn("end", "shrink_sentence", 0, "◀ frase", "Togli l'ultima frase")}
            ${trimBtn("end", "nudge", -1, "◀ 1s", "Finisci 1 secondo prima")}
            ${trimBtn("end", "nudge", 1, "1s ▶", "Finisci 1 secondo dopo")}
            ${trimBtn("end", "extend_sentence", 0, "frase ▶", "Aggiungi la frase successiva")}
          </div>
          <button class="btn sm c-regen" style="width:100%;justify-content:center" ${busy ? "disabled" : ""}
            title="Sostituisce questa clip con un altro momento del video">↻ Rigenera questa clip</button>
        </div>
      </div>
      <div class="stack" style="gap:8px;min-width:0">
        <div class="row" style="gap:6px">
          <span class="badge accent">#${c.idx}</span>
          <span class="badge">${Math.round(c.score)} punti</span>
          <span class="muted small num">${fmtT(c.start)}–${fmtT(c.end)} · ${Math.round(c.end - c.start)}s</span>
          ${c.status === "errore" && c.error ? `<span class="badge bad" title="${esc(c.error)}">! ${esc(c.error.slice(0, 50))}</span>` : ""}
        </div>
        <input type="text" class="c-title" value="${esc(c.title)}" maxlength="100" aria-label="Titolo">
        <textarea class="c-desc" aria-label="Descrizione" placeholder="Descrizione e hashtag">${esc(c.description || "")}</textarea>
        ${c.hook ? `<div class="quote">${esc(c.hook)}</div>` : ""}
        <details class="fold small"><summary>Perché questo momento</summary><div class="muted" style="margin-top:4px">${esc(c.reason)}</div></details>
        <div class="row"><button class="btn sm c-save">Salva testi</button></div>
        <div class="upload-box">
          ${c.uploads.map(u => `<div class="row small" style="gap:6px">${statusBadge(u.status)}
              <span>${esc(u.channel_title || u.channel_id)}</span>
              ${u.youtube_id ? `<a href="https://youtube.com/shorts/${esc(u.youtube_id)}" target="_blank" rel="noopener">vedi ↗</a>` : ""}
              ${u.error ? `<span class="muted" title="${esc(u.error)}">${esc(u.error.slice(0, 60))}</span>` : ""}</div>`).join("")}
          ${connected.length ? `
            <div class="channel-checks">${connected.map(ch => `<label class="check small"><input type="checkbox" value="${esc(ch.channel_id)}" class="c-ch">${esc(ch.title)}</label>`).join("")}</div>
            <div class="row" style="flex-wrap:nowrap">
              <select class="c-privacy" style="width:auto">
                <option value="private">Privato</option><option value="unlisted">Non in elenco</option><option value="public">Pubblico</option>
              </select>
              <button class="btn primary sm c-upload" ${c.exists ? "" : "disabled"}>⬆ Carica su YouTube Shorts</button>
            </div>`
          : `<div class="muted small">Per caricare, collega un canale nella pagina <a href="#statistics">Statistics</a>.</div>`}
        </div>
      </div>
    </div>`;
}

function bindClipper(v, connected) {
  $("#g-run").onclick = async () => {
    const body = { n_clips: Number($("#g-n").value), min_len: Number($("#g-min").value), max_len: Number($("#g-max").value),
                   instructions: $("#g-instr").value };
    if (v.clips.length && !confirm("Rigenerare tutte le clip di questo video? Quelle attuali verranno sostituite.")) return;
    if (await run(() => api(`/api/clipper/videos/${v.id}/generate`, { method: "POST", body }), "Generazione avviata")) renderClipper();
  };
  $("#v-remove").onclick = async () => {
    if (!confirm("Rimuovere il video e le sue clip dal clipper? I file restano nella cartella jobs/.")) return;
    await run(() => api(`/api/clipper/videos/${v.id}`, { method: "DELETE" }));
    state.clipper.videoId = null;
    renderClipper();
    updateBadges();
  };
  const log = $("#clip-log");
  if (log) log.scrollTop = log.scrollHeight;
  $$("[data-clip]").forEach(card => {
    const id = card.dataset.clip;
    const texts = () => ({ title: $(".c-title", card).value, description: $(".c-desc", card).value });
    $(".c-save", card).onclick = () => run(() => api(`/api/clips/${id}`, { method: "PUT", body: texts() }), "Testi salvati");
    $$(".c-trim", card).forEach(b => b.onclick = async () => {
      const body = { edge: b.dataset.edge, action: b.dataset.action, seconds: Number(b.dataset.secs) };
      if (await run(() => api(`/api/clips/${id}/adjust`, { method: "POST", body }), "Riesporto la clip…")) renderClipper();
    });
    $(".c-regen", card).onclick = async () => {
      if (!confirm("Sostituire questa clip con un altro momento del video? Titolo e descrizione verranno riscritti.")) return;
      if (await run(() => api(`/api/clips/${id}/regenerate`, { method: "POST" }), "Cerco un nuovo momento…")) renderClipper();
    };
    const up = $(".c-upload", card);
    if (up) up.onclick = async () => {
      const channels = $$(".c-ch:checked", card).map(i => i.value);
      if (!channels.length) return toast("Seleziona almeno un canale", true);
      const privacy = $(".c-privacy", card).value;
      const names = connected.filter(c => channels.includes(c.channel_id)).map(c => c.title).join(", ");
      if (!confirm(`Caricare questa clip come ${privacy === "public" ? "PUBBLICA" : privacy === "unlisted" ? "non in elenco" : "privata"} su: ${names}?`)) return;
      await api(`/api/clips/${id}`, { method: "PUT", body: texts() });
      if (await run(() => api(`/api/clips/${id}/upload`, { method: "POST", body: { channels, privacy } }), "Caricamento avviato")) renderClipper();
    };
  });
}

// ---------------------------------------------------------------------------
// TRAINER
// ---------------------------------------------------------------------------

async function renderTrainer() {
  const shorts = await api("/api/trainer");
  const active = shorts.filter(s => s.active && s.status === "pronto").length;
  if (state.page !== "trainer") return;  // l'utente ha cambiato pagina durante il caricamento
  $("#page").innerHTML = `
    <div class="page-head">
      <div class="grow"><h1>Trainer</h1><div class="page-sub">Shorts di riferimento: l'agente copywriter ne studia hook, ritmo e temi quando sceglie le clip.</div></div>
      <span class="badge accent">${active} attivi come riferimento</span>
    </div>
    <div class="card stack" style="margin-bottom:18px">
      <label class="field">Link degli Shorts <span class="muted small">uno per riga</span>
        <textarea id="t-urls" rows="3" placeholder="https://youtube.com/shorts/…"></textarea>
      </label>
      <div class="row">
        <button class="btn primary" id="t-add">+ Aggiungi e analizza</button>
        <span class="muted small">Per ogni Short: metriche, trascrizione con Whisper${state.status.api_key ? " e analisi del copywriter" : ""}.</span>
      </div>
    </div>
    ${shorts.length ? `<div class="short-grid">${shorts.map(shortCard).join("")}</div>`
      : '<div class="card empty"><h3>Nessuno Short di riferimento</h3><p>Incolla i link di Shorts che funzionano nel tuo settore.</p></div>'}`;

  $("#t-add").onclick = async () => {
    const urls = $("#t-urls").value.split("\n").map(s => s.trim()).filter(Boolean);
    if (!urls.length) return toast("Incolla almeno un link", true);
    const r = await run(() => api("/api/trainer", { method: "POST", body: { urls } }));
    if (r) { toast(r.job_id ? "Analisi avviata" : "Questi link sono già presenti"); renderTrainer(); }
  };
  $$("[data-short]").forEach(card => {
    const id = card.dataset.short;
    $(".s-active", card).onchange = e => run(() => api(`/api/trainer/${id}`, { method: "PUT", body: { active: e.target.checked } }));
    $(".s-del", card).onclick = async () => { await run(() => api(`/api/trainer/${id}`, { method: "DELETE" })); renderTrainer(); };
    const retry = $(".s-retry", card);
    if (retry) retry.onclick = async () => { await run(() => api(`/api/trainer/${id}/retry`, { method: "POST" }), "Nuovo tentativo avviato"); renderTrainer(); };
  });
}

function shortCard(s) {
  const thumb = s.video_id ? `https://i.ytimg.com/vi/${encodeURIComponent(s.video_id)}/oar2.jpg` : null;
  return `
    <div class="card short-card" data-short="${s.id}">
      ${thumb ? `<img class="thumb" src="${esc(thumb)}" alt="" loading="lazy" onerror="this.src='${esc(s.thumb)}'">` : '<div class="thumb" style="width:96px;aspect-ratio:9/16"></div>'}
      <div style="min-width:0">
        <div class="row" style="gap:6px;margin-bottom:4px">${s.status !== "pronto" ? statusBadge(s.status) : ""}
          ${s.views != null ? `<span class="badge">${compact(s.views)} views</span>` : ""}
          ${s.likes != null && s.views ? `<span class="badge">${(100 * s.likes / s.views).toFixed(1).replace(".", ",")}% like</span>` : ""}
          ${s.duration ? `<span class="muted small">${s.duration}s</span>` : ""}</div>
        <a href="${esc(s.url)}" target="_blank" rel="noopener" class="video-title" style="color:var(--text)">${esc(s.title || s.url)}</a>
        ${s.channel ? `<div class="muted small">${esc(s.channel)}</div>` : ""}
        ${s.hook ? `<div class="quote">${esc(s.hook)}</div>` : ""}
        ${s.notes ? `<details class="fold small"><summary>Perché funziona</summary><div class="notes" style="margin-top:4px">${esc(s.notes)}</div></details>` : ""}
        ${s.transcript ? `<details class="fold small"><summary>Trascrizione</summary><div class="notes" style="margin-top:4px">${esc(s.transcript)}</div></details>` : ""}
        ${s.error ? `<div class="muted small" style="margin-top:4px">${esc(s.error.slice(0, 160))}</div>` : ""}
        <div class="row" style="margin-top:8px">
          <label class="check small"><input type="checkbox" class="s-active" ${s.active ? "checked" : ""}>Usa come riferimento</label>
          ${s.status === "errore" ? '<button class="btn sm s-retry">Riprova</button>' : ""}
          <button class="btn ghost sm danger s-del" style="margin-left:auto">Elimina</button>
        </div>
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// STATISTICS
// ---------------------------------------------------------------------------

async function renderStatistics() {
  const [channels, reports] = await Promise.all([api("/api/channels"), api("/api/reports")]);
  const st = state.stats;
  if (st.channelId !== "__report__" && !channels.find(c => c.channel_id === st.channelId)) st.channelId = channels[0]?.channel_id ?? null;
  const ch = channels.find(c => c.channel_id === st.channelId);
  const videos = ch ? await api(`/api/channels/${encodeURIComponent(ch.channel_id)}/videos`) : [];
  const report = st.channelId === "__report__" && st.reportId ? await api(`/api/reports/${st.reportId}`).catch(() => null) : null;

  if (state.page !== "statistics") return;  // l'utente ha cambiato pagina durante il caricamento
  $("#page").innerHTML = `
    <div class="page-head">
      <div class="grow"><h1>Statistics</h1><div class="page-sub">Statistiche per canale e per video. L'agente analista individua i contenuti che funzionano di più.</div></div>
      <button class="btn" id="s-refresh" ${channels.length ? "" : "disabled"}>↻ Aggiorna statistiche</button>
      <button class="btn primary" id="s-agent" ${channels.length ? "" : "disabled"}>✦ Analizza con l'agente</button>
    </div>
    <div class="grid-2">
      <div class="stack sticky">
        <div class="card stack">
          <h3 style="margin:0">Canali</h3>
          <div class="channel-list">
            ${channels.map(c => `
              <div class="channel-item ${c.channel_id === st.channelId ? "selected" : ""}" data-ch="${esc(c.channel_id)}">
                ${c.thumbnail ? `<img class="avatar" src="${esc(c.thumbnail)}" alt="">` : `<div class="avatar">${esc((c.title || "?")[0])}</div>`}
                <div style="min-width:0;flex:1">
                  <div style="font-weight:560;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(c.title)}</div>
                  <div class="muted small">${c.videos} video · ${compact(c.views)} views</div>
                </div>
                ${c.connected ? '<span class="badge good" title="Autorizzato: upload e retention ufficiale">✓ collegato</span>' : '<span class="badge">osservato</span>'}
              </div>`).join("") || '<div class="muted small">Nessun canale. Collega il tuo canale per caricare gli Shorts e vedere la retention ufficiale, oppure osserva un canale pubblico.</div>'}
          </div>
          <button class="btn primary" id="s-connect">+ Collega canale YouTube</button>
          <div class="row" style="flex-wrap:nowrap">
            <input type="text" id="s-handle" placeholder="@canale da osservare">
            <button class="btn sm" id="s-watch">Osserva</button>
          </div>
        </div>
        <div class="card stack">
          <h3 style="margin:0">Analisi dell'agente</h3>
          <div class="report-list">
            ${reports.map(r => `<div class="report-item ${st.channelId === "__report__" && r.id === st.reportId ? "selected" : ""}" data-report="${r.id}">
              <div style="font-weight:560">${fmtDate(r.created_at, true)} ${r.status !== "completato" ? statusBadge(r.status) : ""}</div>
              <div class="muted small">${esc(r.scope)}</div></div>`).join("") || '<div class="muted small">Nessuna analisi ancora.</div>'}
          </div>
        </div>
      </div>
      <div>${report ? reportView(report) : ch ? channelView(ch, videos) : `
        <div class="card empty"><h3>Nessun canale</h3><p>Aggiungi un canale dalla colonna a sinistra.</p></div>`}</div>
    </div>`;

  $$("[data-ch]").forEach(el => el.onclick = () => { st.channelId = el.dataset.ch; renderStatistics(); });
  $$("[data-report]").forEach(el => el.onclick = () => { st.channelId = "__report__"; st.reportId = Number(el.dataset.report); renderStatistics(); });
  $("#s-connect").onclick = () => {
    if (state.status.google_problem) { toast("Il client OAuth di Google va sistemato: vedi Impostazioni", true); return openSettings(); }
    if (!state.status.google_configured) { toast("Prima carica il file OAuth di Google nelle impostazioni", true); return openSettings(); }
    location.href = "/api/youtube/connect";
  };
  $("#s-watch").onclick = async () => {
    const handle = $("#s-handle").value.trim();
    if (!handle) return;
    const r = await run(() => api("/api/channels/watch", { method: "POST", body: { handle } }), "Canale aggiunto: scarico le statistiche…");
    if (r) { st.channelId = r.channel_id; renderStatistics(); }
  };
  $("#s-refresh").onclick = () => run(() => api("/api/channels/refresh", { method: "POST", body: {} }), "Aggiornamento avviato");
  $("#s-agent").onclick = async () => {
    const body = ch ? { channel_ids: [ch.channel_id] } : {};
    const r = await run(() => api("/api/reports", { method: "POST", body }), "L'agente sta analizzando le statistiche");
    if (r) renderStatistics();
  };
  const del = $("#s-del");
  if (del) del.onclick = async () => {
    if (!confirm(`Rimuovere "${ch.title}" e le sue statistiche? ${ch.connected ? "Il collegamento verrà revocato in questa app." : ""}`)) return;
    await run(() => api(`/api/channels/${encodeURIComponent(ch.channel_id)}`, { method: "DELETE" }));
    st.channelId = null;
    renderStatistics();
  };
  $$("th[data-sort]").forEach(th => th.onclick = () => { st.sort = th.dataset.sort; renderStatistics(); });
}

function channelView(ch, videos) {
  const st = state.stats;
  const median = arr => { const s = [...arr].sort((a, b) => a - b); return s.length ? s[Math.floor(s.length / 2)] : null; };
  const views = videos.map(v => v.views || 0);
  const ret = videos.filter(v => v.avg_view_pct != null);
  const official = ret.some(v => v.avg_view_pct_source === "youtube");
  const likeRates = videos.filter(v => v.views && v.likes != null).map(v => v.likes / v.views);
  const shorts = videos.filter(v => v.is_short).length;
  const sorters = {
    views: (a, b) => (b.views || 0) - (a.views || 0),
    published: (a, b) => String(b.published).localeCompare(String(a.published)),
    likes: (a, b) => (b.likes || 0) / (b.views || 1) - (a.likes || 0) / (a.views || 1),
    retention: (a, b) => (b.avg_view_pct ?? -1) - (a.avg_view_pct ?? -1),
    duration: (a, b) => (b.duration || 0) - (a.duration || 0),
  };
  const sorted = [...videos].sort(sorters[st.sort] || sorters.views);
  const th = (key, label, right = false) => `<th data-sort="${key}" class="${right ? "r" : ""} ${st.sort === key ? "th-sort" : ""}">${label}</th>`;

  return `
    <div class="row" style="margin-bottom:14px">
      <h2 style="margin:0;flex:1">${esc(ch.title)}</h2>
      ${ch.stats_updated_at ? `<span class="muted small">aggiornato ${fmtDate(ch.stats_updated_at, true)}</span>` : ""}
      <button class="btn ghost sm danger" id="s-del">Rimuovi</button>
    </div>
    <div class="tiles">
      <div class="tile"><div class="label">Video analizzati</div><div class="value num">${videos.length}</div><div class="sub">${shorts} Shorts · ${videos.length - shorts} lunghi</div></div>
      <div class="tile"><div class="label">Views totali</div><div class="value num">${compact(views.reduce((a, b) => a + b, 0))}</div><div class="sub">sui video elencati</div></div>
      <div class="tile"><div class="label">Views mediane</div><div class="value num">${compact(median(views))}</div><div class="sub">per video</div></div>
      <div class="tile"><div class="label">Retention media</div><div class="value num">${ret.length ? Math.round(ret.reduce((a, v) => a + v.avg_view_pct, 0) / ret.length) + "%" : "—"}</div>
        <div class="sub">${ret.length ? (official ? "dato ufficiale YouTube" : "stima dalla heatmap") : "non disponibile"}</div></div>
      <div class="tile"><div class="label">Like / views</div><div class="value num">${likeRates.length ? (100 * median(likeRates)).toFixed(1).replace(".", ",") + "%" : "—"}</div><div class="sub">mediana</div></div>
    </div>
    <div class="card" style="padding:0;overflow-x:auto">
      ${videos.length ? `
      <table class="data">
        <thead><tr><th></th><th>Video</th>${th("published", "Uscito")}${th("duration", "Durata", true)}${th("views", "Views", true)}${th("likes", "Like %", true)}${th("retention", "Retention", true)}<th>Clip di origine</th></tr></thead>
        <tbody>${sorted.map(v => `
          <tr>
            <td><img class="thumb" src="${esc(v.thumb)}" alt="" loading="lazy"></td>
            <td><a href="https://www.youtube.com/${v.is_short ? "shorts/" : "watch?v="}${esc(v.video_id)}" target="_blank" rel="noopener" style="color:var(--text);font-weight:560">${esc(v.title)}</a>
              <div class="muted small">${v.is_short ? "Short" : "Video lungo"}</div></td>
            <td class="muted">${fmtDate(v.published)}</td>
            <td class="r">${v.duration >= 60 ? Math.floor(v.duration / 60) + "′" + String(v.duration % 60).padStart(2, "0") : v.duration + "s"}</td>
            <td class="r"><b>${compact(v.views)}</b></td>
            <td class="r">${v.views && v.likes != null ? (100 * v.likes / v.views).toFixed(1).replace(".", ",") + "%" : "—"}</td>
            <td class="r">${v.avg_view_pct != null ? Math.round(v.avg_view_pct) + "%" + (v.avg_view_pct_source === "stima" ? ' <span class="muted small">stima</span>' : "") : "—"}</td>
            <td class="small">${v.source_title ? `${esc(v.source_title.slice(0, 40))}${v.clip_hook ? `<div class="muted">“${esc(v.clip_hook.slice(0, 50))}”</div>` : ""}` : '<span class="muted">—</span>'}</td>
          </tr>`).join("")}</tbody>
      </table>` : '<div class="empty"><h3>Nessun dato</h3><p>Premi <b>Aggiorna statistiche</b> per scaricare i video del canale.</p></div>'}
    </div>`;
}

function reportView(r) {
  return `
    <div class="card">
      <div class="row" style="margin-bottom:6px"><span class="muted small">${fmtDate(r.created_at, true)} · ${esc(r.scope)}</span>${statusBadge(r.status)}</div>
      <div class="report">${r.status === "completato" ? markdown(r.content)
        : r.status === "in_corso" ? "<p class='muted'>L'agente sta scrivendo l'analisi…</p>" : "<p>L'analisi non è andata a buon fine: vedi il riquadro lavori.</p>"}</div>
    </div>`;
}

// ---------------------------------------------------------------------------
// Lavori in background, stato, impostazioni
// ---------------------------------------------------------------------------

let jobTimer = null;
let lastActive = new Set();
const openLogs = new Set();
const dismissed = new Set();

async function pollJobs(immediate = false) {
  clearTimeout(jobTimer);
  if (!immediate) await new Promise(r => setTimeout(r, 0));
  let list = [];
  try { list = await api("/api/jobs"); } catch { /* server non raggiungibile: si riprova */ }
  const active = list.filter(j => j.status === "in_corso" || j.status === "in_coda");
  const recent = list.filter(j => !active.includes(j) && lastActive.has(j.id) || (!active.includes(j) && j.finished_at && Date.now() - new Date(j.finished_at) < 15000));
  const show = [...active, ...recent].filter(j => !dismissed.has(j.id)).slice(0, 4);

  $("#jobs").innerHTML = show.map(j => {
    const lines = (j.log || "").trim().split("\n");
    const last = lines[lines.length - 1] || (j.status === "in_coda" ? "in coda…" : "");
    return `<div class="job" data-job="${j.id}">
      <div class="job-head">${j.status === "in_corso" ? '<span class="spinner"></span>' : statusBadge(j.status)}
        <span class="label">${esc(j.label)}</span>
        <button class="btn ghost sm" data-log="${j.id}">${openLogs.has(j.id) ? "Nascondi" : "Log"}</button>
        ${active.includes(j) ? "" : `<button class="btn ghost sm" data-dismiss="${j.id}">✕</button>`}</div>
      ${openLogs.has(j.id) ? `<div class="log" style="margin-top:8px">${esc(j.log || "")}</div>` : `<div class="last">${esc(j.error || last)}</div>`}
    </div>`;
  }).join("");
  $$("[data-log]").forEach(b => b.onclick = () => { const id = Number(b.dataset.log); openLogs.has(id) ? openLogs.delete(id) : openLogs.add(id); pollJobs(true); });
  $$("[data-dismiss]").forEach(b => b.onclick = () => { dismissed.add(Number(b.dataset.dismiss)); pollJobs(true); });
  $$(".job .log").forEach(l => l.scrollTop = l.scrollHeight);

  const nowActive = new Set(active.map(j => j.id));
  const finished = [...lastActive].filter(id => !nowActive.has(id));
  lastActive = nowActive;
  if (finished.length) {
    const failed = list.filter(j => finished.includes(j.id) && j.status === "errore");
    failed.forEach(j => toast(`${j.label}: ${j.error || "errore"}`, true));
    const kinds = new Set(list.filter(j => finished.includes(j.id)).map(j => j.kind));
    if ((PAGE_JOBS[state.page] || []).some(k => kinds.has(k))) refreshKeepingScroll();
    updateBadges();
  } else if (state.page === "clipper" && active.some(j => j.kind === "clipper")) {
    const log = $("#clip-log");
    const job = list.find(j => j.kind === "clipper" && nowActive.has(j.id));
    if (log && job) { log.textContent = job.log; log.scrollTop = log.scrollHeight; }
  }
  loadStatus();
  jobTimer = setTimeout(pollJobs, active.length ? 2000 : 8000);
}

// Quali lavori, finendo, cambiano il contenuto di ogni pagina
const PAGE_JOBS = {
  analyst: ["analyst"], clipper: ["clipper", "upload"], trainer: ["trainer"], statistics: ["stats", "report"],
};
let pendingRefresh = false;

async function refreshKeepingScroll() {
  // Mai ridisegnare mentre l'utente sta scrivendo: riprova quando lascia il campo
  const focused = document.activeElement;
  const typing = focused && $("#page").contains(focused) && /^(INPUT|TEXTAREA|SELECT)$/.test(focused.tagName);
  const dirty = $$("#page textarea, #page input[type=text]").some(el => el.value !== el.defaultValue);
  if (typing || dirty) {
    if (!pendingRefresh) {
      pendingRefresh = true;
      toast("Dati aggiornati: la pagina si aggiornerà quando avrai finito di scrivere");
    }
    return;
  }
  pendingRefresh = false;
  const main = $(".main");
  const y = main.scrollTop;
  await refresh();
  main.scrollTop = y;
}

let loadedVersion = null;

async function loadStatus() {
  try { state.status = await api("/api/status"); } catch { return; }
  const s = state.status;
  // La pagina aperta è più vecchia dell'app sul server: chiedi di ricaricare
  if (loadedVersion === null) loadedVersion = s.ui_version;
  else if (s.ui_version !== loadedVersion && !$("#update-banner")) {
    const el = document.createElement("div");
    el.id = "update-banner";
    el.className = "toast";
    el.innerHTML = 'È disponibile una versione aggiornata di ClipBot Studio. <button class="btn sm" style="margin-left:8px">Ricarica</button>';
    $("button", el).onclick = () => location.reload();
    document.body.appendChild(el);
  }
  $("#dot-key").className = "dot " + (s.api_key ? "ok" : "off");
  $("#txt-key").textContent = s.api_key ? "Claude attivo" : "Claude non configurato";
  $("#dot-yt").className = "dot " + (s.connected_channels ? "ok" : "off");
  $("#txt-yt").textContent = s.connected_channels ? `${s.connected_channels} canali YouTube collegati` : "YouTube non collegato";
}

async function updateBadges() {
  try {
    const videos = await api("/api/clipper/videos");
    const b = $("#badge-clipper");
    b.hidden = !videos.length;
    b.textContent = videos.length;
  } catch { /* opzionale */ }
}

function openSettings() {
  const s = state.status;
  $("#set-key").innerHTML = (s.api_key
    ? '<span class="badge good">✓ attivo</span> Agente analista, copywriter e statistiche usano Claude. Per cambiare chiave incollane una nuova qui sotto.'
    : 'Incolla la tua chiave API di Anthropic (la trovi su <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener">console.anthropic.com → API keys</a>). Viene verificata e salvata nel file <code>.env</code> del progetto, solo su questo Mac.')
    + `<div class="row" style="margin-top:8px;flex-wrap:nowrap">
         <input type="password" id="api-key-input" placeholder="sk-ant-…" autocomplete="off" spellcheck="false">
         <button class="btn sm primary" id="api-key-save">Verifica e salva</button></div>`;
  $("#api-key-save").onclick = async () => {
    const input = $("#api-key-input");
    const key = input.value.trim();
    if (!key) return toast("Incolla prima la chiave", true);
    $("#api-key-save").disabled = true;
    const r = await run(() => api("/api/settings/anthropic-key", { method: "POST", body: { key } }), "Chiave verificata: Claude è attivo");
    input.value = "";
    await loadStatus();
    openSettings();
    if (r) refresh();
  };
  $("#set-yt").innerHTML = s.google_problem
    ? `<span class="badge bad">! da sistemare</span> ${esc(s.google_problem)}
       <div class="row" style="margin-top:8px;flex-wrap:nowrap"><input type="text" readonly value="${esc(s.redirect_uri)}" id="redirect-uri">
       <button class="btn sm" id="copy-redirect">Copia</button></div>
       <div class="row" style="margin-top:8px"><button class="btn sm" id="recheck-google">↻ Ho sistemato: verifica di nuovo</button></div>`
    : s.google_configured
    ? '<span class="badge good">✓ client OAuth caricato</span> Puoi collegare i canali dalla pagina Statistics.'
    : "Per caricare gli Shorts e leggere la retention ufficiale serve un client OAuth di Google:";
  const copy = $("#copy-redirect");
  if (copy) copy.onclick = () => navigator.clipboard.writeText(s.redirect_uri).then(() => toast("Indirizzo copiato"));
  const recheck = $("#recheck-google");
  if (recheck) recheck.onclick = async () => {
    recheck.disabled = true;
    const r = await run(() => api("/api/youtube/recheck", { method: "POST" }));
    await loadStatus();
    openSettings();
    if (r && !r.problem) toast("Google ora accetta il client: puoi collegare il canale da Statistics");
    else if (r) toast("Google rifiuta ancora il client: controlla l'URI e riprova tra qualche minuto", true);
  };
  $("#settings").showModal();
}

$("#secret-file").onchange = async e => {
  const file = e.target.files[0];
  if (!file) return;
  let data;
  try { data = JSON.parse(await file.text()); } catch { return toast("Il file non è un JSON valido", true); }
  // Il file viene salvato anche quando va sistemato: in quel caso il messaggio spiega cosa manca
  await run(() => api("/api/settings/google-secret", { method: "POST", body: data }), "Client OAuth salvato");
  await loadStatus();
  openSettings();
  e.target.value = "";
};
$("#open-settings").onclick = openSettings;
$("#close-settings").onclick = () => $("#settings").close();
$$(".nav-item").forEach(b => b.onclick = () => go(b.dataset.page));
window.addEventListener("hashchange", route);
// Aggiornamento rimandato: parte quando l'utente esce da un campo senza modifiche in sospeso
document.addEventListener("focusout", () => setTimeout(() => { if (pendingRefresh) refreshKeepingScroll(); }, 300));

(async () => {
  await loadStatus();
  route();
  updateBadges();
  pollJobs(true);
})();
