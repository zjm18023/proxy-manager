"use strict";

const $ = (selector, parent = document) => parent.querySelector(selector);
const $$ = (selector, parent = document) => [...parent.querySelectorAll(selector)];
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const views = {
  subscriptions: ["SUBSCRIPTIONS", "订阅管理", "把多个订阅汇集在一起，保持节点持续更新。"],
  nodes: ["PROXIES", "全部节点", "查看节点状态，为你的应用选择合适的出口。"],
  filters: ["FILTERS", "地区与筛选", "排除不需要的地区，让每次订阅更新都遵循你的选择。"],
  settings: ["CONNECTION", "连接设置", "设置统一的代理入口，供本机或其他应用连接。"],
  activity: ["ACTIVITY", "活动记录", "查看本次运行中的订阅更新、节点切换与服务状态。"],
};
let state = null, activeView = "", loading = false, page = 1, search = "", statusFilter = "all", sourceFilter = "all";
let dirty = false, toastTimer, pollTimer;
let lastExit = null;
let selectionPending = false;
let drawerDirty = false, drawerFocus = null;
const icon = (name) => `<span class="icon icon-${name}" aria-hidden="true"></span>`;
const metricValue = (element, value) => {
  if (window.RouteVisuals) window.RouteVisuals.value(element, value);
  else element.textContent = value;
};

