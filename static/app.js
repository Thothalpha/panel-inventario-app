/* Panel de inventario — frontend */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const state = { data: null, rules: null, sort: { key: "value", dir: -1 }, sev: "", chat: [], charts: {} };

const nf = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1 });
const eur = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const fmt = (v, d = 0) => (v === null || v === undefined || Number.isNaN(v)) ? "—" : (d ? nf1 : nf).format(v);
const fmtEur = v => (v === null || v === undefined) ? "—" : eur.format(v);
const fmtK = v => v === null || v === undefined ? "—" : Math.abs(v) >= 1000 ? nf1.format(v / 1000) + " K€" : eur.format(v);
const fmtDate = s => s ? new Date(s).toLocaleDateString("es-ES", { day: "2-digit", month: "short", year: "numeric" }) : "—";
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const md = s => DOMPurify.sanitize(marked.parse(s || ""));
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const SEV = { critica: { label: "Crítica", ico: "⛔" }, alta: { label: "Alta", ico: "▲" }, media: { label: "Media", ico: "●" }, baja: { label: "Baja", ico: "○" } };
const KIND = {
  rotura: "Sin stock", riesgo_rotura: "Riesgo de rotura", bajo_minimo: "Bajo mínimo Odoo", punto_pedido: "Bajo punto de pedido",
  exceso: "Exceso", sin_movimiento: "Sin movimiento", negativo: "Stock negativo", anomalia: "Anomalía de demanda",
  compra_retrasada: "Compra retrasada", caducidad: "Caducidad",
};
const kindLabel = k => KIND[k] || (k.startsWith("regla") ? "Personalizada" : k);
const sevBadge = s => `<span class="sev ${s}"><i class="ico" aria-hidden="true"></i>${SEV[s].label}</span>`;

const STATIC = window.STATIC_DEMO === true;

/* ---- Demo estática (GitHub Pages): sin servidor. Datos precalculados en data/*.json;
   alertas personalizadas y "vistas" se guardan solo en el navegador de quien la mira. */
const LS = {
  get(k, d) { try { return JSON.parse(localStorage.getItem("inv-demo-" + k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("inv-demo-" + k, JSON.stringify(v)); } catch {} },
};
const OPS = { "<": (a, b) => a < b, "<=": (a, b) => a <= b, ">": (a, b) => a > b, ">=": (a, b) => a >= b, "==": (a, b) => a === b };
let _ai = null;
const getJSON = async rel => { const r = await fetch(rel); if (!r.ok) throw new Error("No se pudo cargar " + rel); return r.json(); };
async function staticApi(path, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();
  const body = opts.body ? JSON.parse(opts.body) : {};
  const wait = ms => new Promise(r => setTimeout(r, ms));
  if (path.startsWith("/api/dashboard")) {
    const d = await getJSON("data/dashboard.json");
    const rules = LS.get("rules", []), metrics = (await getJSON("data/rules.json")).metrics;
    const fmtN = v => new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1 }).format(v);
    for (const r of rules.filter(r => r.active)) {
      const vals = r.scope_values.map(v => v.toLowerCase());
      for (const p of d.products) {
        if (r.scope_type === "category" && !vals.some(v => p.categ.toLowerCase().includes(v))) continue;
        if (r.scope_type === "products" && !vals.includes(p.ref.toLowerCase())) continue;
        const v = p[r.metric];
        if (v === null || v === undefined || !OPS[r.op](+v, r.threshold)) continue;
        d.alerts.push({ key: `regla${r.id}:${p.id}`, kind: `regla${r.id}`, severity: r.severity, product_id: p.id, ref: p.ref, name: p.name,
          categ: p.categ, title: r.name, detail: `${metrics[r.metric]} = ${fmtN(v)} (${r.op} ${fmtN(r.threshold)}).`, action: r.description || "Revisar", impact_eur: null });
      }
    }
    const acks = LS.get("acks", {}), rank = { critica: 0, alta: 1, media: 2, baja: 3 };
    d.alerts.forEach(a => a.acked = !!acks[a.key]);
    d.alerts.sort((a, b) => (a.acked - b.acked) || (rank[a.severity] - rank[b.severity]) || ((b.impact_eur || 0) - (a.impact_eur || 0)));
    d.last_summary = LS.get("summary", null);
    return d;
  }
  if (path.startsWith("/api/products/")) return getJSON("data/products/" + path.split("/").pop() + ".json");
  if (path === "/api/rules" && method === "GET") return { ...(await getJSON("data/rules.json")), rules: LS.get("rules", []) };
  if (path === "/api/rules" && method === "POST") {
    if (!body.name || !body.metric || Number.isNaN(+body.threshold)) throw new Error("Completa nombre, métrica y umbral");
    if (body.scope_type !== "all" && !(body.scope_values || []).length) throw new Error("Indica las categorías o referencias de producto");
    const rules = LS.get("rules", []);
    const rule = { ...body, threshold: +body.threshold, active: true, id: Date.now() };
    rules.unshift(rule); LS.set("rules", rules); return rule;
  }
  if (path.startsWith("/api/rules/")) {
    const id = +path.split("/").pop();
    let rules = LS.get("rules", []);
    rules = method === "DELETE" ? rules.filter(r => r.id !== id) : rules.map(r => r.id === id ? { ...r, active: !!body.active } : r);
    LS.set("rules", rules); return { ok: true };
  }
  if (path === "/api/alerts/ack") {
    const acks = LS.get("acks", {}); (body.keys || []).forEach(k => acks[k] = new Date().toISOString()); LS.set("acks", acks); return { ok: true };
  }
  if (path === "/api/settings" && method === "GET") return getJSON("data/settings.json");
  if (path === "/api/settings") throw new Error("En la demo publicada los parámetros no se pueden recalcular (no hay servidor). En la versión real se guardan y el panel se recalcula al momento.");
  _ai = _ai || await getJSON("data/ai.json");
  await wait(900);
  if (path === "/api/ai/summary") { LS.set("summary", { content: _ai.summary.text, created_at: new Date().toISOString() }); return _ai.summary; }
  if (path === "/api/ai/chat") {
    const last = (body.messages || []).filter(m => m.role === "user").pop()?.content || "";
    const key = Object.keys(_ai.chat).find(k => last.includes(k) || k.includes(last.replace(/^Crea una alerta personalizada.*?: /, "")));
    if (key) return _ai.chat[key];
    const ref = last.match(/producto (\S+)/)?.[1];
    if (ref && _ai.products[ref]) return _ai.products[ref];
    return { text: "En esta **demo publicada** el asistente no está conectado (no hay servidor ni clave de IA), así que solo responde a las preguntas de ejemplo de abajo y al botón «Preguntar a la IA por este producto».\n\nEn la versión real puedes preguntarle cualquier cosa: Claude consulta el panel y lanza las consultas a Odoo en el momento (solo lectura).", proposals: [], queries: [] };
  }
  throw new Error("No disponible en la demo: " + path);
}

/* Token de sesión de la app de escritorio: llega en el fragmento (#t=…), que nunca se envía al servidor */
const APP_TOKEN = (() => {
  const m = location.hash.match(/[#&]t=([^&]+)/);
  if (m) {
    try { sessionStorage.setItem("app-token", m[1]); } catch {}
    history.replaceState(null, "", location.pathname + location.search);
    return m[1];
  }
  try { return sessionStorage.getItem("app-token") || ""; } catch { return ""; }
})();

async function api(path, opts = {}) {
  if (STATIC) return staticApi(path, opts);
  const headers = { "Content-Type": "application/json", ...(APP_TOKEN ? { "X-App-Token": APP_TOKEN } : {}) };
  const r = await fetch(path, { ...opts, headers });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    const err = new Error(msg);
    err.status = r.status;
    throw err;
  }
  return r.json();
}

/* ---------------------------------------------------------------- tabs */
$$(".tabs button").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(t) {
  $$(".tabs button").forEach(b => b.setAttribute("aria-selected", b.dataset.tab === t));
  $$(".tab").forEach(s => s.classList.toggle("active", s.id === "tab-" + t));
  try { localStorage.setItem("inv-tab", t); } catch {}
}

/* ---------------------------------------------------------------- carga */
async function load(refresh = false) {
  const btn = $("#refresh");
  btn.disabled = true; btn.textContent = "Cargando…";
  try {
    state.data = await api("/api/dashboard" + (refresh ? "?refresh=1" : ""));
    render();
  } catch (e) {
    const fix = e.status === 409 || e.status === 401 ? ` <button class="link" id="warn-setup">Revisar la conexión</button>` : "";
    $("#warnings").innerHTML = `<div class="warn">No se pudieron cargar los datos: ${esc(e.message)}${fix}</div>`;
    $("#warn-setup")?.addEventListener("click", openSetup);
  } finally {
    btn.disabled = false; btn.textContent = "↻ Actualizar";
  }
}

function render() {
  const d = state.data;
  const live = d.source === "odoo";
  $("#source").className = "badge " + (live ? "live" : "demo");
  $("#source").textContent = live ? "Odoo en vivo" : (STATIC ? "Demo · datos ficticios" : "Modo demo");
  $("#refresh").hidden = STATIC;
  $("#review").hidden = !STATIC || LS.get("review-closed", false);
  $("#updated").textContent = "Datos de " + new Date(d.generated_at).toLocaleString("es-ES", { dateStyle: "short", timeStyle: "short" });
  $("#warnings").innerHTML = d.warnings.map(w => `<div class="warn">${esc(w)}</div>`).join("");
  const open = d.alerts.filter(a => !a.acked && (a.severity === "critica" || a.severity === "alta")).length;
  $("#alert-count").textContent = open || "";
  if (!d.ai_available) {
    $("#gen-summary").disabled = true;
    $("#gen-summary").title = "Configura ANTHROPIC_API_KEY en el .env";
  }
  if (STATIC) $("#updated").textContent = "Vista previa para revisión";
  if (d.last_summary) showSummary(d.last_summary.content, d.last_summary.created_at, d.last_summary.model);
  renderKpis(); renderCharts(); renderTopDemand(); renderFilters(); renderProducts(); renderAlerts();
}

/* ---------------------------------------------------------------- resumen */
function renderKpis() {
  const o = state.data.overview;
  const s = o.alerts_by_severity;
  const dq = o.data_quality || {};
  const tiles = [
    ["Valor del stock", fmtK(o.stock_value), `${fmt(o.skus_with_stock)} de ${fmt(o.skus)} productos con stock`],
    ["Rotación anual", o.turnover === null ? "—" : fmt(o.turnover, 1) + "×", "consumo 12 m / stock actual"],
    ["Alertas urgentes", fmt(s.critica + s.alta), `${fmt(s.critica)} críticas · ${fmt(s.alta)} altas`],
    ["A pedir", fmt(o.to_order_count), `≈ ${fmtK(o.to_order_value)} a coste`],
    ["Exceso (> 1 año)", fmtK(o.overstock_value), "valor con cobertura > 365 d"],
    ["Sin movimiento", fmtK(o.dead_stock_value), "> 180 días parado"],
    ["Compras en curso", fmtK(o.pending_po_value), "pendiente de recibir"],
    ["Precisión previsión", o.forecast_accuracy === null ? "—" : fmt(o.forecast_accuracy * 100) + " %",
      "1 − WAPE, bloques de 4 semanas"],
  ];
  if (dq.sin_coste_con_stock) tiles.push(["Calidad de datos", fmt(dq.sin_coste_con_stock), "productos con stock y sin coste"]);
  $("#kpis").innerHTML = tiles.map(([l, v, h]) => `<div class="kpi"><div class="label">${l}</div><div class="value">${v}</div><div class="hint">${h}</div></div>`).join("");
}

function baseOpts(extra = {}) {
  const grid = css("--grid"), ink2 = css("--ink-2");
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    plugins: { legend: { display: false }, tooltip: { backgroundColor: css("--surface"), titleColor: css("--ink"), bodyColor: css("--ink"), borderColor: css("--axis"), borderWidth: 1, padding: 10 } },
    scales: {
      x: { grid: { display: false }, ticks: { color: ink2 }, border: { color: css("--axis") } },
      y: { grid: { color: grid }, ticks: { color: ink2 }, border: { display: false }, beginAtZero: true },
    },
    ...extra,
  };
}
function chart(id, cfg) {
  state.charts[id]?.destroy();
  state.charts[id] = new Chart(document.getElementById(id), cfg);
  return state.charts[id];
}

