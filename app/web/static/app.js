"use strict";

const $ = (id) => document.getElementById(id);
const ui = {csrf: "", data: null, group: "Обзор", query: "", saving: false, dirty: new Map(), toastTimer: null,
  role: "owner", auth_method: "password"};
const icons = {
  "Обзор": "◈", "Роли": "◇", "Каналы": "▤", "Участники": "♙",
  "Поддержка": "□", "Голосовые": "◉", "Прогрессия": "↗",
  "Экономика": "◌", "Задания": "◎", "Сообщество": "▦",
  "Статистика": "▥", "Модули": "◫", "Диагностика": "⊕",
};
const blurbs = {
  "Обзор": "Состояние и настройки Discord-сервера.",
  "Роли": "Управление ролями NewSquad. Настройки применяются через действующий бот.",
  "Каналы": "Выбирайте настоящие Discord-каналы. Неподходящие типы недоступны.",
  "Участники": "Вступление, приветствия и оформление новых участников.",
  "Поддержка": "Управление обращениями без хранения переписки закрытых тикетов.",
  "Голосовые": "Параметры временных комнат и голосовой активности.",
  "Прогрессия": "Правила начисления XP, уровней и исключённые каналы.",
  "Экономика": "Настройки NS Coins и наград за активность.",
  "Задания": "Ежедневные задания, достижения и связанные функции.",
  "Сообщество": "Предложения, мероприятия, розыгрыши и поиск команды.",
  "Статистика": "Сбор серверной статистики и настройки приватности рейтингов.",
  "Модули": "Управление загружаемыми модулями. Для применения нужен перезапуск.",
  "Диагностика": "Фактические проверки SQLite, Discord, ролей и фоновых процессов.",
};
function element(tag, className = "", text = null) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== null) node.textContent = String(text);
  return node;
}
function toast(message, error = false) {
  const box = $("toast");
  box.textContent = String(message);
  box.classList.toggle("error", error);
  box.hidden = false;
  if (ui.toastTimer) clearTimeout(ui.toastTimer);
  ui.toastTimer = setTimeout(() => { box.hidden = true; }, 4200);
}
async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = {"Accept": "application/json"};
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && ui.csrf) headers["X-CSRF-Token"] = ui.csrf;
  // An unresponsive backend must not leave loaders running indefinitely.
  // Mutating requests get more time and must never be retried automatically.
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), method === "GET" ? 25000 : 45000);
  try {
    const response = await fetch(path, {
      method, credentials: "same-origin", headers, signal: controller.signal,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    let value;
    try { value = await response.json(); }
    catch (error) {
      if (controller.signal.aborted) throw error;
      value = {};
    }
    if (!response.ok) {
      const error = new Error(value.error || ("Ошибка сервера, код " + response.status));
      error.status = response.status;
      throw error;
    }
    return value;
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error(method === "GET"
        ? "Сервер не ответил за 25 секунд. Повторите загрузку раздела."
        : "Ответ сервера не получен. Проверьте результат операции в Discord перед повтором.");
    }
    if (error instanceof TypeError) {
      throw new Error("Не удалось связаться с сервером. Проверьте запуск бота и соединение.");
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}
function loginScreen(visible) {
  $("login-screen").hidden = !visible;
  $("shell").hidden = visible;
  $("login-form").hidden = false;
  $("login-mode-label").textContent = "ЛОКАЛЬНАЯ ПАНЕЛЬ УПРАВЛЕНИЯ";
  $("login-security-note").textContent = "Локальный доступ по паролю. Сеанс завершается через 4 часа.";
  $("sidebar-access-label").textContent = "Только локальный доступ · 127.0.0.1";
  if (visible) {
    ui.data = null; ui.csrf = ""; ui.dirty.clear();
    Portal.stopNotices();
  }
}
async function initialize() {
  try {
    const branding = await api("/api/site");
    const icon = $("login-guild-icon");
    if (branding.icon_url) {
      icon.src = branding.icon_url;
      icon.hidden = false;
      $("login-initials").hidden = true;
    } else {
      icon.hidden = true;
      $("login-initials").textContent = (branding.name || "NS").slice(0, 2).toUpperCase();
    }
    const session = await api("/api/session");
    ui.auth_method = "password";
    if (session.authenticated) {
      ui.csrf = session.csrf;
      ui.role = session.role || "owner";
      loginScreen(false);
      await refresh();
    } else {
      loginScreen(true);
    }
  } catch (error) {
    loginScreen(true);
    $("login-error").textContent = error.message;
  }
}
async function refresh() {
  try {
    ui.data = await api("/api/bootstrap");
    ui.role = ui.data.viewer_role || "owner";
    $("viewer-role").textContent = Portal.roleLabels[ui.role] || ui.role.toUpperCase();
    const viewerImage = $("viewer-avatar");
    if (ui.data.viewer && ui.data.viewer.avatar_url) {
      viewerImage.src = ui.data.viewer.avatar_url;
      viewerImage.hidden = false;
      $("top-guild-initials").hidden = true;
    } else {
      viewerImage.hidden = true;
      $("top-guild-initials").hidden = false;
    }
    $("brand-guild-icon").hidden = !ui.data.guild_icon_url;
    $("brand-initials").hidden = Boolean(ui.data.guild_icon_url);
    if (ui.data.guild_icon_url) $("brand-guild-icon").src = ui.data.guild_icon_url;
    $("workspace-name").textContent = ui.data.guild_name;
    $("overview-guild-name").textContent = ui.data.guild_name;
    const initials = ui.data.guild_name.trim().slice(0, 2).toLocaleUpperCase("ru") || "NS";
    for (const [imgId, initialsId] of [
      ["workspace-icon", "workspace-initials"],
      ["overview-guild-icon", "overview-guild-initials"],
    ]) {
      const img = $(imgId);
      img.hidden = !ui.data.guild_icon_url;
      $(initialsId).hidden = Boolean(ui.data.guild_icon_url);
      if (ui.data.guild_icon_url) img.src = ui.data.guild_icon_url;
      else { img.removeAttribute("src"); $(initialsId).textContent = initials; }
    }
    $("guild-meta").textContent = (ui.data.guild_member_count === null
      ? "Участники: информация недоступна"
      : "Участников: " + Number(ui.data.guild_member_count).toLocaleString("ru-RU"))
      + "  ·  ID: " + ui.data.guild_id;
    $("guild-banner").style.backgroundImage = ui.data.guild_banner_url
      ? 'url("' + ui.data.guild_banner_url + '")' : "";
    $("metric-version").textContent = ui.data.version;
    $("footer-version").textContent = ui.data.version;
    $("metric-discord").textContent = ui.data.discord_ready ? "Online" : "Offline";
    $("metric-config").textContent = ui.data.total_configured + " / " + ui.data.settings.length;
    const active = Object.values(ui.data.modules || {}).filter((item) => item === "active").length;
    $("metric-modules").textContent = active + " / " + Object.keys(ui.data.modules).length;
    $("connection").classList.toggle("offline", !ui.data.discord_ready);
    $("connection").lastChild.textContent = ui.data.discord_ready ? " Discord подключён" : " Discord не готов";
    $("service-discord").textContent = ui.data.discord_ready ? "Доступен" : "Недоступен";
    renderNav();
    renderPage();
    Portal.startNotices();
    if (ui.role === "owner" && (ui.group === "Обзор" || ui.group === "Диагностика"))
      await refreshDiagnostics();
  } catch (error) {
    if (error.status === 401) loginScreen(true);
    toast(error.message, true);
  }
}
async function confirmDiscard() {
  if (ui.saving) { toast("Сохранение ещё выполняется.", true); return false; }
  const hasSettings = ui.dirty.size > 0;
  const hasReply = Boolean(window.Portal?.hasDraft?.() || window.StaffCenter?.hasDraft?.() || window.MemberSupportVoice?.hasDraft?.());
  if (!hasSettings && !hasReply) return true;
  return new Promise((resolve) => {
    const dialog = $("leave-dialog");
    $("dialog-title").textContent = hasReply ? "Несохранённый ответ" : "Несохранённые изменения";
    $("dialog-description").textContent = hasReply
      ? "В переписке есть неотправленный ответ. При переходе текст будет потерян и не сохранится на сайте."
      : "На этой странице есть изменения, которые ещё не сохранены. Если продолжить, они будут потеряны.";
    const previousFocus = document.activeElement;
    const stay = $("stay-here");
    const leave = $("leave-anyway");
    dialog.hidden = false;
    stay.focus();
    function handleKeys(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        finish(false);
      } else if (event.key === "Tab") {
        if (event.shiftKey && document.activeElement === stay) {
          event.preventDefault(); leave.focus();
        } else if (!event.shiftKey && document.activeElement === leave) {
          event.preventDefault(); stay.focus();
        }
      }
    }
    dialog.addEventListener("keydown", handleKeys);
    function finish(value) {
      dialog.removeEventListener("keydown", handleKeys);
      dialog.hidden = true;
      stay.onclick = null;
      leave.onclick = null;
      if (value) ui.dirty.clear();
      if (previousFocus?.isConnected) previousFocus.focus();
      resolve(value);
    }
    $("stay-here").onclick = () => finish(false);
    $("leave-anyway").onclick = () => finish(true);
  });
}
async function navigate(group) {
  if (group === ui.group) return true;
  if (!await confirmDiscard()) return false;
  ui.group = group;
  ui.query = "";
  $("search").value = "";
  $("sidebar").classList.remove("open");
  renderNav();
  renderPage();
  if (group === "Диагностика" || group === "Обзор") void refreshDiagnostics();
  window.scrollTo({top: 0, behavior: "smooth"});
  return true;
}
function renderNav() {
  const nav = $("navigation");
  nav.replaceChildren();
  if (!ui.data) return;
  const portalGroups = Portal.menu[ui.role] || Portal.menu.support;
  const groups = ui.role === "owner"
    ? [...portalGroups, ...ui.data.groups, "Диагностика"] : portalGroups;
  const workspace = new Set(["Обзор", "Единая очередь", "Кандидаты и адаптация", "SLA поддержки", "Мои задачи", "Уведомления", "Заявки", "Тикеты", "Шаблоны ответов"]);
  const insights = new Set(["Качество поддержки", "Аналитика", "Аналитика команды", "История сотрудников", "Рейтинги", "Активность", "Аудит"]);
  const system = new Set(["Диагностика", "Центр состояния", "Голосовые 2.0"]);
  const categorize = (group) => {
    if (workspace.has(group)) return "РАБОЧАЯ ОБЛАСТЬ";
    if (insights.has(group)) return "АНАЛИТИКА";
    if (system.has(group)) return "СИСТЕМА";
    if (ui.role === "owner" && ui.data.groups.includes(group)) return "КОНФИГУРАЦИЯ";
    return "УПРАВЛЕНИЕ";
  };
  for (const section of ["РАБОЧАЯ ОБЛАСТЬ", "УПРАВЛЕНИЕ", "АНАЛИТИКА", "КОНФИГУРАЦИЯ", "СИСТЕМА"]) {
    const items = groups.filter((group) => categorize(group) === section);
    if (!items.length) continue;
    const heading = element("div", "nav-section-label", section);
    heading.setAttribute("role", "presentation");
    nav.append(heading);
    for (const group of items) {
      const selected = ui.group === group;
      const button = element("button", "nav-item" + (selected ? " active" : ""));
      button.type = "button";
      button.dataset.group = group;
      button.setAttribute("aria-label", group);
      if (selected) button.setAttribute("aria-current", "page");
      button.append(Portal.icon(group));
      button.append(element("span", "nav-text", group));
      button.append(element("span", "nav-indicator"));
      button.addEventListener("click", () => { void navigate(group); });
      nav.append(button);
    }
  }
}

function renderPage() {
  if (!ui.data) return;
  const ownerOverview = ui.group === "Обзор" && ui.role === "owner";
  const isDiagnostic = ui.group === "Диагностика";
  const isConfig = ui.role === "owner" && ui.data.groups.includes(ui.group);
  const isPortal = !ownerOverview && !isDiagnostic && !isConfig;
  $("overview").hidden = !ownerOverview;
  $("settings-section").hidden = !isConfig;
  $("diagnostics-section").hidden = !isDiagnostic;
  $("portal-section").hidden = !isPortal;
  $("breadcrumb").textContent = ui.group;
  $("eyebrow").textContent = ownerOverview ? "РАБОЧЕЕ ПРОСТРАНСТВО"
    : isDiagnostic ? "МОНИТОРИНГ" : isPortal ? "УПРАВЛЕНИЕ СООБЩЕСТВОМ" : "КОНФИГУРАЦИЯ / NEW SQUAD";
  $("page-title").textContent = ownerOverview ? "Обзор системы" : ui.group === "Обзор"
    ? "Панель управления" : ui.group;
  $("page-subtitle").textContent = window.StaffCenter?.descriptions?.[ui.group]
    || window.Management?.descriptions?.[ui.group]
    || Portal.descriptions[ui.group] || blurbs[ui.group]
    || "Настройки и инструменты NewSqad.";
  if (isConfig) renderSettings();
  if (ownerOverview) void Portal.mountOwnerOverview();
  else Portal.stopLive();
  if (isPortal) void Portal.render(ui.group); else Portal.stopTicketRefresh();
}
function labelFor(spec) {
  const map = {"role": "Роль Discord", "channel": "Канал Discord",
               "bool": "Переключатель", "int": "Число", "choice": "Выбор",
               "timezone": "IANA", "channel_list": "Список каналов"};
  return spec.restart_required ? "После перезапуска" : (map[spec.kind] || "Параметр");
}
function option(value, text, selected = false, disabled = false) {
  const item = element("option", "", text);
  item.value = String(value);
  item.selected = selected;
  item.disabled = disabled;
  return item;
}
function createControl(spec, value = spec.value) {
  const current = value;
  let field;
  if (spec.kind === "bool") {
    const toggle = element("label", "toggle-wrap");
    field = element("input");
    field.type = "checkbox";
    field.checked = current === true;
    const status = element("span", "toggle-state", field.checked ? "Включено" : "Выключено");
    field.addEventListener("change", () => { status.textContent = field.checked ? "Включено" : "Выключено"; });
    toggle.append(field, status);
    return [toggle, field];
  }
  if (["role", "channel", "choice", "channel_list"].includes(spec.kind)) {
    field = element("select");
    if (spec.kind === "role" || spec.kind === "channel") {
      field.append(option("", "— Не выбрано —", current === null || current === undefined));
      const pool = spec.kind === "role" ? ui.data.roles : ui.data.channels.filter((item) => item.type === spec.channel_kind);
      const values = new Set();
      for (const item of pool) {
        values.add(String(item.id));
        field.append(option(item.id, item.name + " · " + item.id, String(current) === String(item.id),
                            item.managed === true));
      }
      if (current !== null && current !== undefined && !values.has(String(current)))
        field.append(option(current, "Удалённый объект · " + current, true, true));
    } else if (spec.kind === "choice") {
      for (const [value, text] of spec.choices)
        field.append(option(value, text, current === value));
    } else {
      field.multiple = true;
      field.size = 5;
      const selected = Array.isArray(current) ? current : [];
      const pool = ui.data.channels.filter((item) => item.type === "text");
      const values = new Set();
      for (const item of pool) {
        values.add(String(item.id));
        field.append(option(item.id, item.name, selected.map(String).includes(String(item.id))));
      }
      for (const id of selected) if (!values.has(String(id))) field.append(option(id, "Удалённый канал · " + id, true, true));
    }
    return [field, field];
  }
  field = element("input");
  field.type = spec.kind === "int" ? "number" : "text";
  if (spec.kind === "int") {
    field.min = String(spec.minimum);
    field.max = String(spec.maximum);
    field.step = "1";
  } else {
    field.maxLength = 64;
    field.spellcheck = false;
  }
  field.value = current === undefined || current === null ? "" : String(current);
  return [field, field];
}
function readValue(spec, field) {
  if (spec.kind === "bool") return field.checked;
  if (spec.kind === "int") {
    if (field.value.trim() === "" || !/^-?\d+$/.test(field.value.trim())) throw new Error("Укажите целое число.");
    return Number(field.value);
  }
  if (spec.kind === "role" || spec.kind === "channel") return field.value || null;
  if (spec.kind === "channel_list") return [...field.selectedOptions].map((item) => item.value);
  return field.value.trim();
}
function updateSaveBar() {
  const count = ui.dirty.size;
  $("bulk-save").hidden = count === 0;
  $("pending-count").textContent = count + " " + (
    count % 10 === 1 && count % 100 !== 11 ? "изменение" :
    [2, 3, 4].includes(count % 10) && ![12, 13, 14].includes(count % 100) ? "изменения" : "изменений");
  $("save-all").disabled = count === 0 || ui.saving;
  $("discard-changes").disabled = ui.saving;
}
function renderSettings() {
  const grid = $("settings-grid");
  grid.replaceChildren();
  const found = ui.data.settings.filter((spec) => spec.group === ui.group
    && (spec.label + " " + spec.key + " " + spec.description).toLocaleLowerCase("ru")
      .includes(ui.query.toLocaleLowerCase("ru")));
  $("item-count").textContent = found.length + " параметров";
  $("empty").hidden = found.length !== 0;
  for (const spec of found) {
    const card = element("article", "setting" + (ui.dirty.has(spec.key) ? " setting-dirty" : ""));
    const head = element("div", "setting-top");
    const names = element("div");
    names.append(element("div", "setting-name", spec.label), element("div", "setting-key", spec.key));
    head.append(names, element("span", "setting-tag", labelFor(spec)));
    card.append(head);
    card.append(element("p", "setting-description",
      spec.description || "Сохраняется в настройках сервера NewSquad."));
    if (spec.warning) card.append(element("div", "setting-warning", spec.warning));
    const controls = element("div", "setting-controls");
    const savedDraft = ui.dirty.has(spec.key) ? ui.dirty.get(spec.key) : spec.value;
    const shownDraft = savedDraft && savedDraft.invalid ? savedDraft.raw : savedDraft;
    const [widget, field] = createControl(spec, shownDraft);
    field.setAttribute("aria-label", spec.label);
    const note = element("span", "field-note",
      ui.dirty.has(spec.key) ? "Не сохранено" : (spec.configured ? "Сохранено" : "По умолчанию"));
    function track() {
      let value;
      try { value = readValue(spec, field); }
      catch { value = {invalid: true, raw: field.value}; }
      if (!value?.invalid && JSON.stringify(value) === JSON.stringify(spec.value)) {
        ui.dirty.delete(spec.key);
      } else {
        ui.dirty.set(spec.key, value);
      }
      card.classList.toggle("setting-dirty", ui.dirty.has(spec.key));
      note.textContent = ui.dirty.has(spec.key) ? "Не сохранено" : "Сохранено";
      updateSaveBar();
    }
    field.addEventListener("change", track);
    field.addEventListener("input", track);
    controls.append(widget);
    card.append(controls, note);
    grid.append(card);
  }
  updateSaveBar();
}
async function saveAll() {
  if (!ui.dirty.size || ui.saving) return;
  const changes = Object.fromEntries(ui.dirty);
  if (Object.values(changes).some((value) => value && value.invalid)) {
    toast("Проверьте числовые значения перед сохранением.", true);
    return;
  }
  ui.saving = true;
  updateSaveBar();
  $("save-all").textContent = "Сохраняю...";
  try {
    const result = await api("/api/settings/batch", {
      method: "POST", body: {group: ui.group, changes},
    });
    for (const [key, value] of Object.entries(result.values)) {
      const spec = ui.data.settings.find((item) => item.key === key);
      if (spec) { spec.value = value; spec.configured = true; spec.warning = ""; }
    }
    ui.dirty.clear();
    renderSettings();
    ui.data.total_configured = ui.data.settings.filter((item) => item.configured).length;
    $("metric-config").textContent = ui.data.total_configured + " / " + ui.data.settings.length;
    toast(result.restart_required
      ? "Все изменения сохранены. Для применения настроек модулей перезапустите бота."
      : "Сохранено параметров: " + result.count);
  } catch (error) {
    if (error.status === 401) loginScreen(true);
    toast(error.message, true);
  } finally {
    ui.saving = false;
    $("save-all").textContent = "Сохранить изменения";
    updateSaveBar();
  }
}
async function refreshDiagnostics() {
  if (!ui.data) return;
  try {
    const result = await api("/api/diagnostics");
    $("service-db").textContent = result.ready ? "Проверяется" : "Ожидание";
    $("service-scheduler").textContent = "—";
    $("service-events").textContent = "—";
    const list = $("diagnostics-list");
    list.replaceChildren();
    for (const item of result.items || []) {
      const row = element("div", "diag-item");
      row.append(element("span", "diag-dot " + item.level));
      const content = element("div", "diag-content");
      content.append(element("strong", "", item.name), element("p", "", item.description));
      row.append(content);
      list.append(row);
      if (item.name === "SQLite") $("service-db").textContent = item.level === "ok" ? "Доступна" : "Ошибка";
      if (item.name === "Scheduler") $("service-scheduler").textContent = item.level === "ok" ? "Работает" : "Внимание";
      if (item.name === "EventBus") $("service-events").textContent = item.level === "ok" ? "Работает" : "Внимание";
    }
    if (!result.items || !result.items.length) list.append(element("div", "empty", "Нет данных диагностики."));
  } catch (error) {
    if (ui.group === "Диагностика") toast(error.message, true);
  }
}
window.portalAPI = (path, options) => api(path, options);
window.portalGroup = () => ui.group;
window.portalRole = () => ui.role;
window.portalData = () => ui.data;
window.portalNavigate = (group) => navigate(group);
window.portalFindSetting = async (group, key) => {
  // Only the owner can open settings returned in the authenticated bootstrap.
  const exists = ui.role === "owner" && ui.data?.groups.includes(group)
    && ui.data.settings.some((spec) => spec.group === group && spec.key === key);
  if (!exists || !await navigate(group)) return false;
  ui.query = key;
  $("search").value = key;
  renderSettings();
  $("search").focus();
  return true;
};
window.portalReload = () => Portal.render(ui.group);
window.portalToast = (message, error = false) => toast(message, error);
$("notice-button").addEventListener("click", () => {void navigate("Уведомления");});
$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = $("login-submit");
  const password = $("password").value;
  submit.disabled = true;
  $("login-error").textContent = "";
  try {
    const result = await api("/api/login", {method: "POST", body: {password}});
    ui.csrf = result.csrf;
    $("password").value = "";
    loginScreen(false);
    await refresh();
  } catch (error) {
    $("login-error").textContent = error.message;
  } finally { submit.disabled = false; }
});
$("logout").addEventListener("click", async () => {
  if (!await confirmDiscard()) return;
  try { await api("/api/logout", {method: "POST", body: {}}); }
  catch (error) { toast(error.message, true); return; }
  loginScreen(true);
});
$("refresh").addEventListener("click", async () => {
  if (await confirmDiscard()) await refresh();
});
$("save-all").addEventListener("click", () => { void saveAll(); });
$("discard-changes").addEventListener("click", () => {
  ui.dirty.clear();
  renderSettings();
  toast("Изменения сброшены.");
});
window.addEventListener("beforeunload", (event) => {
  if (ui.dirty.size || ui.saving || window.Portal?.hasDraft?.() || window.StaffCenter?.hasDraft?.()) {
    event.preventDefault();
    event.returnValue = "";
  }
});
$("refresh-diagnostics").addEventListener("click", refreshDiagnostics);
$("open-diagnostics").addEventListener("click", () => navigate("Диагностика"));
$("quick-roles").addEventListener("click", () => navigate("Роли"));
$("search").addEventListener("input", (event) => { ui.query = event.target.value; renderSettings(); });
$("menu-toggle").addEventListener("click", () => $("sidebar").classList.toggle("open"));
document.addEventListener("keydown", (event) => {
  if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey
      && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)
      && !$("settings-section").hidden) {
    event.preventDefault(); $("search").focus();
  }
  if (event.key === "Escape") $("sidebar").classList.remove("open");
});
void initialize();