async function api(path, method = "GET", body) {
  const response = await fetch(`/api${path}`, {
    method, credentials: "same-origin", headers: body === undefined ? {} : {"Content-Type":"application/json"},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data;
  try { data = await response.json(); } catch { throw new Error("服务暂时无法响应，请稍后重试"); }
  if (!response.ok) {
    if (response.status === 401 && path !== "/login") { showLogin(true); }
    const detail = data.detail;
    throw new Error(Array.isArray(detail) ? detail.map((v) => v.msg.replace(/^Value error, /, "")).join("；") : detail || "操作失败，请重试");
  }
  return data;
}

function toast(message, failed = false) {
  const element = $("#toast");
  clearTimeout(toastTimer);
  element.textContent = message;
  element.classList.toggle("failed", failed);
  element.hidden = false;
  toastTimer = setTimeout(() => { element.hidden = true; }, failed ? 7000 : 3500);
}

async function action(button, work) {
  if (button?.disabled) return;
  const contents = button?.innerHTML;
  if (button) { button.disabled = true; button.setAttribute("aria-busy", "true"); button.textContent = "处理中…"; }
  try { await work(); } catch (error) { toast(error.message || "无法连接服务，请检查网络", true); }
  finally {
    if (button?.isConnected) {
      button.disabled = button.dataset.action === "test-all" ? Boolean(state?.job.running) : button.dataset.action === "refresh-all" ? Boolean(state?.syncing) : false;
      button.removeAttribute("aria-busy"); button.innerHTML = contents;
    }
  }
}

function timeText(value, short = false) {
  if (!value) return "尚未更新";
  const date = new Date(value);
  return date.toLocaleString("zh-CN", short ? {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"} : {hour12:false});
}

function sourceHost(value) { try { return new URL(value).host; } catch { return "订阅链接"; } }

function intervalText(seconds) {
  if (seconds % 3600 === 0) return `${seconds / 3600} 小时（${seconds} 秒）`;
  if (seconds % 60 === 0) return `${seconds / 60} 分钟（${seconds} 秒）`;
  return `${seconds} 秒`;
}

function openWorkspaceDrawer(panel, trigger) {
  if (!state) return;
  const drawer = $("#workspace-drawer");
  const names = {connection:["NETWORK", "连接详情"], routing:["ROUTING", "自动选路策略"], policy:["POLICY", "选择规则"]};
  if (!names[panel]) return;
  if (drawer.open && drawerDirty && panel !== "routing") { requestDrawerClose(); return; }
  if (!drawer.open) { drawerFocus = trigger || document.activeElement; drawerDirty = false; }
  $$('[data-drawer-panel]').forEach((section) => { section.hidden = section.dataset.drawerPanel !== panel; });
  $("#drawer-kicker").textContent = names[panel][0];
  $("#drawer-title").textContent = names[panel][1];
  $("#drawer-unsaved").hidden = true;
  if (panel === "routing" && (!drawer.open || !$("#routing-form"))) renderRoutingEditor();
  if (panel === "policy" && !$("#selection-mode")) $("#policy-content").innerHTML = selectionPolicyMarkup();
  if (!drawer.open) drawer.showModal();
  document.body.classList.add("drawer-open");
  renderSelectionStatus();
  window.RouteVisuals?.update(state);
}

function requestDrawerClose() {
  if (drawerDirty) {
    $("#drawer-unsaved").hidden = false;
    $("#drawer-keep").focus();
    return;
  }
  $("#workspace-drawer").close();
}

function selectionPolicyMarkup() {
  return `<section class="selection-policy" aria-label="节点选择策略"><div class="selection-policy-heading"><div><span class="eyebrow">ROUTING POLICY</span><h2><span id="selection-mode" class="mode-badge"></span><span>节点选择策略</span></h2></div><a href="#settings" class="policy-settings">调整检测设置 ${icon("settings")}</a></div><p id="selection-rule"></p><p id="selection-tier-description" class="tier-description" hidden></p><dl class="policy-facts"><div><dt>自动检测间隔</dt><dd id="selection-interval"></dd></div><div><dt>故障处理</dt><dd id="selection-failover"></dd></div><div><dt>当前节点最近检测</dt><dd id="selection-last-check"></dd></div></dl><div class="policy-target"><span>检测地址</span><code id="selection-test-url"></code><span>HTTP 200 / 204 · 超时 5 秒</span></div><details class="policy-details"><summary>查看自动选择规则与切换说明</summary><ol><li id="selection-source-rule">只使用已启用订阅中，未被地区、关键词或手动停用规则排除的候选节点。</li><li>按检测地址的连通性和延迟择优，设置 50ms 切换容差，减少延迟小幅波动造成的切换。</li><li>周期检测发现当前节点不可达后重新选路；连接被拒绝或短时间多次建连失败也可能触发提前复检。不会保证每次失败请求都能立即换节点重试。</li><li>手动指定节点时保持固定；没有可用节点时请求可能失败，不会改为直连。出口变更作用于新连接，已有长连接不会自动迁移。</li></ol><p>检测通过表示检测地址可达，不保证生图等业务可用；业务返回 403 / 429 等状态不等于节点连接失败。页面状态每 5 秒刷新，自动检测间隔以此处设置为准。</p></details></section>`;
}

function renderSelectionStatus() {
  const auto = state.selected === "AUTO";
  const ready = state.core.running;
  const current = state.nodes.find((node) => node.id === state.current_node);
  const modeLabel = auto ? (ready ? "自动选择已开启" : "自动选择待就绪") : "手动指定节点";
  const priority = state.settings.selection_strategy === "primary";
  const routing = state.routing || {};
  const tier = routing.active_tier;
  const recovering = routing.reason === "primary_recovering";
  const sourceLabel = !auto ? "手动指定节点" : !priority ? "全部订阅择优" : !ready ? "主备线路待就绪" :
    routing.error ? "线路状态待确认" : tier === "primary" ? "正在使用主订阅" :
    tier === "backup" ? (recovering ? `备用 · 主订阅恢复中 ${routing.recovery_successes || 0}/2` : "正在使用备用订阅") : "等待可用主备节点";
  $("#metric-mode").textContent = auto ? (priority ? "自动 · 主订阅优先" : "自动 · 全部订阅择优") : "手动指定节点";
  const tierBadge = $("#route-tier-badge");
  tierBadge.hidden = !priority;
  tierBadge.textContent = sourceLabel;
  tierBadge.className = `badge ${tier === "primary" ? "success" : "warning"}`;
  const badge = $("#route-mode-badge");
  badge.textContent = modeLabel;
  badge.classList.toggle("auto", auto && ready);
  $("#compact-mode").textContent = auto ? (ready ? "自动选择已开启" : "自动选择待就绪") : "手动指定节点";
  $("#compact-mode").classList.toggle("auto", auto && ready);
  $("#compact-tier").hidden = !priority;
  $("#compact-tier").textContent = sourceLabel;
  $("#compact-tier").className = `badge ${tier === "primary" ? "success" : "warning"}`;
  const button = $("[data-action=auto]");
  if (button) {
    button.classList.toggle("auto-active", auto && ready);
    button.setAttribute("aria-pressed", String(auto));
    button.disabled = auto || !ready || selectionPending;
    button.innerHTML = `${icon(auto && ready ? "check" : "globe")}${auto ? modeLabel : "启用自动选择"}`;
    button.title = auto ? `已设置自动模式；每 ${state.settings.test_interval} 秒检测候选节点，${priority ? "主订阅优先，失效时使用备用" : "全部订阅按延迟择优"}` : "切换到自动模式，按当前订阅策略选择出口";
  }
  if (!$("#selection-mode")) return;
  $("#selection-mode").textContent = modeLabel;
  $("#selection-mode").classList.toggle("auto", auto && ready);
  const candidateCount = state.nodes.filter((node) => node.eligible).length;
  $("#selection-rule").textContent = auto
    ? (ready ? (priority ? `优先使用主订阅“${routing.primary_name || "待确认"}”，主订阅内按延迟择优；无可用节点时使用备用订阅。` : `全部订阅共有 ${candidateCount} 个候选节点，按检测连通性和延迟择优，使用 50ms 切换容差减少抖动。`) : "已保存自动模式，等待代理服务就绪后按检测结果选择出口。")
    : "当前固定使用手动指定节点；定时检测继续运行，但不会自动更换出口。可点击右上角恢复自动选择。";
  $("#selection-tier-description").hidden = !priority;
  const reasons = {primary_disabled:"主订阅已停用",primary_filtered:"主订阅的节点已全部排除",primary_unavailable:"主订阅暂无检测可用节点",primary_checking:"主订阅仍在检测",all_unavailable:"主备均无可用节点，自动出口暂停连接",checking:"正在等待节点检测结果"};
  $("#selection-tier-description").textContent = `${sourceLabel}${auto && reasons[routing.reason] ? ` · ${reasons[routing.reason]}` : ""}。${auto ? "主订阅恢复后，连续通过两轮检测再回切；备用也不可用时优先恢复已可用的主线路。" : "主订阅优先策略将在启用自动选择后应用。"}`;
  $("#selection-source-rule").textContent = priority ? "先执行地区、关键词和停用规则；主订阅内择优，主订阅无可用节点时使用备用。所有候选节点持续检测，恢复后通过两轮检测再回切。" : "只使用已启用订阅中，未被地区、关键词或手动停用规则排除的候选节点，全部订阅按检测结果择优。";
  $("#selection-interval").textContent = intervalText(state.settings.test_interval);
  $("#selection-failover").textContent = auto ? (priority ? "主失效切备用 · 恢复稳定后回切" : "检测不可达后重新选路") : "保持指定节点，不自动切换";
  $("#selection-last-check").textContent = current?.checked_at ? timeText(current.checked_at) : "尚无检测结果";
  $("#selection-test-url").textContent = state.settings.test_url;
}

function showLogin(initialized) {
  clearInterval(pollTimer);
  $$('dialog[open]').forEach((dialog) => dialog.close());
  $("#app").hidden = true;
  $("#login-screen").hidden = false;
  window.RouteVisuals?.mode("login");
  $("#login-title").textContent = initialized ? "登录管理页面" : "设置你的管理密码";
  $("#login-hint").textContent = initialized ? "输入管理密码，继续管理你的连接。" : "首次使用，请设置一个至少 10 位的密码。";
  $("#login-submit").textContent = initialized ? "登录" : "设置密码并开始";
  $("#password").autocomplete = initialized ? "current-password" : "new-password";
}

async function enter() {
  $("#login-screen").hidden = true;
  $("#app").hidden = false;
  window.RouteVisuals?.mode("app");
  await load(true);
  clearInterval(pollTimer);
  pollTimer = setInterval(() => { void load(false); }, 5000);
}

async function load(force = false) {
  if (loading) return;
  loading = true;
  try {
    state = await api("/state");
    $("#connection-error").hidden = true;
    const next = location.hash.slice(1);
    if (force || !activeView || (views[next] && next !== activeView)) renderView(views[next] ? next : "subscriptions");
    renderSummary();
    if (!force && activeView === "nodes") renderNodes();
    if (!force && activeView === "subscriptions" && !$("#subscription-dialog").open && !$(".row-menu[open], .subscription-warnings[open]") && !dirty) renderSubscriptions();
    if (!force && activeView === "activity") renderActivity();
  } catch (error) {
    if (!$("#app").hidden) { $("#connection-error").textContent = error.message; $("#connection-error").hidden = false; }
  } finally { loading = false; }
}

function renderSummary() {
  const enabled = state.subscriptions.filter((s) => s.enabled).length;
  const candidates = state.nodes.filter((n) => n.eligible);
  const checked = candidates.filter((n) => n.checked_at);
  const available = candidates.filter((n) => n.status === "available");
  metricValue($("#metric-subs"), enabled);
  $("#metric-subs-note").textContent = `共 ${state.subscriptions.length} 个订阅`;
  metricValue($("#metric-nodes"), candidates.length);
  $("#metric-nodes-note").textContent = `共 ${state.nodes.length} 个节点，已排除 ${state.nodes.length - candidates.length} 个`;
  metricValue($("#metric-available"), available.length);
  $("#metric-available-note").textContent = `已检测 ${checked.length} 个 · 检测地址可达`;
  const current = state.nodes.find((n) => n.id === state.current_node);
  metricValue($("#metric-current"), state.core.running && current?.eligible && current?.delay ? `${current.delay} ms` : "—");
  $("#metric-current").title = "当前节点到检测地址的延迟";
  $("#metric-mode").textContent = state.selected === "AUTO" ? "自动选择出口" : "手动指定节点";
  const stage = $("#route-stage");
  stage.dataset.status = !state.core.running ? "offline" : current?.status === "available" ? "active" : "waiting";
  stage.dataset.busy = String(state.job.running || state.syncing);
  $("#route-status").textContent = state.syncing ? "订阅更新中" : state.job.running ? `检测中 ${state.job.done}/${state.job.total}` : !state.core.running ? "服务未就绪" : current?.status === "available" ? "节点检测可用" : current?.status === "unavailable" ? "当前节点检测失败" : current ? "等待节点检测" : "等待可用节点";
  $("#route-node").textContent = !state.core.running ? "代理服务未就绪" : current?.name || (state.nodes.length ? "暂无可用出口" : "从你的第一个订阅开始");
  $("#route-node").title = current?.name || "";
  $("#compact-node").textContent = $("#route-node").textContent;
  $("#compact-node").title = current?.name || "";
  $("#route-source").textContent = current ? `${current.subscription} / ${current.region_name} / ${current.type.toUpperCase()}` : candidates.length ? "正在为新连接选择节点" : "添加订阅或调整筛选条件，开始连接";
  $("#compact-source").textContent = current ? `${current.subscription} · ${current.region_name}` : "等待可用节点";
  $("#compact-health").textContent = state.syncing ? "订阅更新中" : state.job.running ? `检测中 ${state.job.done}/${state.job.total}` : !state.core.running ? "服务未就绪" : current?.status === "available" ? "检测可用" : current?.status === "unavailable" ? "检测失败" : "等待检测";
  $("#compact-latency").textContent = state.core.running && current?.eligible && current?.delay ? `${current.delay} ms` : "—";
  $("#connection-summary").dataset.status = stage.dataset.status;
  $("#connection-summary").dataset.busy = stage.dataset.busy;
  $("#route-destination").textContent = current?.region_name || "等待节点";
  $("#route-entry").textContent = `127.0.0.1:${state.settings.proxy_port}`;
  $("#network-caption").textContent = `${new Set(candidates.filter((n) => n.region !== "OTHER").map((n) => n.region)).size} 个已识别地区`;
  if (lastExit !== null && lastExit !== state.current_node) {
    stage.classList.remove("switching"); void stage.offsetWidth; stage.classList.add("switching");
  }
  lastExit = state.current_node;
  window.RouteVisuals?.update(state);
  document.body.classList.toggle("core-ready", state.core.running);
  $("#nav-sub-count").textContent = state.subscriptions.length;
  $("#nav-node-count").textContent = state.nodes.length;
  $("#sidebar-status").textContent = state.core.running ? "代理服务运行中" : "代理服务未就绪";
  $("#core-dot").classList.toggle("running", state.core.running);
  $("#last-sync").textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN", {hour12:false})}`;
  $("#footer-address").textContent = `本机入口 127.0.0.1:${state.settings.proxy_port}`;
  const warning = $("#core-warning");
  warning.hidden = state.core.running;
  warning.textContent = !state.core.installed ? "尚未安装代理内核。请先在项目目录运行 python scripts\\install_core.py，然后重启管理服务。" : state.core.error || "代理服务未就绪，可在连接设置中重新启动。";
  const progress = $("#test-progress");
  if (progress) progress.textContent = state.job.running ? `检测中 ${state.job.done} / ${state.job.total}` : state.job.error || "";
  const testButton = $("[data-action=test-all]");
  if (testButton) testButton.disabled = state.job.running || !state.core.running || !candidates.length;
  const refreshButton = $("[data-action=refresh-all]");
  if (refreshButton) { refreshButton.disabled = state.syncing; refreshButton.innerHTML = `${icon("refresh")}${state.syncing ? "正在更新…" : "更新全部"}`; }
  const totals = $("#node-totals");
  if (totals) totals.textContent = `候选 ${candidates.length} · 检测可用 ${available.length}`;
  renderSelectionStatus();
}

function renderView(view) {
  activeView = view; dirty = false;
  const [kicker, title, description] = views[view];
  $("#page-kicker").textContent = kicker;
  $("#page-title").textContent = title;
  $("#breadcrumb-page").textContent = title;
  $("#page-description").textContent = description;
  $$('[data-view]').forEach((a) => { a.classList.toggle("active", a.dataset.view === view); if (a.dataset.view === view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  $("#page-actions").innerHTML = view === "subscriptions" ? `<button data-action="refresh-all">${icon("refresh")}更新全部</button><button class="primary" data-action="add-sub">${icon("plus")}添加订阅</button>` : view === "nodes" ? `<button data-action="auto">${icon("globe")}启用自动选择</button><button class="primary" data-action="test-all">${icon("signal")}检测候选节点</button>` : "";
  if (view === "subscriptions") renderSubscriptions();
  if (view === "nodes") {
    $("#content").innerHTML = `<section class="panel"><div class="panel-head"><div><h2>节点列表</h2><p>已排除的节点保留在列表中，便于查看与调整。</p></div><div class="node-panel-tools"><span id="node-totals" class="muted"></span><span id="test-progress" class="progress-note"></span><button class="quiet" data-action="open-policy" aria-haspopup="dialog">选择规则</button></div></div><div class="toolbar"><input id="node-search" aria-label="搜索节点" placeholder="搜索节点名称、地区或订阅" value="${escapeHtml(search)}"><select id="source-filter" aria-label="筛选订阅"><option value="all">全部订阅</option>${state.subscriptions.map((s) => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join("")}</select><select id="status-filter" aria-label="筛选节点状态"><option value="all">全部状态</option><option value="candidate">候选节点</option><option value="available">检测可用</option><option value="unavailable">检测失败</option><option value="unknown">尚未检测</option><option value="excluded">已排除</option></select><span class="count" id="node-count"></span></div><div id="node-table"></div></section>`;
    $("#source-filter").value = state.subscriptions.some((s) => s.id === sourceFilter) ? sourceFilter : "all";
    sourceFilter = $("#source-filter").value;
    $("#status-filter").value = statusFilter;
    $("#node-search").addEventListener("input", (e) => { search = e.target.value; page = 1; renderNodes(); });
    $("#source-filter").addEventListener("change", (e) => { sourceFilter = e.target.value; page = 1; renderNodes(); });
    $("#status-filter").addEventListener("change", (e) => { statusFilter = e.target.value; page = 1; renderNodes(); });
    renderNodes();
  }
  if (view === "filters") renderFilters();
  if (view === "settings") renderSettings();
  if (view === "activity") renderActivity();
  window.RouteVisuals?.enter($("#content"));
}

function renderSubscriptions() {
  const priority = state.settings.selection_strategy === "primary";
  const primaryId = state.settings.primary_subscription_id;
  const activeId = state.routing?.active_subscription_id;
  const primary = state.subscriptions.find((sub) => sub.id === primaryId);
  const rows = state.subscriptions.map((sub) => {
    const main = priority && sub.id === primaryId;
    return `<article class="subscription-row ${main ? "is-primary-source" : ""}"><div class="source-identity"><span class="source-avatar" aria-hidden="true">${escapeHtml(Array.from(sub.name)[0] || "S")}</span><div><div class="source-title"><h3>${escapeHtml(sub.name)}</h3><span class="badge ${sub.enabled ? "success" : ""}">${sub.enabled ? "已启用" : "已停用"}</span>${priority ? `<span class="badge ${main ? "primary-source-badge" : ""}">${main ? "主订阅" : "备用订阅"}</span>` : ""}${activeId === sub.id ? '<span class="badge current">正在使用</span>' : ""}</div><div class="source-host">${escapeHtml(sourceHost(sub.url))}</div></div></div><div class="sub-stat"><strong>${sub.node_count}</strong><small>个节点</small></div><div class="sub-stat"><small>上次成功更新</small><div>${timeText(sub.last_success,true)}</div></div><div class="row-actions"><button data-action="refresh-sub" data-id="${sub.id}" ${!sub.enabled || state.syncing ? "disabled" : ""}>更新</button><details class="row-menu"><summary aria-label="${escapeHtml(sub.name)}的更多操作">更多<span aria-hidden="true">⌄</span></summary><div class="row-menu-panel"><button data-action="edit-sub" data-id="${sub.id}">编辑订阅</button><button class="${main ? "primary-source-button" : ""}" data-action="set-primary" data-id="${sub.id}" ${main || !sub.enabled ? "disabled" : ""}>${main ? "已设为主订阅" : "设为主订阅"}</button><button class="menu-danger" data-action="delete-sub" data-id="${sub.id}">删除订阅</button></div></details></div>${sub.error ? `<p class="subscription-error">更新失败：${escapeHtml(sub.error)}</p>` : ""}${sub.warnings.length ? `<details class="subscription-warnings"><summary>${sub.warnings.length} 条节点提示</summary><p>${escapeHtml(sub.warnings.join("；"))}</p></details>` : ""}</article>`;
  }).join("");
  $("#content").innerHTML = `<div class="strategy-strip"><div>${icon("settings")}<span>自动选路</span><strong>${priority ? "主订阅优先" : "全部订阅择优"}</strong>${priority && primary ? `<span class="strategy-source">${escapeHtml(primary.name)}</span>` : ""}</div><div><button class="quiet" data-action="open-policy" aria-haspopup="dialog">选择规则</button><button data-action="open-routing" aria-haspopup="dialog">调整策略</button></div></div><section class="panel subscription-list"><div class="panel-head"><div><h2>我的订阅 <span class="inline-count">${state.subscriptions.length}</span></h2><p>每 ${state.settings.refresh_hours} 小时更新 · 更新失败保留上次节点</p></div></div>${!state.subscriptions.length ? '<div class="empty"><div class="empty-badge"><span class="icon icon-subscriptions" aria-hidden="true"></span></div><h2>从第一个订阅开始</h2><p>添加订阅链接即可获取节点，再按地区筛选。</p><button class="primary" data-action="add-sub">添加订阅</button></div>' : rows}</section>`;
}

function renderRoutingEditor() {
  const priority = state.settings.selection_strategy === "primary";
  const primaryId = state.settings.primary_subscription_id;
  $("#routing-editor").innerHTML = `<p class="drawer-intro">选择自动模式如何使用多个订阅。手动指定节点时保持固定。</p><form id="routing-form" class="routing-form"><div><label for="routing-strategy">选择策略</label><select id="routing-strategy"><option value="latency" ${!priority ? "selected" : ""}>全部订阅择优</option><option value="primary" ${priority ? "selected" : ""}>主订阅优先</option></select></div><div id="primary-subscription-field" ${!priority ? "hidden" : ""}><label for="primary-subscription">主订阅</label><select id="primary-subscription" ${!priority ? "disabled" : "required"}><option value="">请选择已启用订阅</option>${state.subscriptions.map((sub) => `<option value="${sub.id}" ${sub.id === primaryId ? "selected" : ""} ${!sub.enabled ? "disabled" : ""}>${escapeHtml(sub.name)}${!sub.enabled ? "（已停用）" : ""}</option>`).join("")}</select></div><p id="routing-note">${priority ? "主订阅内择优，失效时使用备用；主订阅连续通过两轮检测后回切。" : "全部已启用订阅的候选节点按检测连通性和延迟择优。"}</p><div class="drawer-form-actions"><button type="button" data-action="close-panel">取消</button><button id="save-routing" type="submit" class="primary" disabled>保存策略</button></div></form><details class="inline-help"><summary>两种策略如何选择</summary><p>订阅质量接近时，可使用全部订阅择优。希望优先使用已经验证的线路，并用其他订阅兜底时，可选择主订阅优先。地区、关键词和节点停用规则始终先执行。</p></details>`;
  $("#routing-form").addEventListener("change", () => {
    const primary = $("#routing-strategy").value === "primary";
    $("#primary-subscription-field").hidden = !primary;
    $("#primary-subscription").disabled = !primary;
    $("#primary-subscription").required = primary;
    $("#save-routing").disabled = false;
    $("#routing-note").textContent = "有尚未保存的策略修改。";
    $("#drawer-unsaved").hidden = true;
    drawerDirty = true;
  });
  $("#routing-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await action($("#save-routing"), async () => {
      const strategy = $("#routing-strategy").value;
      await api("/routing", "PUT", {strategy,primary_subscription_id:strategy === "primary" ? $("#primary-subscription").value : null});
      drawerDirty = false;
      toast("自动选路策略已保存");
      await load(true);
      $("#workspace-drawer").close();
    });
  });
}

function renderNodes() {
  if (!$("#node-table")) return;
  const needle = search.trim().toLowerCase();
  const nodes = state.nodes.filter((n) => (sourceFilter === "all" || n.subscription_id === sourceFilter) &&
    (statusFilter === "all" || statusFilter === "candidate" && n.eligible || n.status === statusFilter) &&
    (!needle || `${n.name} ${n.subscription} ${n.region_name} ${n.type}`.toLowerCase().includes(needle)));
  const pages = Math.max(1, Math.ceil(nodes.length / 30)); page = Math.max(1, Math.min(page, pages));
  $("#node-count").textContent = `${nodes.length} 个节点`;
  const labels = {available:"检测可用", unavailable:"检测失败", unknown:"尚未检测", excluded:"已排除"};
  $("#node-table").innerHTML = !nodes.length ? '<div class="empty"><h2>暂无匹配节点</h2><p>可以调整搜索和筛选条件，或回到订阅管理更新节点。</p></div>' : `<div class="table-wrap"><table><thead><tr><th>节点</th><th class="node-source">订阅来源</th><th class="node-region">地区</th><th>状态 / 延迟</th><th>操作</th></tr></thead><tbody>${nodes.slice((page-1)*30,page*30).map((n) => `<tr class="${n.id === state.current_node ? "is-current" : ""} ${!n.eligible ? "is-excluded" : ""}"><td><div class="node-identity"><span class="region-mark" aria-hidden="true">${n.region === "OTHER" ? "—" : escapeHtml(n.region)}</span><div class="node-name"><strong>${escapeHtml(n.name)} ${n.id === state.current_node ? '<span class="badge current">当前</span>' : ""}</strong><small>${escapeHtml(n.type.toUpperCase())}${n.excluded_reason ? ` · ${escapeHtml(n.excluded_reason)}` : ""}</small></div></div></td><td class="node-source">${escapeHtml(n.subscription)}</td><td class="node-region">${escapeHtml(n.region_name)}</td><td><span class="badge ${n.status === "available" ? "success" : n.status === "unavailable" ? "danger" : ""}">${labels[n.status]}</span>${n.delay && n.eligible ? `<div class="latency"><span class="signal-bars ${n.delay > 350 ? "slow" : n.delay > 150 ? "medium" : ""}" aria-hidden="true"><i></i><i></i><i></i><i></i></span>${n.delay} ms</div>` : ""}</td><td class="node-actions"><div class="row-actions"><button class="${state.selected === n.id ? "is-selected" : ""}" data-action="select-node" data-id="${n.id}" ${!n.eligible || !state.core.running ? "disabled" : ""}>${state.selected === n.id ? "已选择" : "使用"}</button><button class="quiet" data-action="node-details" data-id="${n.id}">管理</button></div></td></tr>`).join("")}</tbody></table></div><div class="pagination"><span>共 ${nodes.length} 个 · ${page} / ${pages} 页</span><button data-action="prev-page" ${page <= 1 ? "disabled" : ""}>上一页</button><button data-action="next-page" ${page >= pages ? "disabled" : ""}>下一页</button></div>`;
}

function renderFilters() {
  const settings = state.settings;
  $("#content").innerHTML = `<div class="layout-split"><section class="panel"><div class="panel-head"><div><h2>排除地区</h2><p>勾选的地区将不参与节点选择，节点仍可在列表中查看。</p></div></div><form class="panel-body" id="filters-form"><div class="region-grid">${state.regions.map((r) => `<label class="region-option"><input type="checkbox" name="region" value="${r.code}" ${settings.excluded_regions.includes(r.code) ? "checked" : ""}>${escapeHtml(r.name)}<span>${state.nodes.filter((n) => n.region === r.code).length}</span></label>`).join("")}</div><div class="form-block"><label for="exclude-keywords">同时排除名称包含这些关键词的节点</label><textarea id="exclude-keywords" placeholder="每行一个关键词，例如：\n剩余流量\n到期时间">${escapeHtml(settings.exclude_keywords)}</textarea><p>按字面匹配，不区分英文字母大小写。留空表示不按关键词排除。</p></div><div class="form-bottom"><p id="dirty-note">保存后立即应用，后续更新继续生效。</p><button class="primary" type="submit">保存并应用筛选</button></div></form></section><aside class="panel"><div class="panel-body"><h2>筛选如何生效</h2><p class="muted">所有已启用订阅共用这份筛选条件。</p><ul class="help-list"><li><b>自动保留你的选择</b>订阅新增节点也会遵守地区与关键词设置。</li><li><b>地区来自节点名称</b>识别名称中的地区、国旗和常见英文缩写，不代表已核实真实出口位置。</li><li><b>可以手动修正</b>在节点列表点击“管理”，调整识别地区或停用单个节点。</li><li><b>没有候选时拒绝连接</b>当前手动节点被排除时会切回自动选择，不会自动直连。</li></ul></div></aside></div>`;
  const filterLayout = $("#content > .layout-split");
  filterLayout.classList.add("filter-layout");
  const filterHelp = $("aside.panel",filterLayout);
  const filterGuide = document.createElement("details");
  filterGuide.className = "inline-help";
  const filterSummary = document.createElement("summary");
  filterSummary.textContent = "筛选说明与地区识别规则";
  filterGuide.append(filterSummary);
  const filterHelpBody = $(".panel-body",filterHelp);
  $("h2",filterHelpBody).remove();
  filterGuide.append(...filterHelpBody.childNodes);
  $("#filters-form .form-bottom").before(filterGuide);
  filterHelp.remove();
  $("#filters-form").addEventListener("input", markDirty);
  $("#filters-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await action($("button[type=submit]", e.target), async () => {
      await api("/settings", "PUT", {...state.settings,
        excluded_regions: $$('input[name=region]:checked', e.target).map((v) => v.value),
        exclude_keywords: $("#exclude-keywords").value});
      dirty = false; toast("筛选已应用"); await load(true);
    });
  });
}

function endpoint(scheme) {
  const settings = state.settings;
  const auth = settings.proxy_username ? `${encodeURIComponent(settings.proxy_username)}:${encodeURIComponent(settings.proxy_password)}@` : "";
  return `${scheme}://${auth}127.0.0.1:${settings.proxy_port}`;
}

function renderSettings() {
  const s = state.settings;
  $("#content").innerHTML = `<div class="layout-split"><section class="panel"><div class="panel-head"><div><h2>代理入口与更新</h2><p>HTTP 和 SOCKS5 共用同一端口，应用切换出口时无需修改地址。</p></div></div><form id="settings-form" class="panel-body"><div class="form-grid"><div><label for="proxy-port">代理端口</label><input type="number" id="proxy-port" value="${s.proxy_port}" min="1024" max="65535" required></div><div><label for="refresh-hours">订阅更新间隔（小时）</label><input type="number" id="refresh-hours" value="${s.refresh_hours}" min="1" max="720" required></div></div><div class="form-block"><label for="test-url">连通性检测地址</label><input type="url" id="test-url" value="${escapeHtml(s.test_url)}" required><p>以 HTTP 200 / 204 为成功。检测可达不代表特定应用一定接受该节点。</p></div><div class="form-block"><label for="test-interval">自动检测间隔（秒）</label><input type="number" id="test-interval" value="${s.test_interval}" min="60" max="86400" required></div><div class="form-block"><label class="check-row"><input type="checkbox" id="allow-lan" ${s.allow_lan ? "checked" : ""}>允许其他机器连接此代理</label><p>开启后需同时设置代理账号、至少 10 位密码及允许访问的 IP。管理页面的访问地址由启动配置决定。</p></div><div class="form-grid"><div><label for="proxy-username">代理账号</label><input id="proxy-username" value="${escapeHtml(s.proxy_username)}" maxlength="80" autocomplete="off"></div><div><label for="proxy-password">代理密码</label><input type="password" id="proxy-password" value="${escapeHtml(s.proxy_password)}" maxlength="128" autocomplete="off"></div></div><div class="form-block"><label for="allowed-ips">允许访问的 IP / 网段</label><textarea id="allowed-ips" placeholder="每行一个，例如 192.168.1.0/24">${escapeHtml(s.allowed_ips.join("\n"))}</textarea><p>仅在允许其他机器连接时生效。也请按实际需要配置系统防火墙。</p></div><div class="form-bottom"><p id="dirty-note">保存后立即生效。</p><button type="submit" class="primary">保存并应用设置</button></div></form></section><aside><section class="panel"><div class="panel-body"><h2>连接你的应用</h2><p class="muted">将以下地址填入应用的代理设置。</p>${["http","socks5"].map((scheme) => `<div class="code-box"><code>${scheme}://${s.proxy_username ? "账号:密码@" : ""}127.0.0.1:${s.proxy_port}</code><button data-action="copy-${scheme}">复制</button></div>`).join("")}<p class="field-note">复制地址会包含你设置的代理账号与密码。跨机器连接时，将 127.0.0.1 换为代理服务所在机器的地址。</p></div></section><section class="panel"><div class="panel-body"><h2>代理服务</h2><p class="muted">重新启动不会清除订阅或筛选配置，进行中的连接可能中断。</p><button data-action="restart-core">重新启动服务</button></div></section></aside></div>`;
  const settingsForm = $("#settings-form");
  const commonFields = $(".form-grid",settingsForm);
  commonFields.classList.add("settings-grid");
  const intervalField = $("#test-interval").parentElement;
  intervalField.classList.remove("form-block");
  commonFields.append(intervalField);
  const makeDisclosure = (label,blocks,open = false) => {
    const details = document.createElement("details");
    details.className = "settings-advanced"; details.open = open;
    const summary = document.createElement("summary"); summary.textContent = label;
    const body = document.createElement("div"); body.append(...blocks);
    details.append(summary,body);
    $(".form-bottom",settingsForm).before(details);
  };
  makeDisclosure("连通性检测地址",[$("#test-url").closest(".form-block")]);
  makeDisclosure(`其他机器访问与认证${s.allow_lan ? " · 已开启" : ""}`,[$("#allow-lan").closest(".form-block"),$("#proxy-username").closest(".form-grid"),$("#allowed-ips").closest(".form-block")],s.allow_lan);
  const settingsAside = $("#content .layout-split > aside");
  const servicePanel = $$(".panel",settingsAside)[1];
  const serviceHelp = document.createElement("details"); serviceHelp.className = "service-disclosure";
  const serviceSummary = document.createElement("summary"); serviceSummary.textContent = "服务维护";
  const serviceBody = $(".panel-body",servicePanel); $("h2",serviceBody).remove();
  serviceHelp.append(serviceSummary,...serviceBody.childNodes);
  $(".panel-body",settingsAside).append(serviceHelp); servicePanel.remove();
  $("#settings-form").addEventListener("input", markDirty);
  $("#settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await action($("button[type=submit]", e.target), async () => {
      await api("/settings", "PUT", {...state.settings,
        proxy_port:Number($("#proxy-port").value), refresh_hours:Number($("#refresh-hours").value),
        test_url:$("#test-url").value.trim(), test_interval:Number($("#test-interval").value),
        allow_lan:$("#allow-lan").checked, proxy_username:$("#proxy-username").value,
        proxy_password:$("#proxy-password").value,
        allowed_ips:$("#allowed-ips").value.split("\n").map((v) => v.trim()).filter(Boolean)});
      dirty = false; toast("连接设置已应用"); await load(true);
    });
  });
}