function renderCharts() {
  const o = state.data.overview;
  // Cobertura (tramos ordenados; el color marca el estado, la etiqueta del eje lo nombra)
  const cb = o.coverage_buckets;
  const covColor = { "Sin stock": "--critical", "< plazo": "--critical", "< 30 d": "--warning", "> 365 d": "--serious" };
  chart("ch-coverage", {
    type: "bar",
    data: { labels: Object.keys(cb), datasets: [{ data: Object.values(cb), backgroundColor: Object.keys(cb).map(k => css(covColor[k] || "--seq-400")), borderRadius: 4, maxBarThickness: 44 }] },
    options: baseOpts({ plugins: { ...baseOpts().plugins, tooltip: { ...baseOpts().plugins.tooltip, callbacks: { label: c => `${c.raw} productos` } } } }),
  });
  // Alertas por tipo
  const byKind = {};
  state.data.alerts.filter(a => !a.acked).forEach(a => byKind[kindLabel(a.kind)] = (byKind[kindLabel(a.kind)] || 0) + 1);
  const kinds = Object.entries(byKind).sort((a, b) => b[1] - a[1]);
  chart("ch-alerts", {
    type: "bar",
    data: { labels: kinds.map(k => k[0]), datasets: [{ data: kinds.map(k => k[1]), backgroundColor: css("--s1"), borderRadius: 4, maxBarThickness: 22 }] },
    options: baseOpts({ indexAxis: "y", scales: { x: { grid: { color: css("--grid") }, ticks: { color: css("--ink-2") }, beginAtZero: true }, y: { grid: { display: false }, ticks: { color: css("--ink-2") } } } }),
  });
  // Valor por categoría (top 8 + otros)
  const cats = Object.entries(o.value_by_category).filter(c => c[1] > 0);
  const top = cats.slice(0, 8);
  const rest = cats.slice(8).reduce((s, c) => s + c[1], 0);
  if (rest > 0) top.push(["Otras", rest]);
  chart("ch-categ", {
    type: "bar",
    data: { labels: top.map(c => c[0].length > 28 ? c[0].slice(0, 27) + "…" : c[0]), datasets: [{ data: top.map(c => c[1]), backgroundColor: css("--s1"), borderRadius: 4, maxBarThickness: 22 }] },
    options: baseOpts({ indexAxis: "y", plugins: { ...baseOpts().plugins, tooltip: { ...baseOpts().plugins.tooltip, callbacks: { title: c => cats[c[0].dataIndex]?.[0] || "Otras", label: c => fmtEur(c.raw) } } },
      scales: { x: { grid: { color: css("--grid") }, ticks: { color: css("--ink-2"), callback: v => fmtK(v) }, beginAtZero: true }, y: { grid: { display: false }, ticks: { color: css("--ink-2") } } } }),
  });
  // Matriz ABC/XYZ (secuencial azul por valor)
  const m = o.abc_xyz;
  const maxV = Math.max(1, ...Object.values(m).map(c => c.value));
  const steps = ["--seq-100", "--seq-250", "--seq-400", "--seq-550", "--seq-700"];
  let html = `<div class="abc"><div></div><div class="h">X estable</div><div class="h">Y variable</div><div class="h">Z errática</div>`;
  for (const a of ["A", "B", "C"]) {
    html += `<div class="h">${a}</div>`;
    for (const x of ["X", "Y", "Z"]) {
      const c = m[a + x] || { count: 0, value: 0 };
      const i = c.count ? Math.min(4, Math.floor((c.value / maxV) * 4.999)) : -1;
      const bg = i < 0 ? "var(--surface-2)" : `var(${steps[i]})`;
      const theme = document.documentElement.dataset.theme;
      const dark = theme ? theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      const ink = (dark ? i <= 2 : i >= 3) ? "#ffffff" : "#0b0b0b";
      html += `<div class="c" style="background:${bg};color:${i < 0 ? "var(--ink-2)" : ink}" title="${a}${x}: ${c.count} productos · ${fmtEur(c.value)}"><b>${c.count}</b><span>${fmtK(c.value)}</span></div>`;
    }
  }
  $("#abcxyz").innerHTML = html + "</div>";
}

function renderTopDemand() {
  const rows = state.data.overview.top_demand_30d;
  const byId = Object.fromEntries(state.data.products.map(p => [p.id, p]));
  $("#top-demand").innerHTML = `<thead><tr><th>Ref.</th><th>Producto</th><th class="num">Demanda prevista 30 d</th><th class="num">Stock</th><th class="num">Cobertura (d)</th><th>Rotura estimada</th></tr></thead><tbody>` +
    rows.map(r => { const p = byId[r.id]; return `<tr data-id="${r.id}"><td>${esc(r.ref)}</td><td class="name">${esc(r.name)}</td><td class="num">${fmt(r.forecast_30d)}</td><td class="num">${fmt(r.on_hand)}</td><td class="num">${fmt(p?.coverage_days)}</td><td>${fmtDate(p?.stockout_date)}</td></tr>`; }).join("") + "</tbody>";
  $$("#top-demand tbody tr").forEach(tr => tr.addEventListener("click", () => openDetail(+tr.dataset.id)));
}

/* ---------------------------------------------------------------- resumen IA */
function showSummary(content, when, model) {
  $("#summary").classList.remove("muted");
  $("#summary").innerHTML = md(content);
  $("#summary-meta").textContent = when ? `Generado ${new Date(when).toLocaleString("es-ES", { dateStyle: "short", timeStyle: "short" })}` : "";
}
$("#gen-summary").addEventListener("click", async () => {
  const b = $("#gen-summary");
  b.disabled = true;
  $("#summary").innerHTML = `<span class="spinner"></span>Analizando el inventario y consultando Odoo… (puede tardar un minuto)`;
  try {
    const r = await api("/api/ai/summary", { method: "POST" });
    showSummary(r.text, new Date().toISOString(), r.model);
  } catch (e) {
    $("#summary").innerHTML = `<div class="warn">${esc(e.message)}</div>`;
  } finally { b.disabled = !state.data.ai_available; }
});

/* ---------------------------------------------------------------- productos */
const COLS = [
  ["ref", "Ref."], ["name", "Producto"], ["on_hand", "Stock", 1], ["virtual", "Previsto", 1], ["forecast_30d", "Demanda 30 d", 1],
  ["coverage_days", "Cobertura (d)", 1], ["stockout_date", "Rotura"], ["lead_time_days", "Plazo (d)", 1], ["reorder_point", "Punto pedido", 1],
  ["suggested_order", "Sugerido", 1], ["abc", "ABC"], ["xyz", "XYZ"], ["value", "Valor €", 1], ["days_idle", "Días parado", 1],
];
function renderFilters() {
  const cats = [...new Set(state.data.products.map(p => p.categ))].sort();
  const sel = $("#f-categ"), cur = sel.value;
  sel.innerHTML = `<option value="">Todas las categorías</option>` + cats.map(c => `<option>${esc(c)}</option>`).join("");
  sel.value = cur;
}
function filteredProducts() {
  const t = $("#f-text").value.trim().toLowerCase(), c = $("#f-categ").value, abc = $("#f-abc").value, st = $("#f-state").value;
  const riskIds = new Set(state.data.alerts.filter(a => a.kind === "rotura" || a.kind === "riesgo_rotura").map(a => a.product_id));
  return state.data.products.filter(p =>
    (!t || p.ref.toLowerCase().includes(t) || p.name.toLowerCase().includes(t)) && (!c || p.categ === c) && (!abc || p.abc === abc) &&
    (!st || (st === "riesgo" && riskIds.has(p.id)) || (st === "pedir" && p.suggested_order > 0) ||
      (st === "exceso" && p.coverage_days > 365) || (st === "parado" && p.days_idle > 180 && p.on_hand > 0) || (st === "stock" && p.on_hand > 0)));
}
function renderProducts() {
  const rows = filteredProducts();
  const { key, dir } = state.sort;
  rows.sort((a, b) => {
    const x = a[key], y = b[key];
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir;
  });
  $("#f-count").textContent = `${rows.length} productos`;
  const head = `<thead><tr>${COLS.map(([k, l, n]) => `<th data-k="${k}" class="${n ? "num" : ""}" ${k === key ? `aria-sort="${dir > 0 ? "ascending" : "descending"}"` : ""}>${l}</th>`).join("")}</tr></thead>`;
  const body = rows.slice(0, 500).map(p => `<tr data-id="${p.id}">${COLS.map(([k, , n]) => {
    let v = p[k];
    if (k === "stockout_date") v = fmtDate(v);
    else if (k === "value") v = fmt(v);
    else if (n) v = fmt(v, k === "coverage_days" ? 0 : 0);
    else v = esc(v ?? "—");
    return `<td class="${n ? "num" : ""} ${k === "name" ? "name" : ""}">${v}</td>`;
  }).join("")}</tr>`).join("");
  $("#products").innerHTML = head + `<tbody>${body}</tbody>`;
  $$("#products th").forEach(th => th.addEventListener("click", () => {
    const k = th.dataset.k;
    state.sort = { key: k, dir: state.sort.key === k ? -state.sort.dir : (k === "coverage_days" || k === "ref" || k === "name" ? 1 : -1) };
    renderProducts();
  }));
  $$("#products tbody tr").forEach(tr => tr.addEventListener("click", () => openDetail(+tr.dataset.id)));
}
["#f-text", "#f-categ", "#f-abc", "#f-state"].forEach(s => $(s).addEventListener("input", renderProducts));

