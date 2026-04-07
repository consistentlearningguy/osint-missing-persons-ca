/* ═══════════════════════════════════════════════════════════
   MERIDIAN — Missing Persons Intelligence Console
   Main Application Module
   ═══════════════════════════════════════════════════════════ */

// ─── Configuration ───
const CONFIG = {
  // Backend API — try localhost first, then common alternatives
  API_URLS: ["http://localhost:8000", "http://127.0.0.1:8000"],
  // ArcGIS direct feed for fallback
  ARCGIS_URL: "https://services.arcgis.com/Sv9ZXFjH5h1fYAaI/arcgis/rest/services/Missing_Children_Cases_View_Master/FeatureServer/0",
  // Bundled data fallback
  STATIC_URL: "./data/public-cases.json",
  // Map tiles
  TILE_URL: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
  TILE_ATTR: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
  // Canada center
  MAP_CENTER: [56.1, -96.0],
  MAP_ZOOM: 4,
};

const STATUS_LABELS = {
  missing: "Missing",
  vulnerable: "Vulnerable",
  abudction: "Abduction",
  abduction: "Abduction",
  amberalert: "Amber Alert",
  childsearchalert: "Child Search Alert",
};

const CONNECTORS = [
  { id: "official-artifacts", name: "Official Sources", icon: "🏛️" },
  { id: "canada-missing-xref", name: "Canada Missing XRef", icon: "🇨🇦" },
  { id: "google-news-rss", name: "Google News RSS", icon: "📰" },
  { id: "bing-news-rss", name: "Bing News RSS", icon: "📡" },
  { id: "duckduckgo-html", name: "DuckDuckGo", icon: "🦆" },
  { id: "reddit-search", name: "Reddit Search", icon: "💬" },
  { id: "wayback-machine", name: "Wayback Machine", icon: "🏛️" },
  { id: "gdelt-doc", name: "GDELT Doc API", icon: "🌐" },
];

// ─── State ───
const state = {
  apiBase: null,
  apiOnline: false,
  cases: [],
  filteredCases: [],
  selectedCaseId: null,
  selectedCase: null,
  selectedRunId: null,
  runs: [],
  leads: [],
  queryLogs: [],
  view: "overview", // "overview" | "dossier"
  maps: {},
};

// ─── DOM References ───
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

// ═══════════════════════════════════════════════════════════
// API LAYER
// ═══════════════════════════════════════════════════════════

async function detectApi() {
  for (const url of CONFIG.API_URLS) {
    try {
      const res = await fetch(`${url}/healthz`, { signal: AbortSignal.timeout(3000) });
      if (res.ok) {
        state.apiBase = url;
        state.apiOnline = true;
        return url;
      }
    } catch { /* try next */ }
  }
  state.apiOnline = false;
  return null;
}

async function api(path, opts = {}) {
  if (!state.apiBase) return null;
  try {
    const res = await fetch(`${state.apiBase}${path}`, {
      ...opts,
      headers: { "Content-Type": "application/json", ...opts.headers },
    });
    if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
    return await res.json();
  } catch (err) {
    console.error(`API error: ${path}`, err);
    return null;
  }
}

// ─── Load cases from API, ArcGIS, or static fallback ───
async function loadCases() {
  // Try API first
  if (state.apiOnline) {
    const data = await api("/api/cases");
    if (data?.cases?.length) {
      return data.cases.map((c) => ({ ...c, _source: "api" }));
    }
  }

  // Try ArcGIS direct
  try {
    const url = `${CONFIG.ARCGIS_URL}/query?where=1%3D1&outFields=*&f=json&resultRecordCount=500`;
    const res = await fetch(url, { signal: AbortSignal.timeout(10000) });
    const data = await res.json();
    if (data?.features?.length) {
      return data.features.map((f) => normalizeArcgis(f));
    }
  } catch { /* fall through */ }

  // Static fallback
  try {
    const res = await fetch(CONFIG.STATIC_URL);
    const data = await res.json();
    return (data.cases || []).map((c) => ({ ...c, _source: "static" }));
  } catch {
    return [];
  }
}

function normalizeArcgis(feature) {
  const a = feature.attributes || {};
  const missingSince = a.Date_Missing ? new Date(a.Date_Missing).toISOString() : null;
  return {
    id: a.OBJECTID || a.FID || Math.random(),
    name: [a.First_Name, a.Last_Name].filter(Boolean).join(" ") || a.First_Name || "Unknown",
    province: a.Province || "",
    city: a.City || "",
    status: (a.Status || "missing").toLowerCase().replace(/\s+/g, ""),
    age: a.Age != null ? Number(a.Age) : null,
    gender: a.Gender || null,
    missing_since: missingSince,
    latitude: a.Latitude || feature.geometry?.y || null,
    longitude: a.Longitude || feature.geometry?.x || null,
    photo_url: a.Photo_URL || a.PhotoURL || null,
    authority_name: a.Authority || a.Police_Agency || null,
    authority_phone: a.Authority_Phone || null,
    _source: "arcgis",
  };
}

// ═══════════════════════════════════════════════════════════
// UI RENDERING
// ═══════════════════════════════════════════════════════════