function markDirty() { dirty = true; const note = $("#dirty-note"); if (note) note.textContent = "有尚未保存的修改。"; }

function renderActivity() {
  $("#content").innerHTML = `<section class="panel"><div class="panel-head"><div><h2>最近活动</h2><p>显示本次服务运行期间最近 200 条记录。</p></div></div>${state.events.length ? state.events.map((v) => `<div class="activity-row ${v.level === "error" ? "error-row" : ""}"><time>${timeText(v.time)}</time><p>${escapeHtml(v.message)}</p></div>`).join("") : '<div class="empty">暂无活动记录</div>'}</section>`;
}

function openSubscription(id) {
  const sub = state.subscriptions.find((s) => s.id === id);
  $("#subscription-title").textContent = sub ? "编辑订阅" : "添加订阅";
  $("#sub-id").value = sub?.id || "";
  $("#sub-name").value = sub?.name || "";
  $("#sub-url").value = sub?.url || "";
  $("#sub-url").type = "password";
  $("#sub-enabled").checked = sub?.enabled ?? true;
  $("#sub-error").textContent = "";
  $("#subscription-dialog").showModal();
}

async function refresh(id) {
  toast("正在获取订阅并检查节点，请稍候…");
  const result = await api(id ? `/subscriptions/${id}/refresh` : "/subscriptions/refresh", "POST");
  const failed = result.results.filter((v) => !v.ok);
  toast(failed.length ? `${failed.length} 个订阅更新失败，原节点已保留。详情见订阅列表。` : "订阅已更新，节点已应用", !!failed.length);
  await load(true);
}