/* ---------------------------------------------------------------- detalle */
async function openDetail(pid) {
  const dlg = $("#detail");
  const p = state.data.products.find(x => x.id === pid);
  $("#d-title").textContent = `${p.ref} · ${p.name}`;
  $("#d-sub").textContent = `${p.categ} · ${p.uom}${p.supplier ? " · Proveedor: " + p.supplier : ""}`;
  $("#d-kpis").innerHTML = "";
  $("#d-tables").innerHTML = "";
  if (!dlg.open) dlg.showModal();
  const d = await api("/api/products/" + pid);
  const m = d.metrics;
  const tiles = [
    ["Stock a mano", fmt(m.on_hand)], ["Previsto", fmt(m.virtual)], ["Demanda prevista 30 d", fmt(m.forecast_30d)],
    ["Cobertura", m.coverage_days === null ? "—" : fmt(m.coverage_days) + " d"], ["Rotura estimada", fmtDate(m.stockout_date)],
    ["Plazo de entrega", fmt(m.lead_time_days) + " d"], ["Stock de seguridad", fmt(m.safety_stock)], ["Punto de pedido", fmt(m.reorder_point)],
    ["Cantidad sugerida", fmt(m.suggested_order)], ["Mín/Máx Odoo", m.orderpoint_min === null ? "sin regla" : `${fmt(m.orderpoint_min)} / ${fmt(m.orderpoint_max)}`],
    ["Clase", `${m.abc}${m.xyz || ""}`], ["Valor", fmtEur(m.value)],
  ];
  $("#d-kpis").innerHTML = tiles.map(([l, v]) => `<div class="kpi"><div class="label">${l}</div><div class="value">${v}</div></div>`).join("");
  const f = d.forecast;
  $("#d-method").textContent = `Método: ${f.method}` + (f.accuracy !== null && f.accuracy !== undefined ? ` · precisión en backtest ${fmt(f.accuracy * 100)} %` : " · sin backtest (historia corta)") + " · banda: intervalo del 80 %";

  const hw = d.history.weeks.slice(-52), hv = d.history.values.slice(-52);
  const labels = [...hw, ...f.future_weeks].map(w => new Date(w).toLocaleDateString("es-ES", { day: "2-digit", month: "short" }));
  const pad = Array(hw.length).fill(null);
  const lastH = hv[hv.length - 1];
  chart("ch-demand", {
    type: "line",
    data: {
      labels, datasets: [
        { label: "Demanda real", data: [...hv, ...Array(f.forecast.length).fill(null)], borderColor: css("--s1"), backgroundColor: css("--s1"), borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0 },
        { label: "Banda 80 % (alta)", data: [...pad.slice(1), lastH, ...f.hi], borderColor: "transparent", backgroundColor: css("--s2") + "26", pointRadius: 0, fill: "+1" },
        { label: "Banda 80 % (baja)", data: [...pad.slice(1), lastH, ...f.lo], borderColor: "transparent", pointRadius: 0, fill: false },
        { label: "Previsión", data: [...pad.slice(1), lastH, ...f.forecast], borderColor: css("--s2"), backgroundColor: css("--s2"), borderWidth: 2, borderDash: [6, 4], pointRadius: 0, pointHoverRadius: 4 },
      ],
    },
    options: baseOpts({
      interaction: { mode: "index", intersect: false },
      plugins: { ...baseOpts().plugins, legend: { display: true, labels: { color: css("--ink-2"), boxWidth: 14, filter: i => !i.text.includes("baja") } },
        tooltip: { ...baseOpts().plugins.tooltip, filter: i => i.raw !== null && !i.dataset.label.includes("Banda"), callbacks: { label: c => `${c.dataset.label}: ${fmt(c.raw, 1)} ${m.uom}` } } },
      scales: { x: { grid: { display: false }, ticks: { color: css("--ink-2"), maxTicksLimit: 12 } }, y: { grid: { color: css("--grid") }, ticks: { color: css("--ink-2") }, beginAtZero: true } },
    }),
  });
  const pl = f.future_weeks.map(w => new Date(w).toLocaleDateString("es-ES", { day: "2-digit", month: "short" }));
  chart("ch-proj", {
    type: "line",
    data: {
      labels: ["Hoy", ...pl], datasets: [
        { label: "Stock proyectado", data: [m.on_hand, ...d.projection], borderColor: css("--s1"), backgroundColor: css("--s1"), borderWidth: 2, pointRadius: 3, stepped: false },
        { label: "Punto de pedido", data: Array(pl.length + 1).fill(m.reorder_point), borderColor: css("--serious"), borderWidth: 1.5, borderDash: [4, 4], pointRadius: 0 },
        { label: "Stock de seguridad", data: Array(pl.length + 1).fill(m.safety_stock), borderColor: css("--critical"), borderWidth: 1.5, borderDash: [2, 3], pointRadius: 0 },
      ],
    },
    options: baseOpts({
      interaction: { mode: "index", intersect: false },
      plugins: { ...baseOpts().plugins, legend: { display: true, labels: { color: css("--ink-2"), boxWidth: 14 } }, tooltip: { ...baseOpts().plugins.tooltip, callbacks: { label: c => `${c.dataset.label}: ${fmt(c.raw)} ${m.uom}` } } },
      scales: { x: { grid: { display: false }, ticks: { color: css("--ink-2") } }, y: { grid: { color: css("--grid") }, ticks: { color: css("--ink-2") } } },
    }),
  });
  const tbl = (title, head, rows) => `<div class="card"><h3>${title}</h3>${rows.length ? `<div class="table-wrap"><table class="tbl"><thead><tr>${head.map(h => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : `<p class="muted small">Sin datos</p>`}</div>`;
  $("#d-tables").innerHTML =
    tbl("Stock por ubicación", ["Ubicación", "Lote", "Caducidad", "Cantidad", "Reservado"], d.quants.map(q => [esc(q.location), esc(q.lot || "—"), fmtDate(q.expiration), fmt(q.qty), fmt(q.reserved)])) +
    tbl("Compras pendientes", ["Pedido", "Proveedor", "Fecha prevista", "Pendiente"], d.pending_po.map(po => [esc(po.order), esc(po.partner), fmtDate(po.date), fmt(po.qty)])) +
    (m.used_in.length ? tbl("Se usa en", ["Producto terminado", "Cantidad por unidad"], m.used_in.map(u => [esc(u.parent), fmt(u.qty_per_unit, 1)])) : "");
  $("#d-ask").onclick = () => { dlg.close(); showTab("asistente"); sendChat(`Analiza el producto ${m.ref} (${m.name}): situación, riesgo y qué me recomiendas hacer.`); };
}
$("#d-close").addEventListener("click", () => $("#detail").close());

/* ---------------------------------------------------------------- alertas */
const FIELD_LABEL = { products: "Productos", categories: "Categorías", families: "Familias de referencia" };
const SCOPE_LABEL = { all: "Todos los productos", category: "Categorías", family: "Familias de referencia", products: "Productos concretos" };

function fillSelect(sel, values, allLabel, labelFn = v => v) {
  const cur = sel.value;
  sel.innerHTML = `<option value="">${esc(allLabel)}</option>` + values.map(v => `<option value="${esc(v)}">${esc(labelFn(v))}</option>`).join("");
  if (values.includes(cur)) sel.value = cur;
}

function renderAlerts() {
  const all = state.data.alerts;
  const counts = { "": all.filter(a => !a.acked).length };
  Object.keys(SEV).forEach(s => counts[s] = all.filter(a => !a.acked && a.severity === s).length);
  $("#sev-chips").innerHTML = [["", "Todas"], ...Object.entries(SEV).map(([k, v]) => [k, v.label])]
    .map(([k, l]) => `<button class="chip" data-s="${k}" aria-pressed="${state.sev === k}">${k ? `<span class="sev ${k}" style="border:0;padding:0"></span> ` : ""}${l} (${counts[k]})</button>`).join("");
  $$("#sev-chips .chip").forEach(c => c.addEventListener("click", () => { state.sev = c.dataset.s; renderAlerts(); }));
  const uniq = f => [...new Set(all.map(f).filter(Boolean))].sort((a, b) => String(a).localeCompare(b, "es"));
  fillSelect($("#a-kind"), uniq(a => a.kind), "Todos los tipos", kindLabel);
  fillSelect($("#a-categ"), uniq(a => a.categ), "Todas las categorías");
  fillSelect($("#a-family"), uniq(a => a.family), "Todas las familias");
  const list = visibleAlerts();
  $("#a-count").textContent = `${list.length} alertas`;
  $("#alert-list").innerHTML = list.length ? list.slice(0, 300).map(a => {
    const auto = !a.kind.startsWith("regla");
    const ruleId = auto ? null : a.kind.slice(5);
    return `
    <div class="alert ${a.severity} ${a.acked ? "acked" : ""}">
      <div>${sevBadge(a.severity)}</div>
      <div>
        <div class="t">${esc(a.title)}</div>
        <div class="p"><button class="link" data-id="${a.product_id}">${esc(a.ref)}</button> · ${esc(a.name)} · ${esc(kindLabel(a.kind))} · <span class="muted">${esc(a.categ)} · familia ${esc(a.family || "—")}</span></div>
        <div class="p">${esc(a.detail)}</div>
        <div class="a">${esc(a.action)}</div>
      </div>
      <div class="side">
        ${a.impact_eur ? `<span class="small muted">Impacto ≈ ${fmtEur(a.impact_eur)}</span>` : ""}
        ${a.acked ? `<span class="small muted">Vista</span>` : `<button class="btn ghost small" data-ack="${esc(a.key)}">Marcar vista</button>`}
        <details class="menu"><summary class="btn ghost small">Opciones ▾</summary>
          <div class="menu-list">
            ${auto ? `<button data-mute-kind="${esc(a.kind)}" data-ref="${esc(a.ref)}">No avisar de ${esc(a.ref)} en «${esc(kindLabel(a.kind))}»</button>` : ""}
            <button data-mute-kind="*" data-ref="${esc(a.ref)}">No avisar de ${esc(a.ref)} en ninguna alerta</button>
            ${auto ? `<button data-edit-kind="${esc(a.kind)}">Configurar «${esc(kindLabel(a.kind))}»</button>`
                   : `<button data-edit-rule="${esc(ruleId)}">Editar esta alerta</button>`}
            <button data-open="${a.product_id}">Ver el producto</button>
          </div></details>
      </div>
    </div>`;
  }).join("") : `<div class="card muted">No hay alertas con estos filtros. 🎉</div>`;
  $$("#alert-list [data-ack]").forEach(b => b.addEventListener("click", () => ackAlerts([b.dataset.ack])));
  $$("#alert-list .link, #alert-list [data-open]").forEach(b => b.addEventListener("click", () => openDetail(+(b.dataset.id || b.dataset.open))));
  $$("#alert-list [data-mute-kind]").forEach(b => b.addEventListener("click", () => muteProduct(b.dataset.muteKind, b.dataset.ref, b)));
  $$("#alert-list [data-edit-kind]").forEach(b => b.addEventListener("click", () => openKindEditor(b.dataset.editKind)));
  $$("#alert-list [data-edit-rule]").forEach(b => b.addEventListener("click", () => openRuleEditor(+b.dataset.editRule)));
}
function visibleAlerts() {
  const k = $("#a-kind").value, c = $("#a-categ").value, f = $("#a-family").value, showAck = $("#a-acked").checked;
  const q = $("#a-search").value.trim().toLowerCase();
  return state.data.alerts.filter(a => (showAck || !a.acked) && (!state.sev || a.severity === state.sev) && (!k || a.kind === k)
    && (!c || a.categ === c) && (!f || a.family === f)
    && (!q || a.ref.toLowerCase().includes(q) || a.name.toLowerCase().includes(q) || a.title.toLowerCase().includes(q)));
}
async function ackAlerts(keys) {
  await api("/api/alerts/ack", { method: "POST", body: JSON.stringify({ keys }) });
  const set = new Set(keys);
  state.data.alerts.forEach(a => { if (set.has(a.key)) a.acked = true; });
  renderAlerts(); renderCharts();
  $("#alert-count").textContent = state.data.alerts.filter(a => !a.acked && (a.severity === "critica" || a.severity === "alta")).length || "";
}
async function muteProduct(kind, ref, btn) {
  try {
    await api("/api/alert-config/mute", { method: "POST", body: JSON.stringify({ kind, ref }) });
    await Promise.all([loadAlertConfig(), load()]);
  } catch (e) { alertBox(btn.closest(".alert") || btn, e.message); }
}
["#a-kind", "#a-categ", "#a-family", "#a-acked", "#a-search"].forEach(s => $(s).addEventListener("input", renderAlerts));
$("#ack-all").addEventListener("click", () => {
  const keys = visibleAlerts().filter(a => !a.acked).map(a => a.key);
  if (keys.length) ackAlerts(keys);
});
function alertBox(el, msg) { const d = document.createElement("div"); d.className = "warn"; d.textContent = msg; el.after(d); setTimeout(() => d.remove(), 6000); }

/* ---------------------------------------------------------------- configuración de alertas automáticas */
function exclSummary(ex) {
  const parts = Object.entries(FIELD_LABEL).filter(([f]) => ex[f]?.length).map(([f, l]) => `${l.toLowerCase()}: ${ex[f].join(", ")}`);
  return parts.length ? "Sin avisos de " + parts.join(" · ") : "";
}
function ensureDatalists() {
  const c = state.acfg;
  const dl = (id, opts) => { let el = document.getElementById(id); if (!el) { el = document.createElement("datalist"); el.id = id; document.body.appendChild(el); } el.innerHTML = opts; };
  dl("dl-products", c.products.map(p => `<option value="${esc(p.ref)}">${esc(p.name)}</option>`).join(""));
  dl("dl-categories", c.categories.map(v => `<option value="${esc(v)}"></option>`).join(""));
  dl("dl-families", c.families.map(v => `<option value="${esc(v)}"></option>`).join(""));
}
async function loadAlertConfig() {
  if (STATIC) return;
  try { state.acfg = await api("/api/alert-config"); } catch { return; }
  ensureDatalists();
  const sevOpts = (cur, def) => `<option value="auto" ${!cur ? "selected" : ""}>Automática (${esc(def)})</option>` +
    state.acfg.severities.map(s => `<option value="${s}" ${cur === s ? "selected" : ""}>${SEV[s].label}</option>`).join("");
  $("#auto-config").innerHTML = state.acfg.kinds.map(k => `
    <div class="ac-row ${k.active ? "" : "off"}">
      <label class="check ac-name"><input type="checkbox" data-kind-active="${k.kind}" ${k.active ? "checked" : ""}> <b>${esc(k.label)}</b></label>
      <div class="ac-desc small muted">${esc(k.desc)}${exclSummary(k.exclude) ? `<br><span class="ac-excl">${esc(exclSummary(k.exclude))}</span>` : ""}</div>
      <select data-kind-sev="${k.kind}" aria-label="Severidad de ${esc(k.label)}">${sevOpts(k.severity, k.default_severity)}</select>
      <button class="btn ghost" data-kind-edit="${k.kind}">Editar</button>
    </div>`).join("");
  $$("#auto-config [data-kind-active]").forEach(c => c.addEventListener("input", () => saveKind(c.dataset.kindActive, { active: c.checked })));
  $$("#auto-config [data-kind-sev]").forEach(s => s.addEventListener("input", () => saveKind(s.dataset.kindSev, { severity: s.value })));
  $$("#auto-config [data-kind-edit]").forEach(b => b.addEventListener("click", () => openKindEditor(b.dataset.kindEdit)));
  const g = state.acfg.global.exclude;
  $("#global-excl").innerHTML = exclSummary(g) ? `<p>${esc(exclSummary(g))}</p>` : `<p class="small muted">Ninguno: recibes alertas de todos los productos.</p>`;
}
async function saveKind(kind, body) {
  await api("/api/alert-config/" + encodeURIComponent(kind), { method: "PUT", body: JSON.stringify(body) });
  await Promise.all([loadAlertConfig(), load()]);
}
$("#global-excl-edit").addEventListener("click", () => openKindEditor("*"));

/* ---- editor genérico */
const ae = { mode: null, id: null, confirmDelete: false };
function aeMsg(text, cls = "err") { $("#ae-msg").className = "ae-msg " + cls; $("#ae-msg").textContent = text; }
function aeOpen(title) {
  $("#ae-title").textContent = title; aeMsg("", ""); ae.confirmDelete = false;
  $("#ae-delete").textContent = "Eliminar";
  if (!$("#alert-editor").open) $("#alert-editor").showModal();
}
["#ae-close", "#ae-cancel"].forEach(s => $(s).addEventListener("click", () => $("#alert-editor").close()));

function chipList(field, values) {
  return values.map(v => `<span class="xchip" data-f="${field}" data-v="${esc(v)}">${esc(v)}<button type="button" aria-label="Quitar ${esc(v)}">×</button></span>`).join("");
}
function exclEditorHtml(ex) {
  return Object.entries(FIELD_LABEL).map(([f, label]) => `
    <div class="xfield" data-field="${f}">
      <div class="small"><b>${label}</b></div>
      <div class="xchips">${chipList(f, ex[f] || [])}</div>
      <div class="row"><input list="dl-${f}" placeholder="Añadir ${label.toLowerCase()}…" data-add="${f}"><button type="button" class="btn ghost" data-addbtn="${f}">Añadir</button></div>
    </div>`).join("");
}
function bindChips(root) {
  root.addEventListener("click", e => {
    const x = e.target.closest(".xchip button");
    if (x) { x.parentElement.remove(); return; }
    const add = e.target.closest("[data-addbtn]");
    if (add) addChip(root, add.dataset.addbtn);
  });
  $$("[data-add]", root).forEach(i => i.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); addChip(root, i.dataset.add); } }));
}
function addChip(root, f) {
  const input = $(`[data-add="${f}"]`, root);
  let v = input.value.trim();
  if (!v) return;
  if (f === "families") v = v.toUpperCase();
  const box = $(`.xfield[data-field="${f}"] .xchips`, root);
  if (![...box.children].some(c => c.dataset.v.toLowerCase() === v.toLowerCase())) box.insertAdjacentHTML("beforeend", chipList(f, [v]));
  input.value = "";
}
function readChips(root) {
  const ex = {};
  Object.keys(FIELD_LABEL).forEach(f => ex[f] = $$(`.xfield[data-field="${f}"] .xchip`, root).map(c => c.dataset.v));
  return ex;
}

/* ---- editor de un tipo automático (o de las exclusiones globales "*") */
function openKindEditor(kind) {
  if (!state.acfg) return;
  const isGlobal = kind === "*";
  const k = isGlobal ? { label: "Productos sin alertas", desc: "No recibirás ninguna alerta, automática ni personalizada, de lo que añadas aquí.", ...state.acfg.global, settings: [] }
    : state.acfg.kinds.find(x => x.kind === kind);
  ae.mode = "kind"; ae.id = kind;
  $("#ae-delete").hidden = true;
  const sevSel = isGlobal ? "" : `<label>Severidad<select name="severity"><option value="auto" ${!k.severity ? "selected" : ""}>Automática (${esc(k.default_severity)})</option>` +
    state.acfg.severities.map(s => `<option value="${s}" ${k.severity === s ? "selected" : ""}>${SEV[s].label}</option>`).join("") + `</select></label>`;
  $("#ae-body").innerHTML = `
    <p class="muted">${esc(k.desc)}</p>
    ${isGlobal ? "" : `<label class="check"><input type="checkbox" name="active" ${k.active ? "checked" : ""}> Alerta activa</label>`}
    ${sevSel}
    ${k.settings.length ? `<div class="ae-grid">${k.settings.map(st => `<label>${esc(SETTING_LABELS[st.key] || st.key)}<input type="number" step="any" name="set:${st.key}" value="${st.value}"></label>`).join("")}</div>
      <p class="small muted">Los umbrales son generales: cambiarlos aquí también los cambia en Configuración.</p>` : ""}
    <h3>${isGlobal ? "Sin alertas de…" : "No avisar de…"}</h3>
    <div class="xedit">${exclEditorHtml(k.exclude)}</div>`;
  bindChips($("#ae-body"));
  aeOpen(isGlobal ? "Productos sin alertas" : `Alerta automática: ${k.label}`);
}

/* ---- editor de una alerta personalizada (nueva o existente) */
function openRuleEditor(id = null) {
  const r = id ? state.rules.rules.find(x => x.id === id) : { name: "", description: "", scope_type: "all", scope_values: [], metric: "on_hand", op: "<", threshold: "", severity: "media", active: true };
  if (!r) return;
  ae.mode = "rule"; ae.id = id;
  $("#ae-delete").hidden = !id;
  const opt = (obj, cur) => Object.entries(obj).map(([v, l]) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(l)}</option>`).join("");
  $("#ae-body").innerHTML = `
    <label>Nombre<input name="name" value="${esc(r.name)}" placeholder="p. ej. Cajas Cronidol bajas"></label>
    <div class="ae-grid">
      <label>Métrica<select name="metric">${opt(state.rules.metrics, r.metric)}</select></label>
      <label>Condición<select name="op">${state.rules.ops.map(o => `<option ${o === r.op ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></label>
      <label>Umbral<input name="threshold" type="number" step="any" value="${r.threshold}"></label>
      <label>Severidad<select name="severity">${opt(Object.fromEntries(Object.entries(SEV).map(([k, v]) => [k, v.label])), r.severity)}</select></label>
    </div>
    <label>Aplicar a<select name="scope_type">${opt(SCOPE_LABEL, r.scope_type)}</select></label>
    <div class="xedit" id="ae-scope"></div>
    <label>Acción recomendada (opcional)<input name="description" value="${esc(r.description || "")}" placeholder="p. ej. Pedir cajas a Cartonajes"></label>
    <label class="check"><input type="checkbox" name="active" ${r.active ? "checked" : ""}> Alerta activa</label>`;
  const scopeField = { category: "categories", family: "families", products: "products" };
  const renderScope = (type, values) => {
    const f = scopeField[type];
    $("#ae-scope").innerHTML = f ? `<div class="xfield" data-field="${f}"><div class="xchips">${chipList(f, values)}</div>
      <div class="row"><input list="dl-${f}" placeholder="Añadir ${FIELD_LABEL[f].toLowerCase()}…" data-add="${f}"><button type="button" class="btn ghost" data-addbtn="${f}">Añadir</button></div></div>` : "";
    $$("#ae-scope [data-add]").forEach(i => i.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); addChip($("#ae-scope"), i.dataset.add); } }));
  };
  renderScope(r.scope_type, r.scope_values);
  $("#ae-scope").addEventListener("click", e => {
    const x = e.target.closest(".xchip button"); if (x) x.parentElement.remove();
    const add = e.target.closest("[data-addbtn]"); if (add) addChip($("#ae-scope"), add.dataset.addbtn);
  });
  $("#ae-body [name=scope_type]").addEventListener("input", e => renderScope(e.target.value, []));
  aeOpen(id ? "Editar alerta personalizada" : "Nueva alerta personalizada");
}

$("#ae-save").addEventListener("click", async () => {
  const body = $("#ae-body"), get = n => body.querySelector(`[name="${n}"]`);
  const btn = $("#ae-save");
  btn.disabled = true;
  try {
    if (ae.mode === "kind") {
      const payload = { exclude: readChips(body) };
      if (ae.id !== "*") {
        payload.active = get("active").checked;
        payload.severity = get("severity").value;
        payload.settings = Object.fromEntries($$("[name^='set:']", body).map(i => [i.name.slice(4), parseFloat(i.value)]));
      }
      await api("/api/alert-config/" + encodeURIComponent(ae.id), { method: "PUT", body: JSON.stringify(payload) });
      await Promise.all([loadAlertConfig(), loadSettings()]);
    } else {
      const type = get("scope_type").value;
      const rule = { name: get("name").value, description: get("description").value, metric: get("metric").value,
        op: get("op").value, threshold: get("threshold").value === "" ? null : parseFloat(get("threshold").value),
        severity: get("severity").value, scope_type: type, active: get("active").checked,
        scope_values: $$("#ae-scope .xchip").map(c => c.dataset.v) };
      if (ae.id) await api("/api/rules/" + ae.id, { method: "PUT", body: JSON.stringify(rule) });
      else await api("/api/rules", { method: "POST", body: JSON.stringify(rule) });
      await loadRules();
    }
    $("#alert-editor").close();
    load();
  } catch (e) { aeMsg(e.message); } finally { btn.disabled = false; }
});
$("#ae-delete").addEventListener("click", async () => {
  if (!ae.confirmDelete) { ae.confirmDelete = true; $("#ae-delete").textContent = "¿Seguro? Pulsa otra vez para eliminar"; return; }
  try {
    await api("/api/rules/" + ae.id, { method: "DELETE" });
    $("#alert-editor").close();
    await loadRules(); load();
  } catch (e) { aeMsg(e.message); }
});
$("#rule-new").addEventListener("click", () => openRuleEditor());

/* ---------------------------------------------------------------- alertas personalizadas */
function ruleScope(r) {
  return r.scope_type === "all" ? "Todos los productos" : `${SCOPE_LABEL[r.scope_type]}: ${r.scope_values.join(", ")}`;
}
async function loadRules() {
  state.rules = await api("/api/rules");
  $("#rules").innerHTML = state.rules.rules.length ? state.rules.rules.map(r => `
    <div class="rule ${r.active ? "" : "off"}">
      <div>${sevBadge(r.severity)} <b>${esc(r.name)}</b><div class="small muted">${esc(ruleScope(r))} · ${esc(state.rules.metrics[r.metric] || r.metric)} ${esc(r.op)} ${fmt(r.threshold, 1)}${r.description ? " · " + esc(r.description) : ""}</div></div>
      <div class="row">
        <label class="check small"><input type="checkbox" data-toggle="${r.id}" ${r.active ? "checked" : ""}> Activa</label>
        <button class="btn ghost" data-edit="${r.id}">Editar</button>
        <button class="btn ghost danger" data-del="${r.id}">Eliminar</button>
      </div>
    </div>`).join("") : `<p class="muted small">Aún no hay alertas personalizadas.</p>`;
  $$("#rules [data-toggle]").forEach(c => c.addEventListener("input", async () => {
    await api("/api/rules/" + c.dataset.toggle, { method: "PATCH", body: JSON.stringify({ active: c.checked }) });
    await loadRules(); load();
  }));
  $$("#rules [data-edit]").forEach(b => b.addEventListener("click", () => openRuleEditor(+b.dataset.edit)));
  $$("#rules [data-del]").forEach(b => b.addEventListener("click", async () => {
    if (b.dataset.confirm !== "1") { b.dataset.confirm = "1"; b.textContent = "¿Seguro?"; setTimeout(() => { b.dataset.confirm = ""; b.textContent = "Eliminar"; }, 4000); return; }
    await api("/api/rules/" + b.dataset.del, { method: "DELETE" });
    await loadRules(); load();
  }));
}
async function saveRule(rule) {
  await api("/api/rules", { method: "POST", body: JSON.stringify(rule) });
  await loadRules(); await load();
}

/* ---------------------------------------------------------------- propuestas del asistente */
function exclText(ex) {
  return Object.entries(FIELD_LABEL).filter(([f]) => ex?.[f]?.length).map(([f, l]) => `${l.toLowerCase()} ${ex[f].join(", ")}`).join("; ");
}
function proposalHtml(p, i) {
  const action = p.action || "create";
  let title = "", detail = "", button = "Aplicar";
  if (action === "create") {
    const r = p.rule || p;
    title = `${sevBadge(r.severity)} Nueva alerta: <b>${esc(r.name)}</b>`;
    detail = `${esc(ruleScope(r))} · ${esc(state.rules?.metrics?.[r.metric] || r.metric)} ${esc(r.op)} ${fmt(r.threshold, 1)}${r.description ? " · " + esc(r.description) : ""}`;
    button = "Guardar alerta";
  } else if (action === "update") {
    const b = p.before, a = p.after;
    title = `Modificar alerta: <b>${esc(b.name)}</b>`;
    const diffs = Object.keys(p.changes).map(k => {
      const show = v => k === "severity" ? SEV[v]?.label : k === "metric" ? state.rules?.metrics?.[v] : Array.isArray(v) ? v.join(", ") : k === "active" ? (v ? "activa" : "desactivada") : v;
      const FIELD = { name: "Nombre", description: "Acción", scope_type: "Aplicar a", scope_values: "Productos/familias",
        metric: "Métrica", op: "Condición", threshold: "Umbral", severity: "Severidad", active: "Estado" };
      const val = v => k === "scope_type" ? SCOPE_LABEL[v] : show(v);
      return `${esc(FIELD[k] || k)}: ${esc(String(val(b[k]) ?? "—"))} → <b>${esc(String(val(a[k]) ?? "—"))}</b>`;
    });
    detail = diffs.join(" · ");
  } else if (action === "delete") {
    title = `Eliminar alerta: <b>${esc(p.before.name)}</b>`;
    detail = esc(ruleScope(p.before));
    button = "Eliminar alerta";
  } else if (action === "auto_config") {
    title = `Alerta automática: <b>${esc(p.label)}</b>`;
    const parts = [];
    if ("active" in p) parts.push(p.active ? "activarla" : "desactivarla");
    if ("severity" in p) parts.push(p.severity === "auto" ? "severidad automática" : `severidad ${SEV[p.severity]?.label}`);
    if (p.add_exclusions && exclText(p.add_exclusions)) parts.push(`no avisar de ${exclText(p.add_exclusions)}`);
    if (p.remove_exclusions && exclText(p.remove_exclusions)) parts.push(`volver a avisar de ${exclText(p.remove_exclusions)}`);
    detail = esc(parts.join(" · "));
  }
  return `<div class="proposal">${title}<div class="small muted">${detail}</div>
    <div class="row"><button class="btn primary" data-apply="${i}">${button}</button><span class="small muted">Propuesta de la IA: no cambia nada hasta que la apliques.</span></div></div>`;
}
function bindProposals(container, proposals) {
  $$("[data-apply]", container).forEach(b => b.addEventListener("click", async () => {
    b.disabled = true;
    const p = proposals[+b.dataset.apply];
    try {
      if (STATIC) await saveRule(p.rule || p);
      else await api("/api/proposals/apply", { method: "POST", body: JSON.stringify(p) });
      b.textContent = "Aplicado ✓";
      await Promise.all([loadRules(), loadAlertConfig()]); load();
    } catch (e) { b.disabled = false; alertBox(b, e.message); }
  }));
}
$("#nl-rule-btn").addEventListener("click", async () => {
  const text = $("#nl-rule").value.trim();
  if (!text) return;
  const out = $("#nl-rule-out"), b = $("#nl-rule-btn");
  b.disabled = true;
  out.innerHTML = `<p class="small"><span class="spinner"></span>Preparando el cambio…</p>`;
  try {
    const r = await api("/api/ai/chat", { method: "POST", body: JSON.stringify({ messages: [{ role: "user", content:
      "Gestión de alertas (consulta antes listar_reglas_alerta y usa la herramienta de propuesta adecuada): " + text }] }) });
    out.innerHTML = `<div class="md small">${md(r.text)}</div>` + r.proposals.map(proposalHtml).join("");
    bindProposals(out, r.proposals);
  } catch (e) { out.innerHTML = `<div class="warn">${esc(e.message)}</div>`; } finally { b.disabled = false; }
});

/* ---------------------------------------------------------------- chat */
const SUGGESTIONS = [
  "¿Qué tengo que pedir esta semana y cuánto?",
  "¿Qué productos se quedan sin stock en los próximos 30 días?",
  "¿Dónde tengo más dinero parado y qué hago con él?",
  "¿Cómo va la cobertura de los componentes de Cronidol?",
  "Avísame si las cajas de Cronidol bajan de 5.000 unidades",
];
$("#suggestions").innerHTML = SUGGESTIONS.map(s => `<button class="chip">${esc(s)}</button>`).join("");
$$("#suggestions .chip").forEach(c => c.addEventListener("click", () => sendChat(c.textContent)));
$("#chat-form").addEventListener("submit", e => { e.preventDefault(); const t = $("#chat-input").value.trim(); if (t) sendChat(t); });
$("#chat-input").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chat-form").requestSubmit(); } });

async function sendChat(text) {
  const log = $("#chat-log");
  $("#chat-input").value = "";
  state.chat.push({ role: "user", content: text });
  log.insertAdjacentHTML("beforeend", `<div class="msg user">${esc(text)}</div>`);
  const pending = document.createElement("div");
  pending.className = "msg assistant";
  pending.innerHTML = `<span class="spinner"></span>Pensando y consultando datos…`;
  log.appendChild(pending);
  log.scrollTop = log.scrollHeight;
  $("#chat-send").disabled = true;
  try {
    const r = await api("/api/ai/chat", { method: "POST", body: JSON.stringify({ messages: state.chat }) });
    state.chat.push({ role: "assistant", content: r.text });
    pending.classList.add("md");
    const q = r.queries.length ? `<details><summary>${r.queries.length} consultas realizadas</summary>${r.queries.map(x => `<div><code>${esc(x.tool)} ${esc(JSON.stringify(x.input))}</code></div>`).join("")}</details>` : "";
    const u = r.usage;
    const cost = u ? `<div class="usage">${esc(r.model || "")} · ${new Intl.NumberFormat("es-ES").format(u.prompt_tokens + u.completion_tokens)} tokens` +
      (u.cost !== null && u.cost !== undefined ? ` · ${esc(usd(u.cost))}` : "") + `</div>` : "";
    pending.innerHTML = md(r.text) + r.proposals.map(proposalHtml).join("") + q + cost;
    bindProposals(pending, r.proposals);
  } catch (e) {
    state.chat.pop();
    pending.innerHTML = `<div class="warn">${esc(e.message)}</div>`;
  } finally {
    $("#chat-send").disabled = false;
    log.scrollTop = log.scrollHeight;
  }
}

/* ---------------------------------------------------------------- config */
const SETTING_LABELS = {
  plazo_entrega_defecto_dias: "Plazo de entrega por defecto (días) — si el proveedor no lo tiene en Odoo",
  nivel_servicio: "Nivel de servicio (0,80–0,99) para el stock de seguridad",
  periodo_revision_dias: "Periodo de revisión de compras (días)",
  semanas_historia: "Semanas de historia para la previsión",
  horizonte_semanas: "Horizonte de previsión (semanas)",
  exceso_cobertura_dias: "Exceso: cobertura superior a (días)",
  exceso_valor_min: "Exceso / sin movimiento: valor mínimo para avisar (€)",
  sin_movimiento_dias: "Sin movimiento: más de (días)",
  caducidad_aviso_dias: "Caducidad: avisar con (días) de antelación",
  anomalia_z: "Anomalía de demanda: umbral z",
  ack_dias: "Una alerta vista vuelve a aparecer tras (días)",
  abc_a: "ABC: corte A (fracción acumulada del consumo)",
  abc_b: "ABC: corte B",
  xyz_x: "XYZ: CV máximo para X",
  xyz_y: "XYZ: CV máximo para Y",
  contexto_empresa: "Contexto de la empresa para el asistente (productos, familias de referencias, cómo trabajáis…)",
};
async function loadSettings() {
  const { settings } = await api("/api/settings");
  $("#settings-form").innerHTML = Object.entries(settings).map(([k, v]) => typeof v === "string"
    ? `<label class="full">${esc(SETTING_LABELS[k] || k)}<textarea name="${k}" rows="4">${esc(v)}</textarea></label>`
    : `<label>${esc(SETTING_LABELS[k] || k)}<input name="${k}" type="number" step="any" value="${v}"></label>`).join("") +
    `<div class="full row"><button class="btn primary">Guardar y recalcular</button><span id="set-msg" class="small muted"></span></div>`;
}
$("#settings-form").addEventListener("submit", async e => {
  e.preventDefault();
  const body = Object.fromEntries([...new FormData(e.target)].map(([k, v]) =>
    [k, e.target.elements[k].tagName === "TEXTAREA" ? v : parseFloat(v)]));
  try { await api("/api/settings", { method: "PUT", body: JSON.stringify(body) }); $("#set-msg").textContent = "Guardado ✓"; load(); }
  catch (err) { $("#set-msg").textContent = err.message; }
});

/* ---------------------------------------------------------------- inicio */
$("#refresh").addEventListener("click", () => load(true));
try { const t = localStorage.getItem("inv-tab"); if (t && $("#tab-" + t)) showTab(t); } catch {}
/* ---------------------------------------------------------------- configuración (Odoo + IA)
   Demo estática: valores de ejemplo, claves bloqueadas y pruebas simuladas; no se guarda ni envía nada.
   App: prueba real de Odoo (con permisos), carga de modelos con precio y claves al Llavero de macOS. */
const PROVIDERS = {
  openrouter: { name: "OpenRouter", base: "https://openrouter.ai/api/v1",
    help: "<p>Crea la clave en <b>openrouter.ai → Keys</b> y añade créditos (pago por uso). Recomendado: ponle un <b>límite de gasto</b> a la clave.</p><p>En <b>Settings → Privacy</b>, excluye los proveedores que puedan guardar o entrenar con tus datos.</p>" },
  openai: { name: "OpenAI", base: "https://api.openai.com/v1",
    help: "<p>Crea la clave en <b>platform.openai.com → API keys</b> y añade saldo en <b>Billing</b>.</p><p><b>Ojo:</b> la suscripción de ChatGPT (Plus/Pro) <b>no incluye la API</b>; se paga aparte, por uso.</p>" },
  anthropic: { name: "Anthropic", base: "https://api.anthropic.com",
    help: "<p>Crea la clave en <b>console.anthropic.com → API Keys</b> y añade saldo (pago por uso).</p>" },
  custom: { name: "Compatible con OpenAI", base: "",
    help: "<p>Para servidores que imitan la API de OpenAI, por ejemplo <b>Ollama</b> (<code>http://localhost:11434/v1</code>) o <b>LM Studio</b>. Un modelo local no envía datos fuera del Mac, pero necesita un equipo potente y un modelo mediano o grande para usar bien las herramientas.</p>" },
};
const DEMO_MODELS = {
  anthropic: ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
  default: ["Modelo de ejemplo 1 · admite herramientas", "Modelo de ejemplo 2 · admite herramientas"],
};
const setup = { step: 1, status: null, odooOk: false, models: [], default: null };
const sf = () => $("#setup-form");
const usd = v => v === null || v === undefined ? "—" :
  new Intl.NumberFormat("es-ES", { maximumFractionDigits: v < 0.01 ? 4 : v < 1 ? 3 : 2 }).format(v) + " $";

function setMsg(el, cls, html) { el.className = "test-msg" + (cls ? " " + cls : ""); el.innerHTML = html; }

function setupFill() {
  const f = sf().elements;
  if (STATIC) {
    $("#setup-demo-note").hidden = false;
    f.odoo_url.value = "https://miempresa.odoo.com";
    f.odoo_user.value = "ruben@ejemplo.com";
    f.odoo_key.value = "demo-0000-0000-0000-0000";
    f.ai_key.value = "sk-demo-000000000000000000";
    [f.odoo_url, f.odoo_db, f.odoo_user, f.odoo_key, f.ai_key].forEach(i => i.readOnly = true);
    const saved = LS.get("connection", null);
    if (saved?.provider) sf().querySelector(`input[name=provider][value=${saved.provider}]`).checked = true;
  } else {
    const st = setup.status || { odoo: {}, ai: {} };
    f.odoo_url.value = st.odoo.url || "";
    f.odoo_db.value = st.odoo.db || "";
    f.odoo_user.value = st.odoo.user || "";
    f.odoo_key.value = "";
    f.odoo_key.placeholder = st.odoo.has_key ? "Guardada en el Llavero · déjala vacía para conservarla" : "Pega aquí tu API key de Odoo";
    f.ai_key.value = "";
    const prov = st.ai.provider || "openrouter";
    sf().querySelector(`input[name=provider][value=${prov}]`).checked = true;
    f.base_url.value = st.ai.base_url || "";
    setup.odooOk = !!st.configured;
    $("#setup-skip").hidden = !st.configured;
    $("#setup-skip").textContent = "Cancelar";
  }
  setMsg($("#test-odoo-msg"), "", "");
  providerChanged();
}

function providerChanged() {
  const f = sf().elements, p = f.provider.value;
  $("#provider-help").innerHTML = PROVIDERS[p].help;
  $("#base-url-row").hidden = p !== "custom";
  if (!STATIC) {
    const st = setup.status?.ai || {};
    f.ai_key.placeholder = st.has_key && st.provider === p ? "Guardada en el Llavero · déjala vacía para conservarla"
      : (p === "custom" ? "Opcional en modelos locales" : "Pega aquí tu clave");
  }
  const sel = $("#model-select");
  sel.disabled = true;
  sel.innerHTML = `<option>Pulsa «Probar y cargar modelos»</option>`;
  $("#model-tools").hidden = true;
  $("#model-info").innerHTML = "";
  setup.models = [];
  setMsg($("#test-ai-msg"), "", "");
}

function setupStep(n) {
  setup.step = n;
  $$("#setup .step").forEach(s => s.hidden = +s.dataset.step !== n);
  $$("#setup .stepper li").forEach(li => {
    const k = +li.dataset.step;
    li.toggleAttribute("aria-current", k === n);
    if (k === n) li.setAttribute("aria-current", "step");
    li.classList.toggle("done", k < n);
  });
  $("#setup-back").hidden = n === 1;
  $("#setup-next").textContent = n === 1 ? "Siguiente →" : "Guardar y entrar";
}

async function openSetup() {
  if (!STATIC) { try { setup.status = await api("/api/setup"); } catch {} }
  setupFill(); setupStep(1);
  if (!$("#setup").open) $("#setup").showModal();
}

async function fakeTest(btn, msg, okText) {
  btn.disabled = true; setMsg(msg, "", `<span class="spinner"></span>Comprobando…`);
  await new Promise(r => setTimeout(r, 900));
  setMsg(msg, "ok", esc(okText)); btn.disabled = false;
}

/* ---- Odoo */
$("#test-odoo").addEventListener("click", async () => {
  const btn = $("#test-odoo"), msg = $("#test-odoo-msg"), f = sf().elements;
  if (STATIC) return fakeTest(btn, msg, "Conexión simulada (demo): Odoo 18 · usuario de ejemplo · solo lectura");
  btn.disabled = true; setMsg(msg, "", `<span class="spinner"></span>Conectando con Odoo…`);
  try {
    const r = await api("/api/setup/odoo/test", { method: "POST", body: JSON.stringify(
      { url: f.odoo_url.value, db: f.odoo_db.value, user: f.odoo_user.value, key: f.odoo_key.value }) });
    if (r.url) f.odoo_url.value = r.url;
    if (!f.odoo_db.value) f.odoo_db.value = r.db;
    const denied = Object.entries(r.access).filter(([, v]) => v === false).map(([k]) => k);
    const missing = Object.entries(r.access).filter(([, v]) => v === null).map(([k]) => k);
    setup.odooOk = true;
    setMsg(msg, "ok", `Conectado a Odoo ${esc(r.version)} como <b>${esc(r.user_name)}</b> (base de datos ${esc(r.db)})` +
      (denied.length ? `<br><span class="warn-inline">Sin permiso de lectura en: ${esc(denied.join(", "))}. Esas partes del panel saldrán vacías.</span>` : "") +
      (missing.length ? `<br><span class="muted">No instalado: ${esc(missing.join(", "))}</span>` : ""));
  } catch (e) {
    setup.odooOk = false;
    setMsg(msg, "err", esc(e.message));
  } finally { btn.disabled = false; }
});

/* ---- IA: modelos con precio */
function modelLabel(m) {
  const price = m.free ? "gratis" : (m.price_in !== null ? `${usd(m.price_in)} / ${usd(m.price_out)} por 1M tokens` : "");
  return [m.name, price, m.tools === false ? "no puede consultar datos" : ""].filter(Boolean).join(" · ");
}
function renderModels() {
  const sel = $("#model-select"), q = $("#model-filter").value.trim().toLowerCase();
  const freeOnly = $("#model-free-only").checked, cur = sel.value || setup.default;
  const list = setup.models.filter(m => (!q || m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q)) && (!freeOnly || m.free));
  const groups = [
    ["Recomendado", list.filter(m => m.id === setup.default)],
    ["De pago", list.filter(m => m.id !== setup.default && !m.free && m.tools !== false)],
    ["Gratuitos (límites bajos; pueden usar tus datos)", list.filter(m => m.id !== setup.default && m.free && m.tools !== false)],
    ["No pueden consultar datos (sin herramientas)", list.filter(m => m.tools === false)],
  ].filter(([, items]) => items.length);
  sel.innerHTML = groups.map(([label, items]) => `<optgroup label="${esc(label)}">` + items.map(m =>
    `<option value="${esc(m.id)}" ${m.tools === false ? "disabled" : ""}>${esc(modelLabel(m))}</option>`).join("") + "</optgroup>").join("")
    || `<option disabled>Ningún modelo coincide</option>`;
  if ([...sel.options].some(o => o.value === cur && !o.disabled)) sel.value = cur;
  modelInfo();
}
function modelInfo() {
  const m = setup.models.find(x => x.id === $("#model-select").value);
  if (!m) { $("#model-info").innerHTML = ""; return; }
  const parts = [];
  if (m.price_in !== null) {
    // Una pregunta típica: ~40.000 tokens de entrada (datos consultados) y ~1.500 de salida
    const est = (40000 * m.price_in + 1500 * m.price_out) / 1e6;
    parts.push(m.free ? "Gratuito" : `Entrada ${usd(m.price_in)} · salida ${usd(m.price_out)} por millón de tokens · una pregunta típica ≈ ${usd(est)}`);
  } else parts.push("El proveedor no publica el precio por API: consúltalo en su web.");
  if (m.context) parts.push(`contexto ${new Intl.NumberFormat("es-ES").format(m.context)} tokens`);
  let html = esc(parts.join(" · "));
  if (m.free) html += `<br><span class="warn-inline">Los modelos gratuitos tienen límites diarios bajos (una pregunta puede hacer 5–10 llamadas) y su proveedor puede guardar o usar lo que envías. No recomendado con datos reales.</span>`;
  $("#model-info").innerHTML = html;
}
$("#model-filter").addEventListener("input", renderModels);
$("#model-free-only").addEventListener("input", renderModels);
$("#model-select").addEventListener("change", modelInfo);

$("#test-ai").addEventListener("click", async () => {
  const btn = $("#test-ai"), msg = $("#test-ai-msg"), f = sf().elements, p = f.provider.value, sel = $("#model-select");
  if (STATIC) {
    await fakeTest(btn, msg, `Clave aceptada (simulado). En la app se cargarán los modelos de tu cuenta de ${PROVIDERS[p].name}.`);
    sel.innerHTML = (DEMO_MODELS[p] || DEMO_MODELS.default).map(m => `<option>${esc(m)}</option>`).join("");
    sel.disabled = false;
    return;
  }
  btn.disabled = true; setMsg(msg, "", `<span class="spinner"></span>Comprobando la clave y cargando modelos…`);
  try {
    const r = await api("/api/setup/ai/test", { method: "POST", body: JSON.stringify(
      { provider: p, key: f.ai_key.value, base_url: f.base_url.value }) });
    setup.models = r.models; setup.default = r.default;
    const st = setup.status?.ai;
    if (st && st.provider === p && st.model && r.models.some(m => m.id === st.model)) setup.default = st.model === r.default ? r.default : setup.default;
    $("#model-tools").hidden = false;
    $("#model-filter").value = ""; $("#model-free-only").checked = false;
    sel.disabled = false;
    renderModels();
    if (st && st.provider === p && st.model && [...sel.options].some(o => o.value === st.model)) { sel.value = st.model; modelInfo(); }
    let acc = `Clave válida · ${r.models.length} modelos disponibles`;
    const a = r.account;
    if (a) {
      if (a.usage !== null && a.usage !== undefined) acc += ` · gastado ${usd(a.usage)}`;
      acc += a.limit ? ` · límite ${usd(a.limit)}` : ` · <b>sin límite de gasto</b> (recomendado ponerle uno en OpenRouter)`;
    }
    setMsg(msg, "ok", acc);
  } catch (e) {
    setMsg(msg, "err", esc(e.message));
  } finally { btn.disabled = false; }
});

sf().addEventListener("change", e => { if (e.target.name === "provider") providerChanged(); });
$$("#setup [data-toggle-pw]").forEach(b => b.addEventListener("click", () => {
  const i = b.previousElementSibling; i.type = i.type === "password" ? "text" : "password";
  b.textContent = i.type === "password" ? "Mostrar" : "Ocultar";
}));
$("#setup-back").addEventListener("click", () => setupStep(1));
$("#setup-skip").addEventListener("click", () => { LS.set("setup-seen", true); $("#setup").close(); });
// Sin configurar, la app no puede funcionar: no se cierra con Escape
$("#setup").addEventListener("cancel", e => { if (!STATIC && !setup.status?.configured) e.preventDefault(); });

$("#setup-next").addEventListener("click", async () => {
  const f = sf().elements, sel = $("#model-select");
  if (setup.step === 1) {
    if (!STATIC && !setup.odooOk) {
      setMsg($("#test-odoo-msg"), "err", "Pulsa «Probar conexión» y comprueba que Odoo responde antes de seguir.");
      return;
    }
    return setupStep(2);
  }
  if (sel.disabled || !sel.value) {
    setMsg($("#test-ai-msg"), "err", "Prueba la clave para cargar los modelos y elige uno.");
    return;
  }
  if (STATIC) {
    // Nunca se guardan claves en el navegador: solo el proveedor y el modelo elegidos
    LS.set("connection", { provider: f.provider.value, model: sel.value, odoo: f.odoo_url.value, user: f.odoo_user.value, at: new Date().toISOString() });
    LS.set("setup-seen", true);
    $("#setup").close();
    renderConnection();
    return;
  }
  const btn = $("#setup-next");
  btn.disabled = true;
  try {
    setup.status = await api("/api/setup/save", { method: "POST", body: JSON.stringify({
      odoo: { url: f.odoo_url.value, db: f.odoo_db.value, user: f.odoo_user.value, key: f.odoo_key.value },
      ai: { provider: f.provider.value, model: sel.value, base_url: f.base_url.value, key: f.ai_key.value },
    }) });
    $("#setup").close();
    await refreshConnection();
    await load(true);
    maybeTour();
  } catch (e) {
    setMsg($("#test-ai-msg"), "err", esc(e.message));
  } finally { btn.disabled = false; }
});

async function refreshConnection() {
  if (!STATIC) { try { setup.status = await api("/api/setup"); } catch {} }
  renderConnection();
}
function renderConnection() {
  const chip = $("#conn-chip");
  chip.hidden = false;
  $("#conn-card").hidden = false;
  let c = null, usage = "";
  if (STATIC) {
    const s = LS.get("connection", null);
    if (s) c = { odoo: s.odoo, user: s.user + " · solo lectura · ejemplo", provider: PROVIDERS[s.provider].name, model: s.model };
  } else if (setup.status?.configured) {
    const st = setup.status;
    c = { odoo: st.odoo.url, user: `${st.odoo.user} · base de datos ${st.odoo.db || "detectada"} · solo lectura`,
          provider: st.ai.provider_name, model: st.ai.model };
    const u = st.usage_month;
    usage = `<div class="kpi"><div class="label">Uso de la IA este mes</div><div class="value">${u.preguntas} preguntas</div>
      <div class="hint">${u.coste ? usd(u.coste) : "0 $"}${u.sin_coste ? ` · ${u.sin_coste} sin coste informado por el proveedor` : ""} · ${new Intl.NumberFormat("es-ES").format(u.tokens)} tokens</div></div>`;
  }
  chip.classList.toggle("off", !c);
  chip.textContent = c ? `Odoo · ${c.provider}` : "Configurar conexión";
  $("#conn-summary").innerHTML = c ? `
    <div class="kpi"><div class="label">Odoo</div><div class="value">${esc(c.odoo)}</div><div class="hint">${esc(c.user)}</div></div>
    <div class="kpi"><div class="label">Asistente IA</div><div class="value">${esc(c.provider)}</div><div class="hint">${esc(c.model)}</div></div>${usage}`
    : `<p class="muted small">Aún no has configurado la conexión. Pulsa «Cambiar conexión».</p>`;
}
$("#conn-chip").addEventListener("click", openSetup);
$("#conn-edit").addEventListener("click", openSetup);

async function checkVersion() {
  try {
    const v = await api("/api/version");
    $("#app-version").textContent = "v" + v.current;
    if (v.newer && v.url) {
      // Descargarla con el navegador le pondría la marca de cuarentena: se actualiza desde Terminal
      const cmd = `curl -fsSL https://raw.githubusercontent.com/Thothalpha/panel-inventario-app/main/scripts/instalar-mac.sh | bash`;
      const b = $("#update-banner");
      b.hidden = false;
      b.innerHTML = `Hay una versión nueva: <b>${esc(v.latest)}</b> (tienes la ${esc(v.current)}). ` +
        `<a href="${esc(v.url)}" target="_blank" rel="noopener">Ver novedades</a>.<br>` +
        `Para actualizar, abre <b>Terminal</b>, pega este comando y pulsa Intro: ` +
        `<code class="cmd">${esc(cmd)}</code> <button class="btn ghost" id="copy-update">Copiar</button>`;
      $("#copy-update").addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(cmd); $("#copy-update").textContent = "Copiado ✓"; }
        catch { $("#copy-update").textContent = "Selecciona y copia el texto"; }
      });
    }
  } catch {}
}