// ─── Case List ───
function renderCaseList() {
  const list = $("#caseList");
  if (!state.filteredCases.length) {
    list.innerHTML = '<div class="empty-state"><p>No cases match your filters.</p></div>';
    return;
  }

  list.innerHTML = state.filteredCases.map((c, i) => {
    const isSelected = c.id === state.selectedCaseId;
    const badge = statusBadge(c.status);
    const elapsed = c.missing_since ? elapsedText(c.missing_since) : "";
    const photoHtml = c.photo_url
      ? `<img class="case-card__photo" src="${escHtml(c.photo_url)}" alt="${escHtml(c.name)}" loading="lazy">`
      : `<div class="case-card__photo-placeholder">👤</div>`;

    return `
      <div class="case-card${isSelected ? " is-selected" : ""} animate-in"
           data-case-id="${c.id}" style="animation-delay:${Math.min(i * 20, 400)}ms">
        ${photoHtml}
        <div class="case-card__body">
          <div class="case-card__name">${escHtml(c.name)}</div>
          <div class="case-card__detail">
            <span class="case-card__location">${escHtml(c.city || "—")}${c.province ? ", " + escHtml(c.province) : ""}</span>
            ${badge}
          </div>
          ${elapsed ? `<div class="case-card__elapsed">${elapsed}</div>` : ""}
        </div>
      </div>`;
  }).join("");

  // Click handlers
  list.querySelectorAll(".case-card").forEach((card) => {
    card.addEventListener("click", () => {
      const id = Number(card.dataset.caseId);
      selectCase(id);
    });
  });
}

function statusBadge(status) {
  const s = (status || "").toLowerCase().replace(/\s+/g, "");
  const cls = {
    missing: "missing", vulnerable: "vulnerable",
    abduction: "abduction", abudction: "abduction",
    amberalert: "amberalert",
  }[s] || "default";
  const label = STATUS_LABELS[s] || status || "Unknown";
  return `<span class="case-card__badge case-card__badge--${cls}">${escHtml(label)}</span>`;
}

// ─── Overview Stats ───
function renderOverviewStats() {
  const cases = state.cases;
  const provinces = new Set(cases.map((c) => c.province).filter(Boolean));
  
  $("#statTotalCases").textContent = cases.length;
  $("#statProvinces").textContent = provinces.size;
  
  // Count investigated (requires API)
  if (state.apiOnline) {
    api("/api/cases/stats").then((data) => {
      if (data) {
        $("#statInvestigated").textContent = data.investigated_count || "—";
        $("#statTotalLeads").textContent = data.total_leads || "—";
      }
    });
  } else {
    $("#statInvestigated").textContent = "—";
    $("#statTotalLeads").textContent = "—";
  }
}

// ─── Province Chart ───
function renderProvinceChart() {
  const counts = {};
  state.cases.forEach((c) => {
    const p = c.province || "Unknown";
    counts[p] = (counts[p] || 0) + 1;
  });

  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const max = sorted[0]?.[1] || 1;

  const container = $("#provinceChart");
  container.innerHTML = sorted.slice(0, 10).map(([prov, count]) => {
    const pct = Math.round((count / max) * 100);
    return `
      <div class="province-bar">
        <span class="province-bar__label">${escHtml(prov)}</span>
        <div class="province-bar__track">
          <div class="province-bar__fill" style="width:${pct}%"></div>
        </div>
        <span class="province-bar__count">${count}</span>
      </div>`;
  }).join("");
}

// ─── Priority Cases ───
function renderPriorityCases() {
  // Priority = most recent + highest risk status
  const prioritized = [...state.cases]
    .filter((c) => c.missing_since)
    .sort((a, b) => {
      const statusWeight = { amberalert: 4, abduction: 3, abudction: 3, vulnerable: 2, missing: 1 };
      const wa = statusWeight[(a.status || "").toLowerCase()] || 0;
      const wb = statusWeight[(b.status || "").toLowerCase()] || 0;
      if (wb !== wa) return wb - wa;
      return new Date(b.missing_since) - new Date(a.missing_since);
    })
    .slice(0, 5);

  const container = $("#priorityCases");
  if (!prioritized.length) {
    container.innerHTML = '<p class="muted-text">No cases with dates available.</p>';
    return;
  }

  container.innerHTML = prioritized.map((c, i) => {
    const days = daysSince(c.missing_since);
    return `
      <div class="priority-card" data-case-id="${c.id}">
        <span class="priority-card__rank">${i + 1}</span>
        <div class="priority-card__info">
          <div class="priority-card__name">${escHtml(c.name)}</div>
          <div class="priority-card__meta">${escHtml(c.city || "")}${c.province ? ", " + escHtml(c.province) : ""}</div>
        </div>
        <span class="priority-card__days">${days}d</span>
      </div>`;
  }).join("");

  container.querySelectorAll(".priority-card").forEach((card) => {
    card.addEventListener("click", () => selectCase(Number(card.dataset.caseId)));
  });
}

// ─── Connector Health ───
function renderConnectorGrid() {
  const container = $("#connectorGrid");
  container.innerHTML = CONNECTORS.map((c) => `
    <div class="connector-card">
      <span class="connector-card__dot connector-card__dot--ok"></span>
      <span class="connector-card__name">${c.icon} ${c.name}</span>
      <span class="connector-card__status">Ready</span>
    </div>`).join("");
}

// ─── Connector status dots in footer ───
function renderConnectorDots() {
  const container = $("#connectorDots");
  container.innerHTML = CONNECTORS.map((c) =>
    `<span class="connector-dot connector-dot--ok" title="${c.name}"></span>`
  ).join("");
}

// ═══════════════════════════════════════════════════════════
// CASE DOSSIER
// ═══════════════════════════════════════════════════════════