async function manageNode(id) {
  const node = state.nodes.find((n) => n.id === id);
  if (!node) return;
  const dialog = document.createElement("dialog");
  dialog.setAttribute("aria-label", "管理节点");
  dialog.innerHTML = `<form><div class="dialog-head"><h2>管理节点</h2><button type="button" class="quiet" data-close>关闭</button></div><p class="muted">${escapeHtml(node.subscription)} · ${escapeHtml(node.name)}</p><label for="node-region-edit">节点地区</label><select id="node-region-edit"><option value="">自动识别</option>${state.regions.map((r) => `<option value="${r.code}" ${r.code === node.region ? "selected" : ""}>${escapeHtml(r.name)}</option>`).join("")}</select><label class="check-row"><input type="checkbox" id="node-disabled" ${node.manual_disabled ? "checked" : ""}>停用这个节点</label><p class="field-note">如果所在地区或订阅被排除，单独启用节点也不会取消那些筛选条件。</p><div class="dialog-actions"><button type="button" id="test-one" ${!node.eligible || state.job.running ? "disabled" : ""}>检测节点</button><button type="submit" class="primary">保存并应用</button></div></form>`;
  document.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove());
  $("[data-close]", dialog).onclick = () => dialog.close();
  $("#test-one", dialog).onclick = (e) => action(e.target, async () => { await api("/test", "POST", {node_id:id}); toast("已开始检测节点"); dialog.close(); await load(); });
  $("form", dialog).onsubmit = async (e) => {
    e.preventDefault();
    await action($("[type=submit]", dialog), async () => {
      await api(`/nodes/${id}`, "PATCH", {region:$("#node-region-edit", dialog).value, disabled:$("#node-disabled", dialog).checked});
      toast("节点设置已应用"); dialog.close(); await load(true);
    });
  };
  dialog.showModal();
}