/* ---------------------------------------------------------------- guía de uso (recorrido) */
const TOUR = [
  { title: "Bienvenido a Panel Inventario",
    text: "En un par de minutos te enseño todo lo que puedes hacer: ver el estado del inventario, saber qué pedir, decidir qué alertas quieres y preguntar a la IA. Usa «Siguiente» o las flechas del teclado; puedes salir cuando quieras." },
  { tab: "resumen", target: "#kpis", title: "Los indicadores clave",
    text: "Valor del stock, rotación, alertas urgentes, cuánto hay que pedir, exceso, stock parado y compras en curso. Se calculan con tus datos de Odoo cada vez que abres la app o pulsas «Actualizar»." },
  { tab: "resumen", target: ".ai-card", title: "Resumen con IA",
    text: "Pulsa «Generar resumen» y el asistente revisa todo el inventario y te dice en una página qué pedir, qué riesgos hay y dónde hay dinero parado. Ideal para empezar la semana." },
  { tab: "resumen", target: () => $("#ch-coverage")?.closest(".card"), title: "Gráficos",
    text: "Cuántos productos están en riesgo de rotura, qué tipos de alerta tienes, el valor por categoría y la matriz ABC/XYZ (importancia y regularidad de cada producto). Pasa el ratón por encima para ver las cifras." },
  { tab: "productos", target: () => $("#f-text")?.closest(".filters"), title: "Buscar y filtrar productos",
    text: "Busca por referencia o nombre y filtra por categoría, clase ABC o situación: en riesgo, con pedido sugerido, en exceso o sin movimiento. Pulsa los títulos de las columnas para ordenar." },
  { tab: "productos", target: "#products", title: "El detalle de cada producto",
    text: "Pulsa una fila para ver su demanda semanal y la previsión, cuándo se quedaría sin stock, la cantidad sugerida a pedir, el stock por lote y ubicación y en qué productos se usa. Desde ahí también puedes preguntar a la IA por ese producto." },
  { tab: "alertas", target: "#alert-filters", title: "Tus alertas, por urgencia",
    text: "Filtra por severidad: crítica, alta, media o baja. El número rojo de la pestaña son las críticas y altas que aún no has visto." },
  { tab: "alertas", target: () => $("#a-search")?.closest(".filters"), title: "Filtra por tipo, categoría o familia",
    text: "Encuentra rápido las alertas de una categoría de Odoo o de una familia de referencia (BOX, ENV, MC…). «Marcar visibles como vistas» las archiva todas de golpe; vuelven a salir pasados unos días si el problema sigue." },
  { tab: "alertas", target: () => $("#alert-list .alert") || $("#alert-list"), title: "Cada alerta",
    text: "Te dice qué pasa, qué hacer y su impacto. En «Opciones» puedes dejar de recibir avisos de ese producto (solo de ese tipo o de ninguno) o configurar ese tipo de alerta." },
  { tab: "alertas", target: "#auto-config-card", title: "Tú decides qué alertas quieres",
    text: "Activa o desactiva cada tipo de alerta automática, cámbiale la severidad y pulsa «Editar» para ajustar sus umbrales o excluir productos, categorías o familias." },
  { tab: "alertas", target: "#global-excl-card", title: "Productos sin alertas",
    text: "Lo que añadas aquí no generará ninguna alerta. Útil para productos descatalogados, muestras o material que no gestionas tú." },
  { tab: "alertas", target: "#rules-card", title: "Tus propias alertas",
    text: "Crea alertas a tu medida con «+ Nueva alerta» (por ejemplo, «avísame si las cajas de Cronidol bajan de 5.000»), o escríbelo y pulsa «Pedir a la IA». Puedes editarlas, pausarlas o eliminarlas cuando quieras." },
  { tab: "asistente", target: "#chat-form", title: "Pregunta lo que quieras",
    text: "El asistente consulta el panel y tu Odoo (solo lectura) para responder: qué pedir esta semana, cómo va un producto, qué compras están retrasadas… También puede crear, cambiar o eliminar alertas por ti: siempre te enseña el cambio y tú lo aplicas con un botón." },
  { tab: "asistente", target: "#suggestions", title: "Ideas para empezar",
    text: "Pulsa cualquiera de estas preguntas de ejemplo. Debajo de cada respuesta verás qué consultas ha hecho y cuánto ha costado." },
  { tab: "config", target: "#settings-form", title: "Ajusta la app a tu forma de trabajar",
    text: "Plazo de entrega por defecto, nivel de servicio, umbrales de alertas… Y en «Contexto de la empresa» cuéntale al asistente cómo trabajáis: sus respuestas mejorarán mucho." },
  { target: "#conn-chip", title: "Tu conexión",
    text: "Aquí cambias la conexión con Odoo o el modelo de IA. En Configuración → Conexiones verás también cuánto llevas gastado en IA este mes." },
  { target: "#refresh", title: "Datos al día",
    text: "La app vuelve a leer Odoo cada 15 minutos; pulsa «Actualizar» si acabas de hacer cambios en Odoo y quieres verlos ya." },
  { target: "#tour-btn", title: "¡Listo!",
    text: "Puedes volver a ver esta guía cuando quieras desde este botón. Cuando haya una versión nueva de la app, te avisará arriba con las instrucciones para actualizar." },
];
const tour = { i: 0, on: false };