async function selectCase(caseId) {
  state.selectedCaseId = caseId;
  state.selectedCase = state.cases.find((c) => c.id === caseId);
  state.selectedRunId = null;

  // Highlight in list
  $$(".case-card").forEach((el) => el.classList.toggle("is-selected", Number(el.dataset.caseId) === caseId));

  // Switch view
  showView("dossier");

  // Render dossier header
  renderDossierHeader();

  // Load investigation data if API online
  if (state.apiOnline) {
    loadCaseRuns(caseId);
    
    // Try to get full case data from API
    const fullCase = await api(`/api/cases/${caseId}`);
    if (fullCase) {
      state.selectedCase = { ...state.selectedCase, ...fullCase };
    }
  }

  // Render facts
  renderFacts();
  
  // Update intel panel map
  renderIntelMap();

  // Update authority quick action
  updateQuickActions();
  
  // Switch to facts tab
  switchTab("facts");
}

function renderDossierHeader() {
  const c = state.selectedCase;
  if (!c) return;

  // Photo
  const photoEl = $("#dossierPhoto");
  photoEl.innerHTML = c.photo_url
    ? `<img src="${escHtml(c.photo_url)}" alt="${escHtml(c.name)}">`
    : '<div style="display:flex;align-items:center;justify-content:center;height:100%;font-size:24px;color:var(--text-muted)">👤</div>';

  // Status badge
  const statusEl = $("#dossierStatus");
  const s = (c.status || "missing").toLowerCase();
  const colors = {
    missing: ["var(--amber-dim)", "var(--amber)"],
    vulnerable: ["var(--red-dim)", "var(--red)"],
    abduction: ["var(--red-dim)", "var(--red-bright)"],
    abudction: ["var(--red-dim)", "var(--red-bright)"],
    amberalert: ["rgba(248,113,113,0.25)", "#fff"],
  };
  const [bg, fg] = colors[s] || ["var(--blue-dim)", "var(--blue)"];
  statusEl.style.background = bg;
  statusEl.style.color = fg;
  statusEl.textContent = STATUS_LABELS[s] || c.status || "Unknown";

  // Name & location
  $("#dossierName").textContent = c.name || "Unknown";
  const locationParts = [c.city, c.province].filter(Boolean);
  const location = locationParts.length ? locationParts.join(", ") : "Location unknown";
  const dateStr = c.missing_since ? `Missing since ${formatDate(c.missing_since)}` : "";
  $("#dossierLocation").textContent = [location, dateStr].filter(Boolean).join(" · ");
}

function renderFacts() {
  const c = state.selectedCase;
  if (!c) return;
  const grid = $("#factsGrid");
  
  const facts = [];

  // Official facts
  if (c.name) facts.push({ label: "Full Name", value: c.name, type: "official" });
  if (c.age != null) facts.push({ label: "Age at Disappearance", value: String(c.age), type: "official" });
  if (c.gender) facts.push({ label: "Gender", value: c.gender, type: "official" });
  if (c.city) facts.push({ label: "City", value: c.city, type: "official" });
  if (c.province) facts.push({ label: "Province / Territory", value: c.province, type: "official" });
  if (c.missing_since) {
    const days = daysSince(c.missing_since);
    facts.push({
      label: "Missing Since",
      value: `${formatDate(c.missing_since)} (${days} days)`,
      type: "official",
    });
  }
  if (c.latitude && c.longitude) {
    facts.push({
      label: "Coordinates",
      value: `${Number(c.latitude).toFixed(4)}°N, ${Number(c.longitude).toFixed(4)}°W`,
      type: "official",
    });
  }

  // Contact
  if (c.authority_name) facts.push({ label: "Investigating Authority", value: c.authority_name, type: "contact" });
  if (c.authority_phone) facts.push({ label: "Authority Phone", value: c.authority_phone, type: "contact" });
  if (c.authority_email) facts.push({
    label: "Authority Email",
    value: `<a href="mailto:${escHtml(c.authority_email)}">${escHtml(c.authority_email)}</a>`,
    type: "contact",
    raw: true,
  });
  if (c.authority_case_url) facts.push({
    label: "Official Case Page",
    value: `<a href="${escHtml(c.authority_case_url)}" target="_blank" rel="noopener" title="${escHtml(c.authority_case_url)}">${escHtml(truncateUrl(c.authority_case_url))}</a>`,
    type: "contact",
    raw: true,
  });
  if (c.mcsc_phone) facts.push({ label: "MCSC Phone", value: c.mcsc_phone, type: "contact" });

  // Summary
  if (c.official_summary_html) {
    facts.push({ label: "Official Summary", value: sanitizeHtml(c.official_summary_html), type: "summary", raw: true });
  }

  grid.innerHTML = facts.map((f) => `
    <div class="fact-card fact-card--${f.type}">
      <div class="fact-card__label">${escHtml(f.label)}</div>
      <div class="fact-card__value">${f.raw ? f.value : escHtml(f.value)}</div>
    </div>`).join("");
}

// ─── Investigation Runs ───
async function loadCaseRuns(caseId) {
  const data = await api(`/api/investigations/cases/${caseId}/runs`);
  state.runs = data?.runs || [];
  renderRunHistory();

  // Auto-select latest run
  if (state.runs.length) {
    selectRun(state.runs[0].id);
  } else {
    state.leads = [];
    state.queryLogs = [];
    renderLeads();
    renderQueryLog();
    $("#leadCountBadge").textContent = "0";
  }
}