document.addEventListener("click", async (event) => {
  const activeMenu = event.target.closest(".row-menu");
  $$(".row-menu[open]").forEach((menu) => { if (menu !== activeMenu) menu.removeAttribute("open"); });
  const button = event.target.closest("[data-action]");
  if (!button || !state) return;
  const id = button.dataset.id, kind = button.dataset.action;
  if (kind === "open-connection" || kind === "open-routing" || kind === "open-policy") { openWorkspaceDrawer(kind.slice(5),button); return; }
  if (kind === "close-panel") { requestDrawerClose(); return; }
  const parentMenu = button.closest(".row-menu");
  if (parentMenu) { parentMenu.removeAttribute("open"); $("summary",parentMenu).focus(); }
  if (kind === "add-sub" || kind === "edit-sub") { openSubscription(id); return; }
  if (kind === "prev-page" || kind === "next-page") { page += kind === "prev-page" ? -1 : 1; renderNodes(); return; }
  if (kind === "node-details") { await manageNode(id); return; }
  if (kind === "delete-sub") {
    const sub = state.subscriptions.find((s) => s.id === id);
    $("#confirm-message").textContent = `“${sub.name}”及其缓存节点将被移除。${state.settings.primary_subscription_id === id ? "删除主订阅后将恢复全部订阅择优。" : "其他订阅不受影响。"}`;
    const dialog = $("#confirm-dialog"); dialog.returnValue = "cancel";
    dialog.showModal();
    dialog.addEventListener("close", () => {
      if (dialog.returnValue === "confirm") void action(button, async () => { await api(`/subscriptions/${id}`, "DELETE"); toast("订阅已删除"); await load(true); });
    }, {once:true});
    return;
  }
  await action(button, async () => {
    if (kind === "refresh-all" || kind === "refresh-sub") await refresh(id);
    if (kind === "set-primary") { await api("/routing", "PUT", {strategy:"primary",primary_subscription_id:id}); dirty = false; toast("主订阅已设置，自动模式将优先使用该订阅"); await load(true); }
    if (kind === "select-node" || kind === "auto") {
      selectionPending = true;
      try {
        const selected = id || "AUTO";
        await api("/select", "POST", {node_id:selected});
        state.selected = selected;
        toast(id ? "已切换为手动节点，新连接将使用所选出口" : "自动选择已开启，将按检测结果选择出口");
        await load();
      } finally { selectionPending = false; }
    }
    if (kind === "test-all") { await api("/test", "POST", {}); toast("已开始检测候选节点"); await load(); }
    if (kind === "restart-core") { await api("/core/restart", "POST"); toast("代理服务已启动"); await load(); }
    if (kind.startsWith("copy-")) { await navigator.clipboard.writeText(endpoint(kind.slice(5))); toast("代理地址已复制"); }
  });
  if (state) renderSelectionStatus();
});

