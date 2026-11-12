"use strict";

/* Node Market: a read-only view of vinted_scanner.db served by dashboard.py. */

const MODES = { mini_pc: "Mini PC", ram: "RAM", cpu: "CPU" };
const PAGE_SIZE = 48;
const VERDICT_CLASS = {
  "Strong candidate": "strong",
  "Worth evaluating": "worth",
  "Cheap base, potentially upgradeable": "upgrade",
  "Weak as configured; upgrade to be verified": "upgrade",
  "Weak for a Proxmox node": "weak",
};
const TIER_CLASS = { HIGH: "strong", MEDIUM: "worth", "TO VERIFY": "upgrade" };
const STATUS_LABEL = {
  notified: "Alert sent", dry_run: "Dry run", failed: "Not delivered",
  filtered: "Filtered out", ignored: "Not a candidate", legacy: "Imported ID",
};
// Availability read by the tracker (availability.py); "active" needs no tag.
const AVAILABILITY_LABEL = {
  active: "For sale", reserved: "Reserved", sold: "Sold", closed: "Closed", removed: "Removed",
};
const AVAILABILITY_FILTER = [
  ["", "Availability"], ["for_sale", "Still for sale"], ["sold", "Sold"],
  ["gone", "Sold, closed or removed"], ["unchecked", "Not checked yet"],
];
const GONE = ["sold", "closed", "removed"];
const STATUS_FILTER = {
  mini_pc: [["alerts", "Alerts sent"], ["all", "Everything seen"], ["filtered", "Filtered out"], ["failed", "Not delivered"]],
  ram: [["alerts", "Alerts sent"], ["all", "Everything seen"], ["ignored", "Not a candidate"], ["failed", "Not delivered"]],
  cpu: [["alerts", "Alerts sent"], ["all", "Everything seen"], ["ignored", "Not a candidate"], ["failed", "Not delivered"]],
};
const FACET_FILTERS = {
  mini_pc: [["platform", "Platform"], ["generation", "Generation"], ["verdict", "Verdict"]],
  ram: [["tier", "Tier"]],
  cpu: [["cpu", "CPU"]],
};
const SORTS = {
  mini_pc: [["newest", "Newest first"], ["perf_value", "Most CPU per euro"], ["fastest", "Fastest CPU"], ["value", "Best score per euro"], ["score", "Highest score"], ["price", "Price: low to high"], ["price_desc", "Price: high to low"]],
  ram: [["newest", "Newest first"], ["price", "Price: low to high"], ["price_desc", "Price: high to low"]],
  cpu: [["newest", "Newest first"], ["price", "Price: low to high"], ["price_desc", "Price: high to low"]],
};
// Hardware filters for mini PCs; min_mt options are built from the first reference CPU.
const HARDWARE_FILTERS = [
  ["min_threads", "Threads", [["4", "4+ threads"], ["6", "6+ threads"], ["8", "8+ threads"], ["12", "12+ threads"]]],
  ["min_ram", "RAM", [["8", "8 GB+"], ["16", "16 GB+"], ["32", "32 GB+"]]],
];
const PERF_STEPS = [1, 1.5, 2, 3];
const RUN_STATE = {
  ok: ["✓", "Completed"],
  problems: ["!", "Completed with problems"],
  failed: ["✕", "Failed"],
  running: ["…", "Interrupted"],
};
const ISSUE_TITLE = {
  crash: "Scanner crashed", config: "Configuration or local file error",
  rate_limit: "Vinted rate limit (HTTP 429)", http: "Vinted HTTP error", network: "Network error",
  vinted_response: "Unexpected Vinted response", listing_page: "Listing page not read",
  processing: "Listing processing error", notification: "Notification not delivered",
  local_database: "Local database error",
};

const view = document.getElementById("view");
const drawer = document.getElementById("drawer");
const drawerBody = document.getElementById("drawer-body");
const tooltip = document.getElementById("tooltip");
const searchInput = document.getElementById("search-input");

/* -- helpers ---------------------------------------------------------------- */
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
// Only http(s) links reach href/src, whatever the database contains.
const safeUrl = (value) => /^https?:\/\//i.test(value || "") ? esc(value) : "";
const euro = (value) => value == null ? "–" : new Intl.NumberFormat("en-IE", {
  style: "currency", currency: "EUR", maximumFractionDigits: Number.isInteger(value) ? 0 : 2,
}).format(value);
const dateTime = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const dayOnly = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long" });
const when = (iso) => iso ? dateTime.format(new Date(iso)) : "–";
const duration = (s) => s == null ? "–" : s < 60 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;
const median = (values) => {
  const sorted = values.filter((v) => v != null).sort((a, b) => a - b);
  if (!sorted.length) return null;
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
};

// 9501 -> "9.5k", 15000 -> "15k", 0 -> "0".
const thousands = (value) => value == null ? "–" : value >= 1000 ? `${Number((value / 1000).toFixed(1))}k` : String(value);
let referencesPromise = null;
const loadReferences = () => (referencesPromise ??= api("references").then((data) => data, () => ({ items: [], source: "" })));
let REFS = [];
// "×1.5 N100" against the first reference, or "" when either benchmark is missing.
function vsFirstReference(mt) {
  const ref = REFS[0];
  return mt && ref?.mt ? `×${(mt / ref.mt).toFixed(1)} ${ref.name}` : "";
}