function renderRunHistory() {
  const container = $("#runHistory");
  if (!state.runs.length) {
    container.innerHTML = '<p class="muted-text">No investigations yet. Click "Run Investigation" to start.</p>';
    return;
  }

  container.innerHTML = state.runs.map((r) => {
    const date = formatDateTime(r.started_at);
    const isSelected = r.id === state.selectedRunId;
    return `
      <div class="run-item${isSelected ? " is-selected" : ""}" data-run-id="${r.id}">
        <span class="run-item__id">Run #${r.id}</span>
        <span class="run-item__info">${date}</span>
        <span class="run-item__leads">${r.lead_count || 0} leads</span>
      </div>`;
  }).join("");

  container.querySelectorAll(".run-item").forEach((el) => {
    el.addEventListener("click", () => selectRun(Number(el.dataset.runId)));
  });
}

async function selectRun(runId) {
  state.selectedRunId = runId;

  // Highlight
  $$(".run-item").forEach((el) => el.classList.toggle("is-selected", Number(el.dataset.runId) === runId));

  // Load leads + query logs in parallel
  const [leadsData, queryData] = await Promise.all([
    api(`/api/investigations/runs/${runId}/leads?limit=200`),
    api(`/api/investigations/runs/${runId}/query-logs`),
  ]);

  state.leads = leadsData?.leads || [];
  state.queryLogs = queryData?.query_logs || [];

  $("#leadCountBadge").textContent = String(state.leads.length);
  
  renderLeads();
  renderQueryLog();
  populateLeadSourceFilter();
  renderTimeline();
}

// ─── Leads ───
function renderLeads() {
  const container = $("#leadsList");
  const minConf = parseFloat($("#leadFilterConfidence").value) || 0;
  const sourceFilter = $("#leadFilterSource").value;
  const reviewFilter = $("#leadFilterReview").value;

  let filtered = state.leads.filter((l) => l.confidence >= minConf);
  if (sourceFilter) filtered = filtered.filter((l) => l.source_name === sourceFilter);
  if (reviewFilter) filtered = filtered.filter((l) => l.review_status === reviewFilter);

  const summary = $("#leadsSummary");
  summary.textContent = `${filtered.length} of ${state.leads.length} leads`;

  if (!filtered.length) {
    container.innerHTML = '<div class="empty-state"><p>No leads match current filters.</p></div>';
    return;
  }

  container.innerHTML = filtered.map((l, i) => {
    const conf = l.confidence || 0;
    const tier = conf >= 0.6 ? "high" : conf >= 0.3 ? "medium" : "low";
    const color = conf >= 0.6 ? "var(--green)" : conf >= 0.3 ? "var(--amber)" : "var(--text-muted)";
    const circumference = 2 * Math.PI * 22;
    const dashOffset = circumference - (conf * circumference);

    const reviewed = l.reviewed;
    const approveActive = l.review_status === "credible" ? " is-active--approve" : "";
    const rejectActive = l.review_status === "not-relevant" ? " is-active--reject" : "";

    return `
      <div class="lead-card lead-card--${tier} animate-in" style="animation-delay:${Math.min(i * 30, 600)}ms" data-lead-id="${l.id}">
        <div class="confidence-meter">
          <svg class="confidence-meter__ring" viewBox="0 0 48 48">
            <circle class="confidence-meter__bg" cx="24" cy="24" r="22"/>
            <circle class="confidence-meter__fill" cx="24" cy="24" r="22"
              stroke="${color}"
              stroke-dasharray="${circumference}"
              stroke-dashoffset="${dashOffset}"/>
          </svg>
          <span class="confidence-meter__value" style="color:${color}">${(conf * 100).toFixed(0)}</span>
        </div>
        <div class="lead-card__body">
          <div class="lead-card__title">${escHtml(l.title || "Untitled Lead")}</div>
          <div class="lead-card__source">
            ${escHtml(l.source_name || "")} · 
            ${l.source_url ? `<a href="${escHtml(l.source_url)}" target="_blank" rel="noopener">View source ↗</a>` : "No URL"}
            ${l.published_at ? " · " + formatDate(l.published_at) : ""}
          </div>
          ${l.summary ? `<div class="lead-card__excerpt">${escHtml(l.summary)}</div>` : ""}
          ${l.content_excerpt ? `<div class="lead-card__excerpt">${escHtml(l.content_excerpt)}</div>` : ""}
          <div class="lead-card__tags">
            ${l.category ? `<span class="lead-tag lead-tag--category">${escHtml(l.category)}</span>` : ""}
            ${l.source_kind ? `<span class="lead-tag lead-tag--source">${escHtml(l.source_kind)}</span>` : ""}
            ${l.location_text ? `<span class="lead-tag">${escHtml(l.location_text)}</span>` : ""}
            ${l.corroboration_count > 1 ? `<span class="lead-tag">×${l.corroboration_count} corroboration</span>` : ""}
          </div>
        </div>
        <div class="lead-card__actions">
          <button class="review-btn review-btn--approve${approveActive}" title="Mark credible" data-action="credible" data-lead-id="${l.id}">✓</button>
          <button class="review-btn review-btn--reject${rejectActive}" title="Not relevant" data-action="not-relevant" data-lead-id="${l.id}">✕</button>
          <button class="review-btn review-btn--flag" title="Flag for review" data-action="flag" data-lead-id="${l.id}">⚑</button>
        </div>
      </div>`;
  }).join("");

  // Review button handlers
  container.querySelectorAll(".review-btn").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const leadId = Number(btn.dataset.leadId);
      const action = btn.dataset.action;
      await reviewLead(leadId, action);
    });
  });
}

