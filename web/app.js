const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const money = (value) => {
  const n = Number(value);
  return Number.isFinite(n)
    ? new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD" }).format(n)
    : "—";
};
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));
const safeHref = (value) => (/^https:\/\//i.test(String(value || "")) ? String(value) : null);

const POSTAL = /^[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z](?:\d[ABCEGHJ-NPRSTV-Z]\d)?$/;
const state = {
  postal: localStorage.getItem("dr_postal") || "",
  query: "",
  data: null,
  filter: "all",
  openGroup: null,
  watch: JSON.parse(localStorage.getItem("dr_watch") || "[]"),
  device: localStorage.getItem("dr_device") || crypto.randomUUID(),
};
localStorage.setItem("dr_device", state.device);

const els = {
  form: $("#searchForm"), query: $("#query"), postal: $("#postal"),
  btn: $("#searchBtn"), error: $("#formError"), empty: $("#emptyState"),
  grid: $("#cardGrid"), skeleton: $("#skeleton"), strip: $("#statusStrip"),
  sources: $("#sourceStatus"), note: $("#statusNote"), compare: $("#comparePanel"),
  table: $("#compareTable tbody"), compareTitle: $("#compareTitle"),
  compareNote: $("#compareNote"), historyPanel: $("#historyPanel"),
  chart: $("#historyChart"), stats: $("#historyStats"), historyRange: $("#historyRange"),
  watchList: $("#watchList"), locLabel: $("#locLabel"), locBtn: $("#locBtn"),
  emptyTitle: $("#emptyState h3"), emptyText: $("#emptyState p"),
  locDialog: $("#locDialog"), locForm: $("#locForm"), locPostal: $("#locPostal"),
  locError: $("#locError"), resultsEyebrow: $("#resultsEyebrow"),
};

function setPostal(value) {
  state.postal = value;
  localStorage.setItem("dr_postal", value);
  els.locLabel.textContent = value ? value.replace(/(.{3})/, "$1 ") : "Set postal code";
  els.postal.value = value ? value.replace(/(.{3})/, "$1 ") : "";
}
setPostal(state.postal);

function showError(message) {
  els.error.textContent = message;
  els.error.hidden = !message;
}

function checkedAgo(iso) {
  if (!iso) return "never checked";
  const seconds = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (!Number.isFinite(seconds)) return "never checked";
  if (seconds < 90) return "checked just now";
  if (seconds < 3600) return `checked ${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `checked ${Math.round(seconds / 3600)} h ago`;
  return `checked ${Math.round(seconds / 86400)} d ago`;
}

function unitText(offer) {
  if (!offer.unit_price) return "";
  return `${money(offer.unit_price)} / ${offer.unit_basis || "unit"}`;
}

function offerCard(offer, groupKey) {
  const watched = state.watch.some((w) => w.item_id === offer.item_id);
  const discount = Number(offer.discount_percent) >= 1
    ? `${Number(offer.discount_percent).toFixed(0)}% off` : "";
  return `
    <article class="card" data-group="${esc(groupKey)}" data-source="${esc(offer.source)}" data-discount="${Number(offer.discount_percent || 0)}">
      <div class="card__top">
        <span class="store">${esc(offer.store)}</span>
        <span class="badge ${discount ? "drop" : ""}" ${discount ? 'title="Discount compares the retailer-reported original price; not checkout-verified."' : ""}>${discount || (offer.source === "flipp-online" ? "online" : "flyer")}</span>
      </div>
      <h3>${esc(offer.product_name)}</h3>
      <div class="priceRow">
        <span class="price">${money(offer.sale_price)}</span>
        ${offer.regular_price ? `<span class="was">${money(offer.regular_price)}</span>` : ""}
        ${offer.unit_price ? `<span class="unit">${esc(unitText(offer))}</span>` : ""}
      </div>
      <div class="meta">
        <span>${esc(checkedAgo(offer.observed_at))}</span>
        <span>${offer.price_basis === "package" ? "package price" : `priced per ${esc(offer.price_basis)}`}</span>
        <span>${offer.source === "flipp-online" ? "online listing" : "flyer"}</span>
      </div>
      ${offer.conditions?.length ? `<details class="cond"><summary>Price notes</summary><ul>${offer.conditions.map((c) => `<li>${esc(c)}</li>`).join("")}</ul></details>` : ""}
      <div class="card__actions">
        <button class="btn" type="button" data-compare="${esc(groupKey)}">Compare</button>
        <button class="btn watchBtn ${watched ? "is-on" : ""}" type="button"
          aria-pressed="${watched}" aria-label="${watched ? "Stop watching" : "Watch"} ${esc(offer.product_name)}"
          data-watch="${esc(offer.item_id)}" data-name="${esc(offer.product_name)}">${watched ? "★" : "☆"}</button>
      </div>
    </article>`;
}

function flatten(data) {
  const rows = [];
  for (const group of data.groups || []) {
    const offers = group.offers || [];
    const best = offers.reduce((acc, o) => {
      const price = Number(o.unit_price ?? o.sale_price);
      return !acc || price < Number(acc.unit_price ?? acc.sale_price) ? o : acc;
    }, null);
    offers.forEach((offer) => rows.push({ offer, groupKey: group.key, best: offer === best, count: offers.length }));
  }
  return rows;
}

function render() {
  const rows = flatten(state.data);
  const visible = rows.filter(({ offer }) => {
    if (state.filter === "flyer") return offer.source === "flipp";
    if (state.filter === "online") return offer.source === "flipp-online";
    if (state.filter === "discount") return Number(offer.discount_percent || 0) >= 15;
    return true;
  });
  els.empty.hidden = visible.length > 0;
  els.grid.hidden = visible.length === 0;
  if (!rows.length) {
    els.emptyTitle.textContent = `No prices found for “${state.query}”`;
    els.emptyText.textContent = "No supported source returned a usable price for this product and postal code. Try a broader product name or a nearby postal code.";
  } else if (!visible.length) {
    els.emptyTitle.textContent = "No offers match this filter";
    els.emptyText.textContent = "Clear the filter to see the offers returned for this search.";
  } else {
    els.emptyTitle.textContent = "Start with a product and a postal code";
    els.emptyText.textContent = "DealRadar reads current Canadian flyers and online listings. It will not invent a regular price, a discount, or a checkout total.";
  }
  els.grid.innerHTML = visible
    .sort((a, b) => Number(b.offer.discount_percent || 0) - Number(a.offer.discount_percent || 0))
    .map(({ offer, groupKey }) => offerCard(offer, groupKey)).join("");
  const count = visible.length;
  els.resultsEyebrow.textContent = count ? `${count} price${count === 1 ? "" : "s"} · ${state.data.postal_code}` : "Live radar";
  $("#resultsTitle").textContent = state.query ? `Results for “${state.query}”` : "Source-reported deals";
}

function renderSources(sources, warnings = []) {
  els.strip.hidden = !sources?.length && !warnings.length;
  if (!sources?.length && !warnings.length) return;
  els.sources.innerHTML = sources.map((s) => `
    <span class="srcPill ${esc(s.status)}"><span class="dot" aria-hidden="true"></span>
      <b>${esc(s.source)}</b> ${esc(s.status)}${s.item_count ? ` · ${s.item_count}` : ""}
    </span>`).join("");
  const problems = (sources || []).filter((s) => s.status !== "ok" && s.status !== "disabled")
    .map((s) => `${s.source}: ${s.message || s.error_code || s.status}`);
  els.note.textContent = [...problems, ...(warnings || [])].join(" · ");
}

async function search(event) {
  event?.preventDefault();
  showError("");
  const postal = els.postal.value.toUpperCase().replace(/\s/g, "");
  const query = els.query.value.trim();
  if (!POSTAL.test(postal)) return showError("Enter a full Canadian postal code, such as N9A 6K9.");
  if (postal.length === 3) return showError("A full postal code is required. Flyer coverage differs inside one FSA, so an FSA cannot be priced.");
  if (!query) return showError("Enter a product to compare.");
  setPostal(postal);
  state.query = query;
  els.btn.disabled = true;
  els.skeleton.hidden = false;
  els.grid.hidden = true;
  els.empty.hidden = true;
  els.grid.setAttribute("aria-busy", "true");
  // Never leave a previous search's comparison or history beside a new result.
  els.compare.hidden = true;
  els.historyPanel.hidden = true;
  els.resultsEyebrow.textContent = "Scanning sources…";
  $("#resultsTitle").textContent = `Results for “${query}”`;
  try {
    const response = await fetch("/v1/search", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, postal_code: postal }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = body.detail;
      const message = typeof detail === "string" ? detail : detail?.message
        || detail?.sources?.map((s) => `${s.source}: ${s.message || s.error_code}`).join(" · ")
        || "Prices could not be read right now. Try again shortly.";
      throw new Error(message);
    }
    state.data = body;
    renderSources(body.sources, body.warnings);
    render();
  } catch (error) {
    state.data = null;
    showError(error.message);
    els.strip.hidden = true;
    els.sources.innerHTML = "";
    els.note.textContent = "";
    els.resultsEyebrow.textContent = "Live radar";
    $("#resultsTitle").textContent = "Source-reported deals";
    els.emptyTitle.textContent = "Prices could not be loaded";
    els.emptyText.textContent = "No price is shown rather than a stale or invented one. Check the message above and try again.";
    els.empty.hidden = false;
  } finally {
    els.btn.disabled = false;
    els.skeleton.hidden = true;
    els.grid.setAttribute("aria-busy", "false");
  }
}

function openCompare(groupKey) {
  const group = state.data?.groups.find((g) => g.key === groupKey);
  if (!group) return;
  state.openGroup = groupKey;
  els.compare.hidden = false;
  els.compareTitle.textContent = group.offers[0]?.product_name || "Compare prices";
  const best = group.offers.reduce((acc, o) => {
    const price = Number(o.unit_price ?? o.sale_price);
    return !acc || price < Number(acc.unit_price ?? acc.sale_price) ? o : acc;
  }, null);
  els.table.innerHTML = group.offers.map((offer) => `
    <tr class="${offer === best ? "best" : ""}">
      <td>${esc(offer.store)}${offer === best ? " · best" : ""}</td>
      <td>${money(offer.sale_price)}${offer.regular_price ? ` <span class="was">${money(offer.regular_price)}</span>` : ""}</td>
      <td>${offer.unit_price ? esc(unitText(offer)) : "—"}</td>
      <td>${esc(checkedAgo(offer.observed_at))}</td>
      <td>${safeHref(offer.source_url) ? `<a href="${esc(safeHref(offer.source_url))}" target="_blank" rel="noopener noreferrer">Source</a>` : "No public link"}</td>
    </tr>`).join("");
  els.compareNote.textContent = group.offers.length > 1
    ? "Same product identity (brand, name, and declared size). Unit price decides the best value, not the biggest percentage off."
    : "Only one retailer currently has this product in the selected area.";
  els.historyPanel.hidden = false;
  els.chart.innerHTML = "<p class='fine'>Loading observed history…</p>";
  els.stats.innerHTML = "";
  state.compareTrigger = document.activeElement;
  const close = $("#closeCompare");
  els.compare.scrollIntoView({ behavior: "smooth", block: "start" });
  close.focus({ preventScroll: true });
  loadHistory(group.offers[0]);
}

async function loadHistory(offer) {
  try {
    const url = `/v1/items/${encodeURIComponent(offer.item_id)}/history?postal_code=${encodeURIComponent(state.postal)}&days=90`;
    const response = await fetch(url);
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "History unavailable");
    drawChart(body.observations || [], body.summary);
    els.historyRange.textContent = `last ${body.days} days`;
  } catch (error) {
    els.historyRange.textContent = "";
    els.chart.innerHTML = `<p class="fine">${esc(error.message)}</p>`;
  }
}

function drawChart(points, summary) {
  const usable = points.filter((p) => Number.isFinite(Number(p.sale_price)));
  if (!usable.length) {
    els.chart.innerHTML = "<p class='fine'>No earlier observations for this product yet. The chart fills in as prices are checked.</p>";
    els.stats.innerHTML = "";
    return;
  }
  const prices = usable.map((p) => Number(p.sale_price));
  const min = Math.min(...prices), max = Math.max(...prices);
  const span = max - min || 1;
  const w = 640, h = 180, pad = 18;
  const step = usable.length > 1 ? (w - pad * 2) / (usable.length - 1) : 0;
  const xy = prices.map((price, i) => [pad + i * step, h - pad - ((price - min) / span) * (h - pad * 2)]);
  const line = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${line} L${xy.at(-1)[0].toFixed(1)},${h - pad} L${xy[0][0].toFixed(1)},${h - pad} Z`;
  els.chart.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Observed sale prices, ${money(min)} to ${money(max)}">
      <path class="area" d="${area}"></path>
      <path class="line" d="${line}"></path>
      ${xy.map(([x, y]) => `<circle class="pt" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3"></circle>`).join("")}
      <text x="${pad}" y="${h - 3}">${esc(new Date(usable[0].observed_at).toLocaleDateString("en-CA"))}</text>
      <text x="${w - pad}" y="${h - 3}" text-anchor="end">${esc(new Date(usable.at(-1).observed_at).toLocaleDateString("en-CA"))}</text>
    </svg>`;
  const change = Number(summary?.change_percent);
  els.stats.innerHTML = `
    <div><dt>Lowest seen</dt><dd>${money(summary?.lowest)}</dd></div>
    <div><dt>Typical</dt><dd>${money(summary?.median)}</dd></div>
    <div><dt>Latest</dt><dd>${money(summary?.latest)}</dd></div>
    <div><dt>Since last check</dt><dd>${Number.isFinite(change) ? `${change > 0 ? "+" : ""}${change.toFixed(1)}%` : "—"}</dd></div>`;
}

async function toggleWatch(itemId, name) {
  const existing = state.watch.find((w) => w.item_id === itemId);
  const before = state.watch.slice();
  if (existing) {
    state.watch = state.watch.filter((w) => w.item_id !== itemId);
  } else {
    state.watch.push({ item_id: itemId, name });
  }
  try {
    const response = existing
      ? await fetch(`/v1/watchlist/${encodeURIComponent(itemId)}?device_id=${encodeURIComponent(state.device)}`, { method: "DELETE" })
      : await fetch("/v1/watchlist", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_id: state.device, item_id: itemId }),
      });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(typeof body.detail === "string" ? body.detail : "The watchlist could not be updated.");
    }
  } catch (error) {
    state.watch = before;
    showError(error.message);
  }
  localStorage.setItem("dr_watch", JSON.stringify(state.watch));
  render();
  renderWatch();
}

async function renderWatch() {
  if (!state.watch.length) {
    els.watchList.innerHTML = `<p class="empty">Nothing on the radar yet. Tap ☆ on any product to track its price.</p>`;
    return;
  }
  if (!state.postal) {
    els.watchList.innerHTML = `<p class="empty">Set a postal code to see current prices for ${state.watch.length} watched item(s).</p>`;
    return;
  }
  try {
    const response = await fetch(`/v1/watchlist?device_id=${encodeURIComponent(state.device)}&postal_code=${encodeURIComponent(state.postal)}`);
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Watchlist unavailable");
    const items = body.items || [];
    els.watchList.innerHTML = items.length ? items.map((row) => {
      const offer = row.best_offer;
      const name = offer?.product_name || state.watch.find((w) => w.item_id === row.item_id)?.name || "Watched product";
      const change = Number(row.summary?.change_percent);
      return `<div class="watchRow">
        <div><strong>${esc(name)}</strong>
          <small>${offer ? `${esc(offer.store)} · ${money(offer.sale_price)} · ${esc(checkedAgo(offer.observed_at))}` : "No current offer in this area"}</small></div>
        <div style="text-align:right">
          ${row.target_hit ? '<span class="hit">target hit</span>' : ""}
          <small>${Number.isFinite(change) ? `${change > 0 ? "+" : ""}${change.toFixed(1)}% vs previous check` : "no prior check recorded"}</small>
          <button class="chip" type="button" data-watch="${esc(row.item_id)}" data-name="${esc(name)}">Remove</button>
        </div>
      </div>`;
    }).join("") : `<p class="empty">Watching ${state.watch.length} item(s). They will appear once a price is seen in ${esc(state.postal)}.</p>`;
  } catch (error) {
    els.watchList.innerHTML = `<p class="empty">${esc(error.message)}</p>`;
  }
}

els.form.addEventListener("submit", search);
els.grid.addEventListener("click", (event) => {
  const compare = event.target.closest("[data-compare]");
  if (compare) return openCompare(compare.dataset.compare);
  const watch = event.target.closest("[data-watch]");
  if (watch) return toggleWatch(watch.dataset.watch, watch.dataset.name);
});
els.watchList.addEventListener("click", (event) => {
  const watch = event.target.closest("[data-watch]");
  if (watch) toggleWatch(watch.dataset.watch, watch.dataset.name);
});
$("#closeCompare").addEventListener("click", () => {
  els.compare.hidden = true;
  state.compareTrigger?.focus?.();
});
$$(".chip[data-filter]").forEach((chip) => chip.addEventListener("click", () => {
  $$(".chip[data-filter]").forEach((c) => c.classList.toggle("is-on", c === chip));
  state.filter = chip.dataset.filter;
  render();
}));
$("#refreshWatch").addEventListener("click", renderWatch);
els.locBtn.addEventListener("click", () => {
  els.locPostal.value = state.postal.replace(/(.{3})/, "$1 ");
  els.locError.hidden = true;
  els.locDialog.showModal();
});
els.locForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const value = els.locPostal.value.toUpperCase().replace(/\s/g, "");
  if (!POSTAL.test(value) || value.length === 3) {
    els.locError.textContent = "Enter a full Canadian postal code, such as N9A 6K9.";
    els.locError.hidden = false;
    return;
  }
  setPostal(value);
  els.locDialog.close();
  renderWatch();
});
$("#locCancel").addEventListener("click", () => els.locDialog.close());

if (state.postal) els.postal.value = state.postal.replace(/(.{3})/, "$1 ");
renderWatch();