async function api(path, params = {}) {
  const query = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null));
  const response = await fetch(`/api/${path}${query.size ? `?${query}` : ""}`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

/* -- routing ---------------------------------------------------------------- */
function route() {
  const [path, query = ""] = location.hash.replace(/^#\/?/, "").split("?");
  const page = ["overview", "runs", ...Object.keys(MODES)].includes(path) ? path : "overview";
  return { page, params: Object.fromEntries(new URLSearchParams(query)) };
}

function go(page, params = {}) {
  const query = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null));
  location.hash = `#/${page}${query.size ? `?${query}` : ""}`;
}

async function render() {
  const { page, params } = route();
  document.querySelectorAll("[data-tab]").forEach((tab) => {
    tab.toggleAttribute("aria-current", tab.dataset.tab === page);
    if (tab.dataset.tab === page) tab.setAttribute("aria-current", "page");
  });
  searchInput.value = params.q || "";
  hideTooltip();
  try {
    REFS = (await loadReferences()).items || [];
    if (page === "overview") await renderOverview(params);
    else if (page === "runs") await renderRuns(params);
    else await renderCatalog(page, params);
  } catch (error) {
    view.innerHTML = `<p class="error">${esc(error.message)}</p>`;
  }
}

/* -- shared pieces ---------------------------------------------------------- */
function markClass(item) {
  if (item.mode === "ram") return TIER_CLASS[(item.tier || "").split(" — ")[0]] || "";
  return VERDICT_CLASS[item.verdict] || "";
}

function badge(item) {
  let text;
  if (item.mode === "mini_pc") text = item.score == null ? null : `${item.score}/100`;
  else if (item.mode === "ram") text = (item.tier || "").split(" — ")[0] || null;
  else text = item.cores ? `${item.cores} cores · ${item.generation}th gen` : null;
  if (!text) return "";
  const mark = item.mode === "cpu" ? "" : `<i class="mark mark--${markClass(item)}"></i>`;
  return `<span class="badge">${mark}${esc(text)}</span>`;
}

function buildLine(item) {
  if (item.mode === "mini_pc" && item.total_32_eur != null) return `With 32 GB: ${euro(item.total_32_eur)}`;
  if (item.mode === "cpu" && item.build_estimate_eur != null) return `Build: ${euro(item.build_estimate_eur)}`;
  if (item.mode === "ram" && item.ram_gb) return `${item.ram_gb} GB stated`;
  return "";
}

function benchLine(item) {
  if (item.mode !== "mini_pc" || !item.bench_mt) return "";
  return [`MT ${thousands(item.bench_mt)}`, vsFirstReference(item.bench_mt)].filter(Boolean).join(" · ");
}

function metaLine(item) {
  if (item.mode === "mini_pc") return item.platform || item.model || item.matched_query || "";
  if (item.mode === "cpu") return item.cpu || "";
  return item.matched_query || "";
}