function populateLeadSourceFilter() {
  const select = $("#leadFilterSource");
  const sources = [...new Set(state.leads.map((l) => l.source_name).filter(Boolean))].sort();
  const current = select.value;
  select.innerHTML = '<option value="">All Sources</option>' +
    sources.map((s) => `<option value="${escHtml(s)}">${escHtml(s)}</option>`).join("");
  select.value = current;
}

async function reviewLead(leadId, decision) {
  if (!state.apiOnline) {
    showToast("Backend required for lead review", "warning");
    return;
  }
  const data = await api(`/api/investigations/leads/${leadId}/review`, {
    method: "POST",
    body: JSON.stringify({ decision, notes: null }),
  });
  if (data) {
    // Update local state
    const lead = state.leads.find((l) => l.id === leadId);
    if (lead) {
      lead.reviewed = true;
      lead.review_status = decision;
    }
    renderLeads();
    showToast(`Lead marked as ${decision}`, "success");
  }
}

// ─── Query Log ───
function renderQueryLog() {
  const container = $("#queryLog");
  if (!state.queryLogs.length) {
    container.innerHTML = '<div class="empty-state"><p>No query logs for this run.</p></div>';
    return;
  }

  container.innerHTML = `
    <table class="query-log-table">
      <colgroup>
        <col><col><col><col><col>
      </colgroup>
      <thead>
        <tr>
          <th>Connector</th>
          <th>Query</th>
          <th>Status</th>
          <th>Results</th>
          <th>Time</th>
        </tr>
      </thead>
      <tbody>
        ${state.queryLogs.map((q) => {
          const statusCls = q.status === "ok" || q.status === "completed" ? "status--ok"
            : q.status === "failed" ? "status--fail" : "status--warn";
          return `
            <tr>
              <td class="mono">${escHtml(q.connector_name || "")}</td>
              <td>${escHtml(q.query_used || "")}</td>
              <td class="${statusCls} mono">${escHtml(q.status || "—")}</td>
              <td class="mono">${q.result_count ?? "—"}</td>
              <td class="mono">${q.completed_at ? formatTime(q.completed_at) : "—"}</td>
            </tr>`;
        }).join("")}
      </tbody>
    </table>`;
}

// ─── Timeline ───
function renderTimeline() {
  const container = $("#timelineView");
  const c = state.selectedCase;
  
  const entries = [];

  // Official dates
  if (c?.missing_since) {
    entries.push({
      date: c.missing_since,
      title: `${c.name} reported missing`,
      desc: `${c.city || ""}${c.province ? ", " + c.province : ""}`,
      type: "official",
    });
  }

  // Lead dates
  state.leads.forEach((l) => {
    if (l.published_at) {
      entries.push({
        date: l.published_at,
        title: l.title || "Lead found",
        desc: `Source: ${l.source_name || "Unknown"} · Confidence: ${((l.confidence || 0) * 100).toFixed(0)}%`,
        type: l.confidence >= 0.6 ? "official" : l.category?.includes("news") ? "news" : "lead",
      });
    }
  });

  // Sort by date desc
  entries.sort((a, b) => new Date(b.date) - new Date(a.date));

  if (!entries.length) {
    container.innerHTML = '<div class="empty-state"><p>No timeline data available. Run an investigation to populate.</p></div>';
    return;
  }

  container.innerHTML = entries.slice(0, 50).map((e) => `
    <div class="timeline-entry">
      <div class="timeline-entry__dot timeline-entry__dot--${e.type}"></div>
      <div class="timeline-entry__content">
        <div class="timeline-entry__date">${formatDateTime(e.date)}</div>
        <div class="timeline-entry__title">${escHtml(e.title)}</div>
        ${e.desc ? `<div class="timeline-entry__desc">${escHtml(e.desc)}</div>` : ""}
      </div>
    </div>`).join("");
}

// ═══════════════════════════════════════════════════════════
// MAPS
// ═══════════════════════════════════════════════════════════

function initOverviewMap() {
  const container = $("#overviewMap");
  if (!container || state.maps.overview) return;
  
  const map = L.map(container, {
    zoomControl: false,
    attributionControl: false,
  }).setView(CONFIG.MAP_CENTER, CONFIG.MAP_ZOOM);
  
  L.tileLayer(CONFIG.TILE_URL, { attribution: CONFIG.TILE_ATTR }).addTo(map);
  L.control.zoom({ position: "topright" }).addTo(map);
  state.maps.overview = map;

  // Add case markers
  setTimeout(() => updateOverviewMapMarkers(), 100);
}

function updateOverviewMapMarkers() {
  const map = state.maps.overview;
  if (!map) return;

  // Clear existing
  if (state.maps.overviewMarkers) {
    state.maps.overviewMarkers.forEach((m) => map.removeLayer(m));
  }
  state.maps.overviewMarkers = [];

  const bounds = [];
  state.cases.forEach((c) => {
    if (c.latitude && c.longitude) {
      const marker = L.circleMarker([c.latitude, c.longitude], {
        radius: 5,
        fillColor: "#f5a623",
        fillOpacity: 0.6,
        color: "#f5a623",
        weight: 1,
        opacity: 0.8,
      }).addTo(map);
      
      marker.bindPopup(`
        <div style="font-family:var(--font-body);min-width:160px">
          <strong>${escHtml(c.name)}</strong><br>
          <span style="font-size:12px;opacity:0.8">${escHtml(c.city || "")}${c.province ? ", " + escHtml(c.province) : ""}</span>
        </div>
      `);
      
      marker.on("click", () => selectCase(c.id));
      state.maps.overviewMarkers.push(marker);
      bounds.push([c.latitude, c.longitude]);
    }
  });

  if (bounds.length) {
    map.fitBounds(bounds, { padding: [30, 30], maxZoom: 6 });
  }
}