$("#subscription-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = $("#sub-id").value;
  const button = $("button[type=submit]", e.target); button.disabled = true; button.setAttribute("aria-busy", "true");
  try {
    await api(id ? `/subscriptions/${id}` : "/subscriptions", id ? "PUT" : "POST", {
      name:$("#sub-name").value.trim(), url:$("#sub-url").value.trim(), enabled:$("#sub-enabled").checked});
    $("#subscription-dialog").close(); toast("订阅已保存，点击更新即可获取节点"); await load(true);
  } catch (error) { $("#sub-error").textContent = error.message; }
  finally { button.disabled = false; button.removeAttribute("aria-busy"); }
});
$("#close-dialog").onclick = $("#cancel-dialog").onclick = () => $("#subscription-dialog").close();
$("#toggle-sub-url").onclick = () => { $("#sub-url").type = $("#sub-url").type === "password" ? "text" : "password"; };
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const button = $("#login-submit"); button.disabled = true; button.setAttribute("aria-busy", "true"); $("#login-error").textContent = "";
  try { await api("/login", "POST", {password:$("#password").value}); $("#password").value = ""; await enter(); }
  catch (error) { $("#login-error").textContent = error.message; }
  finally { button.disabled = false; button.removeAttribute("aria-busy"); }
});
$("#logout").onclick = $("#logout-mobile").onclick = (e) => action(e.currentTarget, async () => { await api("/logout", "POST"); state = null; activeView = ""; showLogin(true); });
$("#reload").onclick = (e) => action(e.currentTarget, () => load(false));
$("#close-drawer").onclick = requestDrawerClose;
$("#drawer-keep").onclick = () => {
  $("#drawer-unsaved").hidden = true;
  $("#routing-strategy").focus();
};
$("#drawer-discard").onclick = () => { drawerDirty = false; $("#workspace-drawer").close(); };
$("#workspace-drawer").addEventListener("cancel", (event) => { event.preventDefault(); requestDrawerClose(); });
$("#workspace-drawer").addEventListener("close", () => {
  drawerDirty = false;
  $("#drawer-unsaved").hidden = true;
  document.body.classList.remove("drawer-open");
  if (drawerFocus?.isConnected && !drawerFocus.disabled) drawerFocus.focus();
  else if (!$("#app").hidden) $("#main").focus();
});
$("#workspace-drawer").addEventListener("click", (event) => {
  const drawer = event.currentTarget;
  if (event.target === drawer) {
    const rect = drawer.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) requestDrawerClose();
  }
  const link = event.target.closest('a[href^="#"]');
  if (link) requestDrawerClose();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("#workspace-drawer").open) $$(".row-menu[open]").forEach((menu) => { menu.removeAttribute("open"); $("summary",menu).focus(); });
});
window.addEventListener("hashchange", () => {
  if (!state) return;
  const next = location.hash.slice(1);
  if (views[next] && next !== activeView) {
    if (dirty && !confirm("还有未保存的修改，确定离开此页面吗？")) { history.replaceState(null, "", `#${activeView}`); return; }
    renderView(next); renderSummary();
  }
});
window.addEventListener("beforeunload", (e) => { if (dirty || drawerDirty) { e.preventDefault(); e.returnValue = ""; } });
(async () => {
  try { const auth = await api("/auth"); if (auth.authenticated) await enter(); else showLogin(auth.initialized); }
  catch { showLogin(true); $("#login-error").textContent = "无法连接管理服务，请刷新页面重试"; }
})();