function tourEls() {
  if (!$("#tour-pop")) {
    document.body.insertAdjacentHTML("beforeend", `
      <div id="tour-block" class="tour-block" hidden></div>
      <div id="tour-spot" class="tour-spot" hidden></div>
      <div id="tour-pop" class="tour-pop" role="dialog" aria-modal="true" aria-labelledby="tour-title" hidden>
        <div class="tour-step" id="tour-step"></div>
        <h3 id="tour-title"></h3>
        <p id="tour-text"></p>
        <div class="tour-actions">
          <button class="btn ghost" id="tour-skip">Saltar guía</button>
          <span class="spacer"></span>
          <button class="btn ghost" id="tour-prev">← Anterior</button>
          <button class="btn primary" id="tour-next">Siguiente →</button>
        </div>
      </div>`);
    $("#tour-skip").addEventListener("click", () => endTour());
    $("#tour-prev").addEventListener("click", () => tourGo(tour.i - 1));
    $("#tour-next").addEventListener("click", () => tour.i >= TOUR.length - 1 ? endTour() : tourGo(tour.i + 1));
    $("#tour-block").addEventListener("click", () => {});
  }
}
function startTour() {
  tourEls();
  $$("dialog[open]").forEach(d => d.close());
  tour.on = true;
  ["#tour-block", "#tour-pop"].forEach(s => $(s).hidden = false);
  tourGo(0);
}
function endTour() {
  tour.on = false;
  ["#tour-block", "#tour-spot", "#tour-pop"].forEach(s => $(s).hidden = true);
  LS.set("tour-done", true);
}
function tourTarget(step) {
  if (!step.target) return null;
  const el = typeof step.target === "function" ? step.target() : $(step.target);
  return el && !el.hidden && el.offsetParent !== null ? el : null;
}
function tourGo(i) {
  tour.i = Math.max(0, Math.min(TOUR.length - 1, i));
  const step = TOUR[tour.i];
  if (step.tab) showTab(step.tab);
  $("#tour-step").textContent = `Paso ${tour.i + 1} de ${TOUR.length}`;
  $("#tour-title").textContent = step.title;
  $("#tour-text").textContent = step.text;
  $("#tour-prev").disabled = tour.i === 0;
  $("#tour-next").textContent = tour.i === TOUR.length - 1 ? "Terminar" : "Siguiente →";
  const el = tourTarget(step);
  if (el) {
    // Elementos altos: arriba del todo (bajo la cabecera) para que quepa la explicación debajo
    const tall = el.getBoundingClientRect().height > window.innerHeight * 0.4;
    el.scrollIntoView({ block: tall ? "start" : "center", behavior: "instant" });
    if (tall) window.scrollBy(0, -20);
  }
  requestAnimationFrame(() => tourPlace(el));
  $("#tour-next").focus();
}
function tourPlace(el) {
  const spot = $("#tour-spot"), pop = $("#tour-pop");
  const vw = window.innerWidth, vh = window.innerHeight, pad = 8, gap = 14;
  $("#tour-block").classList.toggle("dim", !el);
  if (!el) {
    spot.hidden = true;
    pop.style.left = Math.max(16, (vw - pop.offsetWidth) / 2) + "px";
    pop.style.top = Math.max(16, (vh - pop.offsetHeight) / 2) + "px";
    return;
  }
  const r = el.getBoundingClientRect();
  const pw = pop.offsetWidth, ph = pop.offsetHeight;
  const top = Math.max(4, r.top - pad), left = Math.max(4, r.left - pad);
  // Elementos más altos que el hueco disponible: se resalta su parte superior y la explicación va debajo
  const room = vh - top - ph - gap - 16;
  const height = Math.min(r.height + pad * 2, vh - top - 4, Math.max(120, room));
  const width = Math.min(r.width + pad * 2, vw - left - 4);
  Object.assign(spot.style, { top: top + "px", left: left + "px", width: width + "px", height: height + "px" });
  spot.hidden = false;
  let pt, pl = Math.min(Math.max(16, left), vw - pw - 16);
  if (vh - (top + height) >= ph + gap + 16) pt = top + height + gap;   // debajo
  else if (top >= ph + gap + 16) pt = top - ph - gap;                  // encima
  else { pt = vh - ph - 16; pl = vw - pw - 16; }                        // no cabe: esquina inferior derecha
  pop.style.top = pt + "px"; pop.style.left = pl + "px";
}
window.addEventListener("resize", () => { if (tour.on) tourPlace(tourTarget(TOUR[tour.i])); });
document.addEventListener("keydown", e => {
  if (!tour.on) return;
  if (e.key === "Escape") { e.preventDefault(); endTour(); }
  else if (e.key === "ArrowRight") { e.preventDefault(); $("#tour-next").click(); }
  else if (e.key === "ArrowLeft" && tour.i > 0) { e.preventDefault(); tourGo(tour.i - 1); }
});
$("#tour-btn").addEventListener("click", startTour);
function maybeTour() {
  if (!LS.get("tour-done", false) && !$$("dialog[open]").length) setTimeout(startTour, 400);
}

/* ---- arranque */
async function start() {
  if (STATIC) {
    renderConnection();
    if (!LS.get("setup-seen", false)) openSetup();
    start();
    return;
  }
  await refreshConnection();
  loadRules(); loadSettings(); loadAlertConfig(); checkVersion();
  if (setup.status?.configured) { await load(); maybeTour(); }
  else {
    $("#warnings").innerHTML = `<div class="warn">Configura la conexión con Odoo y el proveedor de IA para empezar.</div>`;
    openSetup();
  }
}

$("#review-close").addEventListener("click", () => { $("#review").hidden = true; LS.set("review-closed", true); });
start();