function renderIntelMap() {
  const container = $("#intelMap");
  const c = state.selectedCase;

  // Destroy previous
  if (state.maps.intel) {
    state.maps.intel.remove();
    state.maps.intel = null;
  }

  if (!c) return;

  const lat = c.latitude || 56.1;
  const lng = c.longitude || -96.0;
  const zoom = c.latitude ? 10 : 4;

  const map = L.map(container, {
    zoomControl: false,
    attributionControl: false,
  }).setView([lat, lng], zoom);

  L.tileLayer(CONFIG.TILE_URL, { attribution: CONFIG.TILE_ATTR }).addTo(map);
  state.maps.intel = map;

  if (c.latitude && c.longitude) {
    L.circleMarker([c.latitude, c.longitude], {
      radius: 8,
      fillColor: "#f87171",
      fillOpacity: 0.7,
      color: "#f87171",
      weight: 2,
    }).addTo(map).bindPopup(`<strong>${escHtml(c.name)}</strong><br>Last seen location`);
  }
}

function renderEvidenceMap() {
  const container = $("#evidenceMap");
  const c = state.selectedCase;

  // Destroy previous
  if (state.maps.evidence) {
    state.maps.evidence.remove();
    state.maps.evidence = null;
  }

  const lat = c?.latitude || 56.1;
  const lng = c?.longitude || -96.0;
  const zoom = c?.latitude ? 8 : 4;

  const map = L.map(container, {
    attributionControl: false,
  }).setView([lat, lng], zoom);

  L.tileLayer(CONFIG.TILE_URL, { attribution: CONFIG.TILE_ATTR }).addTo(map);
  state.maps.evidence = map;

  // Case marker
  if (c?.latitude && c?.longitude) {
    L.circleMarker([c.latitude, c.longitude], {
      radius: 10,
      fillColor: "#f87171",
      fillOpacity: 0.8,
      color: "#fff",
      weight: 2,
    }).addTo(map).bindPopup(`<strong>${escHtml(c.name)}</strong><br>Last seen location`).openPopup();
  }

  // Lead markers
  const bounds = [];
  if (c?.latitude && c?.longitude) bounds.push([c.latitude, c.longitude]);

  state.leads.forEach((l) => {
    if (l.latitude && l.longitude) {
      const conf = l.confidence || 0;
      const color = conf >= 0.6 ? "#4ade80" : conf >= 0.3 ? "#f5a623" : "#64748b";
      
      L.circleMarker([l.latitude, l.longitude], {
        radius: 6,
        fillColor: color,
        fillOpacity: 0.7,
        color: color,
        weight: 1,
      }).addTo(map).bindPopup(`
        <div style="min-width:180px;font-family:var(--font-body)">
          <strong>${escHtml(l.title || "Lead")}</strong><br>
          <span style="font-size:11px;opacity:0.8">${escHtml(l.source_name || "")} · ${((conf) * 100).toFixed(0)}% confidence</span>
        </div>
      `);
      bounds.push([l.latitude, l.longitude]);
    }
  });

  if (bounds.length > 1) {
    map.fitBounds(bounds, { padding: [40, 40] });
  }
}

// ═══════════════════════════════════════════════════════════
// INVESTIGATION ACTIONS
// ═══════════════════════════════════════════════════════════

async function runInvestigation() {
  if (!state.apiOnline) {
    showToast("Backend must be running to investigate", "error");
    return;
  }
  if (!state.selectedCaseId) return;

  const btn = $("#runInvestigationBtn");
  btn.classList.add("is-running");
  btn.innerHTML = `
    <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M15.312 11.424a5.5 5.5 0 01-9.379 2.624l-1.06 1.06a7 7 0 0011.558-3.534l.03-.15-1.149-.15v.15zm-10.624-2.85a5.5 5.5 0 019.38-2.623l1.06-1.06A7 7 0 003.57 8.424l-.03.15 1.15.15v-.15z"/></svg>
    Investigating…`;
  
  showToast("Investigation started. OSINT connectors are running…", "info");

  const data = await api(`/api/investigations/${state.selectedCaseId}`, { method: "POST" });
  
  btn.classList.remove("is-running");
  btn.innerHTML = `
    <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z"/></svg>
    Run Investigation`;

  if (data) {
    showToast(`Investigation complete! Run #${data.run_id} — ${data.connectors?.length || 0} connectors used`, "success");
    // Reload runs
    await loadCaseRuns(state.selectedCaseId);
  } else {
    showToast("Investigation failed. Check if the backend is running.", "error");
  }
}

async function syncCases() {
  if (!state.apiOnline) {
    showToast("Backend required for sync", "warning");
    return;
  }

  const btn = $("#syncBtn");
  btn.classList.add("is-loading");
  showToast("Syncing cases from MCSC ArcGIS…", "info");

  const data = await api("/api/sync/cases", { method: "POST" });
  
  btn.classList.remove("is-loading");

  if (data) {
    showToast(`Sync complete! ${data.synced || data.total || "?"} cases updated.`, "success");
    // Reload cases
    await initCaseData();
  } else {
    showToast("Sync failed", "error");
  }
}