function card(item) {
  const photo = safeUrl(item.image_url)
    ? `<img src="${safeUrl(item.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : "";
  const tag = !["notified", "dry_run"].includes(item.status) ? STATUS_LABEL[item.status] || item.status
    : item.availability && item.availability !== "active" ? AVAILABILITY_LABEL[item.availability] : "";
  const status = tag ? `<span class="status-tag">${esc(tag)}</span>` : "";
  const gone = GONE.includes(item.availability) ? " card--gone" : "";
  return `
    <button class="card${gone}" type="button" data-open="${esc(item.mode)}/${esc(item.item_id)}">
      <div class="card__photo${photo ? "" : " no-photo"}">${photo}${badge(item)}${status}</div>
      <div class="card__price">${euro(item.price_eur)}</div>
      <div class="card__build">${esc(buildLine(item))}</div>
      <div class="card__title">${esc(item.title)}</div>
      <div class="card__meta">${esc(metaLine(item))}</div>
      ${benchLine(item) ? `<div class="card__bench">${esc(benchLine(item))}</div>` : ""}
    </button>`;
}

function runState(status) {
  const [icon, label] = RUN_STATE[status] || RUN_STATE.running;
  return `<span class="state state--${esc(status)}"><span aria-hidden="true">${icon}</span>${label}</span>`;
}

/* -- overview --------------------------------------------------------------- */
async function renderOverview(params) {
  const [overview, list] = await Promise.all([
    api("overview"),
    api("listings", { mode: "mini_pc", limit: 200 }),
  ]);
  const market = overview.market;
  const since = overview.daily.length ? dayOnly.format(new Date(overview.daily[0].day)) : null;
  const run = overview.last_run;
  const headline = market.count
    ? `${plural(market.count, "mini PC")} alerted since ${since}. A 32 GB node costs a median ${euro(market.median_total_32)}.`
    : "No mini-PC alerts yet.";
  const intro = market.count
    ? `Median asking price ${euro(market.median_price)}, median score ${market.median_score}/100. ` +
      "Prices are listing prices plus the configured RAM budget, without shipping or buyer-protection fees."
    : "Run the scanner, or restore past alerts with telegram_import.py, and this page fills in.";

  view.innerHTML = `
    <section class="hero">
      <div>
        <h1>${esc(headline)}</h1>
        <p>${esc(intro)}</p>
        ${run ? `<p class="last-run">Last run ${runState(run.status)} ${esc(when(run.started_at))}, took ${duration(run.duration_s)}</p>` : ""}
      </div>
      <div class="hero__modes">
        ${Object.entries(MODES).map(([mode, label]) => {
          const stats = overview.modes[mode];
          return `<a class="mode-link" href="#/${mode}">
            <span>${label} alerts${stats.last_alert ? `, latest ${esc(when(stats.last_alert))}` : ""}</span>
            <strong>${stats.alerts}</strong></a>`;
        }).join("")}
      </div>
    </section>
    ${market.count ? `
    <section class="block">
      <div class="block__head">
        <div>
          <h2>Value map</h2>
          <p class="subtle">CPU performance against the 16 GB build cost. Up and to the left is better; orange dots are the best deals,
            where no other listing is both cheaper and faster.</p>
        </div>
      </div>
      ${valueMap(list.items, market.value_frontier, REFS)}
    </section>
    <section class="block">
      <div class="block__head">
        <div>
          <h2>Prices by platform</h2>
          <p class="subtle">Each dot is one alerted listing; the bar marks the median. Select a dot to open it.</p>
        </div>
        <div class="board__controls" role="group" aria-label="Price shown">
          <button class="pill" type="button" data-metric="price_eur" aria-pressed="${params.metric !== "total_32_eur"}">Asking price</button>
          <button class="pill" type="button" data-metric="total_32_eur" aria-pressed="${params.metric === "total_32_eur"}">With 32 GB</button>
        </div>
      </div>
      ${priceBoard(list.items, params.metric === "total_32_eur" ? "total_32_eur" : "price_eur")}
    </section>
    ${shelf("Most CPU per euro", "PassMark multithread points per euro of the 16 GB build.", market.best_perf_value, "#/mini_pc?sort=perf_value")}
    ${shelf("Best score per euro", "Score per euro of the 16 GB build.", market.best_value, "#/mini_pc?sort=value")}
    ${shelf("Cheapest with 32 GB", "Lowest total once RAM is upgraded to 32 GB.", market.cheapest_32, "#/mini_pc?sort=price")}
    ${shelf("Highest score", "Best current specification for a Proxmox node.", market.top_score, "#/mini_pc?sort=score")}
    ${market.availability?.checked ? `
    <section class="block">
      <div class="block__head">
        <div>
          <h2>How fast they sell</h2>
          <p class="subtle">The scanner re-opens alerted listings about once a day. ${market.availability.gone} of
            ${market.availability.checked} checked are gone (${market.availability.sold} marked sold by Vinted; “closed” and “removed”
            give no reason). Median days count only listings whose sale is known to within 1.5 days
            (number in brackets); older alerts found already gone are left out.</p>
        </div>
      </div>
      ${sellTable(market.by_platform)}
    </section>` : ""}
    <section class="block">
      <div class="block__head"><h2>By CPU generation</h2></div>
      ${generationTable(market.by_generation)}
    </section>
    <section class="block">
      <div class="block__head">
        <div>
          <h2>CPU price guide</h2>
          <p class="subtle">Cheapest performance first: median euros of the 16 GB build per 1,000 PassMark multithread points.
            ${esc(overview.references?.source || "")}. Scores of older alerts used the earlier tier scale, so compare history by performance per euro.</p>
        </div>
      </div>
      ${cpuTable(market.by_cpu)}
    </section>` : ""}`;

  view.querySelectorAll("[data-metric]").forEach((button) => button.addEventListener("click", () =>
    go("overview", { metric: button.dataset.metric === "price_eur" ? "" : button.dataset.metric })));
}

function niceStep(span) {
  const raw = span / 7;
  const power = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 2.5, 5, 10].map((m) => m * power).find((step) => step >= raw);
}

function priceBoard(items, metric) {
  const groups = new Map();
  for (const item of items) {
    if (item[metric] == null || !item.platform) continue;
    if (!groups.has(item.platform)) groups.set(item.platform, []);
    groups.get(item.platform).push(item);
  }
  const rows = [...groups.entries()]
    .map(([name, list]) => ({ name, list, median: median(list.map((i) => i[metric])) }))
    .sort((a, b) => b.list.length - a.list.length).slice(0, 14)
    .sort((a, b) => a.median - b.median);
  if (!rows.length) return `<p class="empty">No priced listings with a recognised platform yet.</p>`;
  const values = rows.flatMap((row) => row.list.map((i) => i[metric]));
  const step = niceStep(Math.max(...values) - Math.min(...values) || 50);
  const lo = Math.floor(Math.min(...values) / step) * step;
  const hi = Math.ceil(Math.max(...values) / step) * step || lo + step;
  const x = (value) => ((value - lo) / (hi - lo)) * 100;
  const ticks = [];
  for (let v = lo; v <= hi + 0.001; v += step) ticks.push(v);
  const label = metric === "price_eur" ? "Asking price" : "With 32 GB";

  return `
    <div class="board">
      ${rows.map((row) => `
        <div class="board__row">
          <div class="board__name" title="${esc(row.name)}">${esc(row.name)}<small>${row.list.length}</small></div>
          <div class="board__track">
            <span class="board__median" style="left:${x(row.median)}%"></span>
            ${row.list.map((item) => `<button class="dot" type="button" style="left:${x(item[metric])}%"
                data-open="mini_pc/${esc(item.item_id)}"
                data-tip="${esc(`${euro(item[metric])}\n${item.title}\n${item.score ?? "–"}/100 · ${when(item.notified_at)}`)}"
                aria-label="${esc(`${item.title}, ${euro(item[metric])}`)}"></button>`).join("")}
          </div>
          <div class="board__value">${euro(row.median)}</div>
        </div>`).join("")}
      <div class="board__axis" aria-hidden="true">
        <div></div>
        <div class="board__ticks">${ticks.map((t) => `<span style="left:${x(t)}%">${euro(t)}</span>`).join("")}</div>
        <div class="board__value muted">median</div>
      </div>
      <div class="board__legend"><span><i class="legend-dot"></i>${label} of one listing</span><span><i class="legend-median"></i>Median for the platform</span></div>
    </div>`;
}

function shelf(title, subtitle, items, href) {
  if (!items.length) return "";
  return `
    <section class="block">
      <div class="block__head">
        <div><h2>${esc(title)}</h2><p class="subtle">${esc(subtitle)}</p></div>
        <a class="link" href="${href}">See all</a>
      </div>
      <div class="shelf">${items.map((item) => card({ mode: "mini_pc", ...item })).join("")}</div>
    </section>`;
}

function niceCeil(value) {
  const step = niceStep(value);
  return Math.ceil(value / step) * step;
}

function valueMap(items, frontierIds, refs) {
  const cost = (item) => item.total_16_eur ?? item.price_eur;
  const points = items.filter((item) => item.bench_mt && cost(item) != null);
  const skipped = items.length - points.length;
  if (!points.length) return `<p class="empty">No alerted listing has a CPU with benchmark data yet.</p>`;
  const frontier = new Set(frontierIds);
  const costs = points.map(cost);
  const xStep = niceStep(Math.max(...costs) - Math.min(...costs) || 50);
  const xLo = Math.floor(Math.min(...costs) / xStep) * xStep;
  const xHi = Math.ceil(Math.max(...costs) / xStep) * xStep || xLo + xStep;
  const refLines = refs.filter((ref) => ref.mt);
  const yHi = niceCeil(Math.max(...points.map((i) => i.bench_mt), ...refLines.map((r) => r.mt)) * 1.05);
  const yStep = niceStep(yHi);
  const x = (v) => ((v - xLo) / (xHi - xLo)) * 100;
  const y = (v) => (v / yHi) * 100;
  const xTicks = [], yTicks = [];
  for (let v = xLo; v <= xHi + 0.001; v += xStep) xTicks.push(v);
  for (let v = 0; v <= yHi + 0.001; v += yStep) yTicks.push(v);
  // The frontier is a staircase: each better deal costs more and is faster.
  const steps = points.filter((i) => frontier.has(i.item_id)).sort((a, b) => cost(a) - cost(b));
  const stair = steps.flatMap((item, index) => {
    const here = `${x(cost(item))},${100 - y(item.bench_mt)}`;
    return index ? [`${x(cost(item))},${100 - y(steps[index - 1].bench_mt)}`, here] : [here];
  }).join(" ");
  // Draw best deals last so they sit on top of ordinary listings.
  const ordered = [...points].sort((a, b) => frontier.has(a.item_id) - frontier.has(b.item_id));

  return `
    <figure class="vmap" aria-label="Scatter plot of CPU benchmark against 16 GB build cost">
      <div class="vmap__y" aria-hidden="true">${yTicks.map((t) => `<span style="bottom:${y(t)}%">${thousands(t)}</span>`).join("")}</div>
      <div class="vmap__plot">
        ${yTicks.map((t) => `<i class="vmap__grid" style="bottom:${y(t)}%"></i>`).join("")}
        ${refLines.map((ref) => `<div class="vmap__ref" style="bottom:${y(ref.mt)}%"><span>${esc(ref.name)} · ${thousands(ref.mt)}</span></div>`).join("")}
        ${steps.length > 1 ? `<svg class="vmap__stair" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><polyline points="${stair}"/></svg>` : ""}
        ${ordered.map((item) => {
          const deal = frontier.has(item.item_id);
          const ratio = vsFirstReference(item.bench_mt);
          return `<button class="vdot${deal ? " vdot--deal" : ""}" type="button" style="left:${x(cost(item))}%;bottom:${y(item.bench_mt)}%"
            data-open="mini_pc/${esc(item.item_id)}"
            data-tip="${esc(`${euro(cost(item))} with 16 GB${deal ? " · best deal" : ""}\n${item.title}\n${item.cpu || ""}\nMT ${item.bench_mt.toLocaleString("en-GB")}${ratio ? ` · ${ratio}` : ""}`)}"
            aria-label="${esc(`${item.title}: ${euro(cost(item))}, multithread ${item.bench_mt}${deal ? ", best deal" : ""}`)}"></button>`;
        }).join("")}
      </div>
      <div></div>
      <div class="vmap__x" aria-hidden="true">${xTicks.map((t) => `<span style="left:${x(t)}%">${euro(t)}</span>`).join("")}</div>
      <div></div>
      <figcaption class="board__legend">
        <span><i class="legend-vdot"></i>Listing</span>
        <span><i class="legend-vdot legend-vdot--deal"></i>Best deal (cost/performance frontier)</span>
        ${refLines.length ? `<span><i class="legend-ref"></i>Reference CPU</span>` : ""}
        <span class="muted">x: 16 GB build cost · y: PassMark multithread${skipped ? ` · ${plural(skipped, "listing")} without benchmark data not shown` : ""}</span>
      </figcaption>
    </figure>`;
}

function cpuTable(rows) {
  const ranked = rows.filter((row) => row.median_bench_mt)
    .sort((a, b) => (a.eur_per_1k_mt ?? Infinity) - (b.eur_per_1k_mt ?? Infinity)).slice(0, 20);
  if (!ranked.length) return `<p class="empty">No alerted listing has a CPU with benchmark data yet.</p>`;
  const ref = REFS[0];
  return `
    <div class="table-wrap"><table>
      <thead><tr><th>CPU</th><th class="num">Alerts</th><th class="num">Median price</th><th class="num">Multithread</th>
        ${ref ? `<th class="num">vs ${esc(ref.name)}</th>` : ""}<th class="num">€ per 1k MT</th></tr></thead>
      <tbody>${ranked.map((row) => `
        <tr><td><a class="link" href="#/mini_pc?cpu=${encodeURIComponent(row.name)}">${esc(row.name)}</a></td>
          <td class="num">${row.count}</td><td class="num">${euro(row.median_price)}</td>
          <td class="num">${Math.round(row.median_bench_mt).toLocaleString("en-GB")}</td>
          ${ref ? `<td class="num">${ref.mt ? `×${(row.median_bench_mt / ref.mt).toFixed(1)}` : "–"}</td>` : ""}
          <td class="num">${euro(row.eur_per_1k_mt)}</td></tr>`).join("")}
      </tbody>
    </table></div>`;
}

function generationTable(rows) {
  const sorted = [...rows].sort((a, b) => b.name - a.name);
  return `
    <div class="table-wrap"><table>
      <thead><tr><th>Intel generation</th><th class="num">Alerts</th><th class="num">Lowest price</th>
        <th class="num">Median price</th><th class="num">Median with 32 GB</th><th class="num">Median MT</th><th class="num">€ per 1k MT</th><th class="num">Median score</th><th class="num">Best score</th></tr></thead>
      <tbody>${sorted.map((row) => `
        <tr><td><a class="link" href="#/mini_pc?generation=${row.name}">${row.name}th gen</a></td>
          <td class="num">${row.count}</td><td class="num">${euro(row.min_price)}</td>
          <td class="num">${euro(row.median_price)}</td><td class="num">${euro(row.median_total_32)}</td>
          <td class="num">${row.median_bench_mt ? Math.round(row.median_bench_mt).toLocaleString("en-GB") : "–"}</td><td class="num">${euro(row.eur_per_1k_mt)}</td>
          <td class="num">${row.median_score ?? "–"}</td><td class="num">${row.best_score ?? "–"}</td></tr>`).join("")}
      </tbody>
    </table></div>`;
}

/* -- catalogue (Mini PC / RAM / CPU) ---------------------------------------- */
async function renderCatalog(mode, params) {
  const query = { mode, status: "alerts", sort: "newest", ...params, limit: PAGE_SIZE };
  const [facets, list] = await Promise.all([api("facets", { mode }), api("listings", query)]);
  const select = (name, label, options) => `
    <select name="${name}" aria-label="${label}" class="select${params[name] && name !== "sort" ? " is-set" : ""}">
      ${options.map(([value, text]) => `<option value="${esc(value)}"${String(params[name] ?? "") === String(value) ? " selected" : ""}>${esc(text)}</option>`).join("")}
    </select>`;
  const facetOptions = (name, label) => [["", label], ...facets[name].map((f) =>
    [f.value, `${name === "generation" ? `${f.value}th gen` : f.value} (${f.count})`])];
  const filtered = Object.keys(params).some((key) => !["sort", "status"].includes(key) || params[key] !== (key === "sort" ? "newest" : "alerts"));

  view.innerHTML = `
    <form class="filters" id="filters">
      ${select("status", "Listings shown", STATUS_FILTER[mode])}
      ${FACET_FILTERS[mode].map(([name, label]) => select(name, label, facetOptions(name, label))).join("")}
      ${mode === "mini_pc" ? hardwareFilters(select) + select("availability", "Availability", AVAILABILITY_FILTER) : ""}
      <input name="min_price" type="number" min="0" inputmode="decimal" placeholder="From €" value="${esc(params.min_price)}" aria-label="Minimum price">
      <input name="max_price" type="number" min="0" inputmode="decimal" placeholder="To €" value="${esc(params.max_price)}" aria-label="Maximum price">
      ${params.run_id ? `<button class="pill" type="button" aria-pressed="true" data-drop="run_id">One run only ✕</button>` : ""}
      ${params.q ? `<button class="pill" type="button" aria-pressed="true" data-drop="q">“${esc(params.q)}” ✕</button>` : ""}
      ${filtered ? `<button class="clear" type="button" data-clear>Clear filters</button>` : ""}
    </form>
    <div class="catalog__bar">
      <span class="subtle" id="count">${plural(list.total, "listing")}</span>
      <label>Sort ${select("sort", "Sort", SORTS[mode])}</label>
    </div>
    ${list.total ? `<div class="grid" id="grid">${list.items.map(card).join("")}</div>` :
      `<p class="empty">No ${MODES[mode]} listings match these filters.</p>`}
    <div class="more">${list.total > list.items.length ? `<button class="button button--quiet" type="button" id="more">Show more</button>` : ""}</div>`;

  const update = (changes) => go(mode, { ...params, ...changes });
  view.querySelectorAll("select").forEach((el) => el.addEventListener("change", () => update({ [el.name]: el.value })));
  view.querySelectorAll("input[name$=_price]").forEach((el) => el.addEventListener("change", () => update({ [el.name]: el.value })));
  view.querySelectorAll("[data-drop]").forEach((el) => el.addEventListener("click", () => update({ [el.dataset.drop]: "" })));
  view.querySelector("[data-clear]")?.addEventListener("click", () => go(mode));
  document.getElementById("filters").addEventListener("submit", (event) => event.preventDefault());

  let offset = list.items.length;
  document.getElementById("more")?.addEventListener("click", async (event) => {
    const next = await api("listings", { ...query, offset });
    document.getElementById("grid").insertAdjacentHTML("beforeend", next.items.map(card).join(""));
    offset += next.items.length;
    if (offset >= next.total) event.target.remove();
  });
}

function hardwareFilters(select) {
  const filters = HARDWARE_FILTERS.map(([name, label, options]) => select(name, label, [["", label], ...options]));
  const ref = REFS[0];
  if (ref?.mt) {
    const options = PERF_STEPS.map((step) => [String(Math.round(ref.mt * step)), `≥ ${step}× ${ref.name}`]);
    filters.push(select("min_mt", "CPU performance", [["", "CPU performance"], ...options]));
  }
  return filters.join("");
}

/* -- runs ------------------------------------------------------------------- */
async function renderRuns(params) {
  const limit = Number(params.limit) || 96;
  const data = await api("runs", { limit });
  const runs = data.items;
  const counts = runs.reduce((acc, run) => ({ ...acc, [run.status]: (acc[run.status] || 0) + 1 }), {});
  const modeCell = (run, mode) => {
    const stats = run.modes[mode];
    if (!stats) return `<div class="run__mode muted"><span class="run__label">${MODES[mode]}: </span>not run</div>`;
    const alerts = stats.notified
      ? `<a href="#/${mode}?run_id=${run.id}&status=all">${plural(stats.notified, "alert")}</a>`
      : `<span class="muted">no alerts</span>`;
    const failed = stats.failed_searches ? `, ${stats.failed_searches} failed` : "";
    return `<div class="run__mode"><span class="run__label">${MODES[mode]}: </span>${alerts}<br>
      <span class="subtle">${stats.new} new of ${stats.listings}, ${plural(stats.searches, "search", "searches")}${failed}</span></div>`;
  };

  view.innerHTML = `
    <section>
      <div class="block__head">
        <div>
          <h2>Scanner runs</h2>
          <p class="subtle">${data.total} recorded. Of the latest ${runs.length}: ${counts.ok || 0} completed,
            ${counts.problems || 0} with problems, ${counts.failed || 0} failed. Oldest on the left.</p>
        </div>
      </div>
      <div class="health" role="list">
        ${[...runs].reverse().map((run) => `<button class="health__cell health__cell--${esc(run.status)}" type="button" role="listitem"
            data-run="${run.id}" data-tip="${esc(`${RUN_STATE[run.status]?.[1] || run.status}\n${when(run.started_at)}, ${duration(run.duration_s)}\n${plural(run.alerts, "alert")}`)}"
            aria-label="${esc(`${when(run.started_at)}: ${RUN_STATE[run.status]?.[1] || run.status}`)}"></button>`).join("")}
      </div>
      <div class="health__legend">
        <span><i class="health__cell"></i>Completed</span>
        <span><i class="health__cell health__cell--problems"></i>With problems</span>
        <span><i class="health__cell health__cell--failed"></i>Failed</span>
      </div>
    </section>
    <section class="block">
      ${runs.length ? `<div class="run-list">
        <div class="run run__head"><span>Started</span><span>Result</span><span>Took</span>
          ${Object.values(MODES).map((label) => `<span>${label}</span>`).join("")}</div>
        ${runs.map((run) => `
          <div class="run run--${esc(run.status)}" id="run-${run.id}">
            <div>${esc(when(run.started_at))}${run.dry_run ? ` <span class="muted">dry run</span>` : ""}
              ${run.source === "telegram" ? `<br><span class="muted">from Telegram</span>` : ""}</div>
            <div>${runState(run.status)}</div>
            <div class="subtle">${duration(run.duration_s)}</div>
            ${Object.keys(MODES).map((mode) => modeCell(run, mode)).join("")}
            ${run.issues.length ? `<details class="run__issues"><summary>${plural(run.issues.length, "problem")}</summary>
              <ul>${run.issues.map((issue) => `<li><strong>${esc(ISSUE_TITLE[issue.kind] || issue.kind)}:</strong> ${esc(issue.message)}</li>`).join("")}</ul>
            </details>` : ""}
          </div>`).join("")}
      </div>` : `<p class="empty">No runs yet. Each scanner run is recorded here.</p>`}
      ${data.total > runs.length ? `<div class="more"><a class="button button--quiet" href="#/runs?limit=${limit * 2}">Show older runs</a></div>` : ""}
    </section>`;

  view.querySelectorAll("[data-run]").forEach((cell) => cell.addEventListener("click", () => {
    const row = document.getElementById(`run-${cell.dataset.run}`);
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    row.querySelector("details")?.setAttribute("open", "");
  }));
}

/* -- listing drawer --------------------------------------------------------- */
let lastFocus = null;

async function openListing(key) {
  const [mode, id] = key.split("/");
  lastFocus = document.activeElement;
  drawerBody.innerHTML = `<p class="subtle" style="margin-top:56px">Loading…</p>`;
  drawer.hidden = false;
  document.body.style.overflow = "hidden";
  drawer.querySelector(".drawer__close").focus();
  try {
    drawerBody.innerHTML = detail(await api(`listings/${mode}/${encodeURIComponent(id)}`));
  } catch (error) {
    drawerBody.innerHTML = `<p class="error" style="margin-top:56px">${esc(error.message)}</p>`;
  }
}

function closeListing() {
  drawer.hidden = true;
  document.body.style.overflow = "";
  lastFocus?.focus();
}

function detail(item) {
  const cores = item.cores ? ` (${item.cores} cores, ${item.threads} threads)` : "";
  const facts = [
    ["Platform", item.platform || item.model],
    ["CPU", item.cpu && `${item.cpu}${cores}`],
    ["Generation", item.generation && `${item.generation}th gen`],
    ["Benchmark", item.mode === "mini_pc" && item.cpu && (item.bench_mt
      ? `PassMark multithread ${item.bench_mt.toLocaleString("en-GB")}, single thread ${item.bench_st?.toLocaleString("en-GB") ?? "N/A"}`
      : "N/A (no benchmark data for this CPU)")],
    ["Compared with", (item.vs_reference || []).map((ref) => ref.mt_ratio == null
      ? `${ref.name}: incomplete` : `×${ref.mt_ratio.toFixed(2)} ${ref.name}`).join(", ")],
    ["RAM", item.ram_gb && `${item.ram_gb} GB`],
    ["Disk", item.storage],
    ["GPU", item.gpu],
    ["Score", item.score != null && `${item.score}/100${item.upgrade_score != null ? `, ${item.upgrade_score}/100 after upgrade` : ""}`
      + (item.mode === "mini_pc" && !item.score_current ? " (older scoring rules, not recalculated)" : "")],
    ["With 16 GB", item.total_16_eur != null && euro(item.total_16_eur)],
    ["With 32 GB", item.total_32_eur != null && euro(item.total_32_eur)],
    ["16 GB and CPU upgrade", item.total_16_cpu_eur != null && euro(item.total_16_cpu_eur)],
    ["32 GB and CPU upgrade", item.total_32_cpu_eur != null && euro(item.total_32_cpu_eur)],
    ["Build estimate", item.build_estimate_eur != null && euro(item.build_estimate_eur)],
    ["Availability", availabilityText(item)],
    ["Found by search", item.matched_query],
    ["Status", STATUS_LABEL[item.status] || item.status],
    ["Alert sent", item.notified_at && when(item.notified_at)],
    ["First seen", when(item.first_seen)],
    ["Recorded from", { scanner: "Scanner", telegram: "Telegram history", legacy: "Old text ledger" }[item.source]],
  ].filter(([, value]) => value);
  const verdict = item.verdict || item.tier;

  return `
    ${safeUrl(item.image_url) ? `<img class="detail__photo" src="${safeUrl(item.image_url)}" alt="" referrerpolicy="no-referrer">` : `<div style="height:40px"></div>`}
    <div class="detail__price">${euro(item.price_eur)}</div>
    <h2 class="detail__title" id="drawer-title">${esc(item.title || `Listing ${item.item_id}`)}</h2>
    ${verdict ? `<div class="detail__verdict"><i class="mark mark--${markClass(item)}"></i>${esc(verdict)}</div>` : ""}
    <div class="detail__actions">
      ${safeUrl(item.url) ? `<a class="button" href="${safeUrl(item.url)}" target="_blank" rel="noopener noreferrer">Open on Vinted</a>` : ""}
      ${item.run_id ? `<a class="button button--quiet" href="#/runs" data-close>See runs</a>` : ""}
    </div>
    <table class="facts"><tbody>${facts.map(([label, value]) => `<tr><th>${label}</th><td>${esc(value)}</td></tr>`).join("")}</tbody></table>
    ${(item.sections || []).map(section).join("")}`;
}

function days(value) {
  return value < 1 ? `${Math.round(value * 24)} h` : `${value.toFixed(1)} days`;
}

function availabilityText(item) {
  if (item.mode !== "mini_pc") return null;
  if (!item.availability) return item.availability_checked_at
    ? `Page not readable (checked ${when(item.availability_checked_at)})` : "Not checked yet";
  let text = AVAILABILITY_LABEL[item.availability] || item.availability;
  if (item.availability_label && item.availability_label !== text) text += ` (“${item.availability_label}”)`;
  const t = item.time_to_close;
  if (t) {
    text += t.days != null
      ? ` between ${days(t.min_days)} and ${days(t.max_days)} after it was first seen`
      : ` within ${days(t.max_days)} (already gone at the first check)`;
  }
  if (item.availability === "closed") text += " · reason not shown by Vinted";
  return `${text} · checked ${when(item.availability_checked_at)}`;
}

function sellTable(rows) {
  const tracked = rows.filter((row) => row.checked)
    .sort((a, b) => (b.gone - a.gone) || (b.checked - a.checked)).slice(0, 20);
  if (!tracked.length) return `<p class="empty">No alerted listing has been re-checked yet.</p>`;
  return `
    <div class="table-wrap"><table>
      <thead><tr><th>Platform</th><th class="num">Checked</th><th class="num">Still for sale</th><th class="num">Sold</th>
        <th class="num">Gone</th><th class="num">Median days until gone</th></tr></thead>
      <tbody>${tracked.map((row) => `
        <tr><td><a class="link" href="#/mini_pc?platform=${encodeURIComponent(row.name)}&availability=gone">${esc(row.name)}</a></td>
          <td class="num">${row.checked}</td><td class="num">${row.for_sale}</td><td class="num">${row.sold}</td>
          <td class="num">${row.gone}</td>
          <td class="num">${row.median_days_to_close != null ? `${row.median_days_to_close.toFixed(1)} <span class="subtle">(${row.timed})</span>` : "–"}</td></tr>`).join("")}
      </tbody>
    </table></div>`;
}

function section({ title, rows }) {
  const isDescription = /Seller description$/.test(title);
  const lines = rows.map((row) => row.startsWith("   ")
    ? `<p class="indent">${esc(row.trim())}</p>` : `<p>${esc(row)}</p>`).join("");
  return `<section class="section${isDescription ? " section--description" : ""}"><h3>${esc(title)}</h3>${lines}</section>`;
}

/* -- tooltip ---------------------------------------------------------------- */
function showTooltip(target) {
  const [first, ...rest] = target.dataset.tip.split("\n");
  tooltip.innerHTML = `<strong>${esc(first)}</strong>${rest.map((line) => `<div>${esc(line)}</div>`).join("")}`;
  tooltip.hidden = false;
  const box = target.getBoundingClientRect();
  const tip = tooltip.getBoundingClientRect();
  const left = Math.min(Math.max(8, box.left + box.width / 2 - tip.width / 2), innerWidth - tip.width - 8);
  const top = box.top - tip.height - 10 < 8 ? box.bottom + 10 : box.top - tip.height - 10;
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function hideTooltip() { tooltip.hidden = true; }

/* -- events ----------------------------------------------------------------- */
document.addEventListener("click", (event) => {
  const opener = event.target.closest("[data-open]");
  if (opener) return openListing(opener.dataset.open);
  if (event.target.closest("[data-close]")) closeListing();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !drawer.hidden) closeListing();
});
for (const type of ["mouseover", "focusin"]) {
  document.addEventListener(type, (event) => {
    const target = event.target.closest?.("[data-tip]");
    if (target) showTooltip(target);
  });
}
for (const type of ["mouseout", "focusout"]) {
  document.addEventListener(type, (event) => { if (event.target.closest?.("[data-tip]")) hideTooltip(); });
}
// Vinted photo links expire; show the placeholder instead of a broken image.
document.addEventListener("error", (event) => {
  if (event.target.tagName !== "IMG") return;
  event.target.parentElement?.classList.add("no-photo");
  event.target.remove();
}, true);
document.getElementById("search").addEventListener("submit", (event) => {
  event.preventDefault();
  const { page, params } = route();
  const mode = page in MODES ? page : "mini_pc";
  go(mode, { ...(page === mode ? params : {}), q: searchInput.value.trim() });
});
addEventListener("hashchange", render);
render();