// ═══════════════════════════════════════════════════════════
// VIEW NAVIGATION
// ═══════════════════════════════════════════════════════════

function showView(name) {
  state.view = name;
  $$(".view").forEach((v) => v.classList.toggle("is-active", v.id === `view${capitalize(name)}`));
}

function switchTab(tabName) {
  $$(".tab-btn").forEach((t) => t.classList.toggle("is-active", t.dataset.tab === tabName));
  $$(".tab-content").forEach((t) => t.classList.toggle("is-active", t.dataset.tabContent === tabName));

  // Lazy init maps when switching to map tab
  if (tabName === "map") {
    setTimeout(() => renderEvidenceMap(), 100);
  }
}

// ═══════════════════════════════════════════════════════════
// FILTERING & SEARCH
// ═══════════════════════════════════════════════════════════

function filterCases() {
  const search = ($("#globalSearch").value || "").toLowerCase().trim();
  const province = $("#filterProvince").value;
  const status = $("#filterStatus").value;
  const sort = $("#filterSort").value;

  let filtered = [...state.cases];

  if (search) {
    filtered = filtered.filter((c) => {
      const text = [c.name, c.city, c.province, c.status].filter(Boolean).join(" ").toLowerCase();
      return text.includes(search);
    });
  }
  if (province) {
    filtered = filtered.filter((c) => c.province === province);
  }
  if (status) {
    filtered = filtered.filter((c) => c.status === status);
  }

  // Sort
  filtered.sort((a, b) => {
    switch (sort) {
      case "name": return (a.name || "").localeCompare(b.name || "");
      case "province": return (a.province || "").localeCompare(b.province || "");
      case "age-asc": return (a.age || 99) - (b.age || 99);
      case "recent":
      default: return new Date(b.missing_since || 0) - new Date(a.missing_since || 0);
    }
  });

  state.filteredCases = filtered;
  $("#caseCount").textContent = String(filtered.length);
  renderCaseList();
}

function populateProvinceFilter() {
  const select = $("#filterProvince");
  const provinces = [...new Set(state.cases.map((c) => c.province).filter(Boolean))].sort();
  select.innerHTML = '<option value="">All Provinces</option>' +
    provinces.map((p) => `<option value="${escHtml(p)}">${escHtml(p)}</option>`).join("");
}

// ═══════════════════════════════════════════════════════════
// TOASTS & MODALS
// ═══════════════════════════════════════════════════════════

function showToast(message, type = "info") {
  const container = $("#toastContainer");
  const icons = { success: "✓", error: "✕", warning: "⚠", info: "ℹ" };
  
  const toast = document.createElement("div");
  toast.className = `toast toast--${type}`;
  toast.innerHTML = `
    <span class="toast__icon">${icons[type] || "ℹ"}</span>
    <span class="toast__text">${escHtml(message)}</span>
    <button class="toast__close" onclick="this.parentElement.remove()">×</button>`;
  
  container.appendChild(toast);
  
  setTimeout(() => {
    toast.classList.add("is-exiting");
    setTimeout(() => toast.remove(), 300);
  }, 5000);
}

function showModal(html) {
  $("#modalBody").innerHTML = html;
  $("#modalOverlay").classList.add("is-open");
}

function closeModal() {
  $("#modalOverlay").classList.remove("is-open");
}

// ═══════════════════════════════════════════════════════════
// UTILITIES
// ═══════════════════════════════════════════════════════════

function escHtml(str) {
  if (!str) return "";
  const div = document.createElement("div");
  div.textContent = String(str);
  return div.innerHTML;
}

function sanitizeHtml(value) {
  const ALLOWED_TAGS = new Set(["p", "br", "strong", "em", "b", "i", "ul", "ol", "li", "a", "span", "div"]);
  const ALLOWED_ATTRS = { a: new Set(["href", "target", "rel"]) };
  const tmp = document.createElement("div");
  tmp.innerHTML = String(value || "");
  tmp.querySelectorAll("script,style,iframe,object,embed,form,input,textarea,link,meta,base,svg,math").forEach(
    (el) => el.remove()
  );
  tmp.querySelectorAll("*").forEach((el) => {
    const tag = el.tagName.toLowerCase();
    if (!ALLOWED_TAGS.has(tag)) {
      el.replaceWith(...el.childNodes);
      return;
    }
    for (const attr of [...el.attributes]) {
      const name = attr.name.toLowerCase();
      if (name.startsWith("on") || name === "style" || name === "class" || name === "id") {
        el.removeAttribute(attr.name);
      } else if (!(ALLOWED_ATTRS[tag] || new Set()).has(name)) {
        el.removeAttribute(attr.name);
      }
    }
    if (tag === "a") {
      const href = el.getAttribute("href") || "";
      if (!/^https?:\/\//i.test(href) && !href.startsWith("mailto:")) {
        el.removeAttribute("href");
      }
      el.setAttribute("target", "_blank");
      el.setAttribute("rel", "noopener noreferrer");
    }
  });
  return tmp.innerHTML;
}

function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("en-CA", { year: "numeric", month: "short", day: "numeric" });
  } catch { return String(iso).slice(0, 10); }
}

function formatDateTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-CA", {
      year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return String(iso).slice(0, 16); }
}

function formatTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString("en-CA", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch { return "—"; }
}

function daysSince(iso) {
  if (!iso) return 0;
  const diff = Date.now() - new Date(iso).getTime();
  return Math.max(0, Math.floor(diff / 86400000));
}

function elapsedText(iso) {
  const d = daysSince(iso);
  if (d === 0) return "Today";
  if (d === 1) return "1 day ago";
  if (d < 30) return `${d} days ago`;
  if (d < 365) {
    const m = Math.floor(d / 30);
    return `${m} month${m > 1 ? "s" : ""} ago`;
  }
  const y = Math.floor(d / 365);
  return `${y} year${y > 1 ? "s" : ""} ago`;
}

function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

function truncateUrl(url, maxLen = 50) {
  if (!url || url.length <= maxLen) return url;
  try {
    const u = new URL(url);
    const host = u.hostname.replace(/^www\./, "");
    const path = u.pathname;
    const available = maxLen - host.length - 3; // 3 for "…"
    if (available <= 0) return host + "…";
    if (path.length <= available) return host + path;
    return host + path.slice(0, available) + "…";
  } catch { return url.slice(0, maxLen) + "…"; }
}

function updateQuickActions() {
  const c = state.selectedCase;
  const link = $("#qaReportAuthority");
  if (c?.authority_phone) {
    link.href = `tel:${c.authority_phone}`;
    link.innerHTML = `<span class="qa-link__icon">📞</span> Call ${escHtml(c.authority_name || "Authority")}`;
  } else {
    link.href = "#";
    link.innerHTML = '<span class="qa-link__icon">📞</span> Report to Authority';
  }
}

function updateClock() {
  const el = $("#systemClock");
  if (el) {
    el.textContent = new Date().toLocaleTimeString("en-CA", {
      hour: "2-digit", minute: "2-digit",
    });
  }
}

// ═══════════════════════════════════════════════════════════
// INITIALIZATION
// ═══════════════════════════════════════════════════════════

async function initCaseData() {
  state.cases = await loadCases();
  state.filteredCases = [...state.cases];

  populateProvinceFilter();
  filterCases();
  renderOverviewStats();
  renderProvinceChart();
  renderPriorityCases();
  renderConnectorGrid();
  renderConnectorDots();
  updateOverviewMapMarkers();
}

async function boot() {
  // Clock
  updateClock();
  setInterval(updateClock, 30000);

  // Detect backend
  const statusEl = $("#apiStatus");
  const backendLabel = $("#backendUrl");
  await detectApi();
  
  if (state.apiOnline) {
    statusEl.classList.add("is-online");
    statusEl.querySelector(".status-beacon__label").textContent = "API Online";
    backendLabel.textContent = `Backend: ${state.apiBase}`;
    $("#lastSyncTime").textContent = "Backend connected";
  } else {
    statusEl.classList.add("is-offline");
    statusEl.querySelector(".status-beacon__label").textContent = "API Offline";
    backendLabel.textContent = "Backend: offline — using fallback data";
    showToast("Backend offline. Using ArcGIS direct feed or bundled data.", "warning");
  }

  // Load cases
  await initCaseData();

  // Init overview map
  setTimeout(() => initOverviewMap(), 200);

  // ─── Event Listeners ───

  // Search
  let searchTimer;
  $("#globalSearch").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(filterCases, 200);
  });

  // Keyboard shortcut for search
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
      e.preventDefault();
      $("#globalSearch").focus();
    }
    if (e.key === "Escape") {
      $("#globalSearch").blur();
      closeModal();
    }
  });

  // Filters
  ["filterProvince", "filterStatus", "filterSort"].forEach((id) => {
    $(`#${id}`).addEventListener("change", filterCases);
  });

  // Lead filters
  ["leadFilterConfidence", "leadFilterSource", "leadFilterReview"].forEach((id) => {
    $(`#${id}`).addEventListener("change", renderLeads);
  });

  // Tabs
  $$(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  // Back to overview
  $("#backToOverview").addEventListener("click", () => {
    state.selectedCaseId = null;
    $$(".case-card").forEach((el) => el.classList.remove("is-selected"));
    showView("overview");
    // Refresh overview map
    setTimeout(() => {
      if (state.maps.overview) state.maps.overview.invalidateSize();
    }, 100);
  });

  // Run investigation
  $("#runInvestigationBtn").addEventListener("click", runInvestigation);

  // Sync
  $("#syncBtn").addEventListener("click", syncCases);

  // Resource pack
  $("#viewResourcePackBtn").addEventListener("click", async () => {
    if (!state.apiOnline || !state.selectedCaseId) {
      showToast("Backend required for resource pack", "warning");
      return;
    }
    const data = await api(`/api/investigations/cases/${state.selectedCaseId}/resource-pack`);
    if (data) {
      showModal(`
        <h2 style="font-family:var(--font-display);font-size:22px;font-weight:800;margin-bottom:16px;color:var(--amber)">
          OSINT Resource Pack
        </h2>
        <pre style="font-family:var(--font-mono);font-size:12px;white-space:pre-wrap;overflow-wrap:break-word;word-break:break-word;color:var(--text-secondary);line-height:1.6;max-height:60vh;overflow-y:auto">
${escHtml(JSON.stringify(data, null, 2))}
        </pre>`);
    }
  });

  // Modal close
  $("#modalClose").addEventListener("click", closeModal);
  $("#modalOverlay").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeModal();
  });

  console.log("[MERIDIAN] Intelligence Console booted ✓");
}

// Boot when DOM ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
