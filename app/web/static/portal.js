"use strict";

/* All user/Discord text uses textContent, never HTML insertion. */
(function () {
  const descriptions = {
    "Заявки": "Рассмотрение кандидатов, история решений и запросы уточнений.",
    "Тикеты": "Работа с приватными обращениями и перепиской Discord.",
    "Сотрудники": "Состав команды и доступность сотрудников.",
    "Список участников": "Участники сервера, роли и поиск по имени.",
    "Модерация": "Активные и завершённые дела модерации.",
    "Аналитика": "Аналитика NewSqad без хранения содержимого сообщений.",
    "Рейтинги": "Рейтинг активности с учётом настроек приватности.",
    "Аудит": "Системный журнал действий и настроек.",
    "Активность": "Агрегированная активность администрации за 30 дней.",
    "Качество поддержки": "Оценки 1–5 и скорость первого ответа без хранения переписки.",
    "Уведомления": "Персональные события и назначения с отметкой прочитанного.",
  };
  const menu = {
    support: ["Обзор", "Уведомления", "Заявки", "Тикеты"],
    moder: ["Обзор", "Уведомления", "Заявки", "Тикеты", "Сотрудники", "Список участников", "Модерация"],
    admin: ["Обзор", "Уведомления", "Заявки", "Тикеты", "Качество поддержки", "Сотрудники", "Список участников", "Модерация",
            "Аналитика", "Рейтинги", "Активность", "Аудит", "Мероприятия", "Розыгрыши", "Предложения", "Голосовые комнаты", "Поиск команды"],
    owner: ["Обзор", "Единая очередь", "Кандидаты и адаптация", "SLA поддержки", "Голосовые 2.0", "Мои задачи", "История сотрудников", "Шаблоны ответов", "Центр состояния", "Аналитика команды", "Уведомления", "Заявки", "Тикеты", "Качество поддержки", "Сотрудники", "Список участников", "Модерация",
            "Аналитика", "Рейтинги", "Активность", "Аудит", "Мероприятия", "Розыгрыши",
            "Предложения", "Голосовые комнаты", "Поиск команды",
            "Магазин NS Coins", "Магазин заказов", "Панели ролей", "Правила ролей", "Категории тикетов", "Объявления"],
  };
  const roleLabels = {owner: "OWNER", admin: "ADMIN", moder: "MODERATOR", support: "TICKET STAFF"};
  const glyphs = {
    "Обзор": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "Роли": '<circle cx="9" cy="8" r="3"/><path d="M3 21v-2a6 6 0 0 1 12 0v2"/><path d="m16 11 2 2 4-4"/>',
    "Каналы": '<path d="M4 8h16M4 16h16M8 4v16M16 4v16"/>',
    "Список участников": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    "Поддержка": '<path d="M4 13v-3a8 8 0 0 1 16 0v3M4 13H2v5h5v-5H4zm16 0h2v5h-5v-5h3z"/><path d="M20 18a5 5 0 0 1-5 4h-3"/>',
    "Голосовые": '<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 17v5m-4 0h8"/>',
    "Прогрессия": '<path d="m3 17 6-6 4 4 8-8M15 7h6v6"/>',
    "Экономика": '<circle cx="12" cy="12" r="9"/><path d="M15.5 9c-.8-1.8-6.5-2-6.5.5 0 3 6.5 1.5 6.5 5 0 3-6.7 3.2-7.5 1M12 5v14"/>',
    "Задания": '<path d="m9 12 2 2 4-4"/><rect x="4" y="3" width="16" height="18" rx="2"/>',
    "Сообщество": '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    "Аналитика": '<path d="M4 20V9m5 11V4m5 16v-7m5 7V7"/>',
    "Модули": '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>',
    "Диагностика": '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    "Заявки": '<path d="M7 3h10l4 4v14H3V3zM17 3v5h5M7 12h10M7 16h7"/>',
    "Тикеты": '<path d="M3 5h18v4a3 3 0 0 0 0 6v4H3v-4a3 3 0 0 0 0-6V5zM13 5v14" stroke-dasharray="3 3"/>',
    "Сотрудники": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0M19 4l2 2m-2 0 2-2"/>',
    "Модерация": '<path d="M12 2 4 6v6c0 5 3 8 8 10 5-2 8-5 8-10V6L12 2z"/><path d="m9 12 2 2 4-4"/>',
    "Рейтинги": '<path d="M8 21V9H3v12m10 0V3H8v18m10 0v-9h-5v9M2 21h20"/>',
    "Активность": '<path d="M3 12h4l3-6 4 12 3-6h4"/>',
    "Аудит": '<path d="M5 3h14v18H5zM8 8h8M8 12h8M8 16h5"/>',
    "Качество поддержки": '<path d="m12 2 3 6.5 7 .9-5.1 4.9 1.2 6.8L12 18l-6.1 3.1 1.2-6.8L2 9.4l7-.9z"/>',
    "Уведомления": '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
    "Магазин заказов": '<path d="M3 7h18l-2 6H5L3 7zM5 13v8h14v-8M10 21v-5h4v5M4 7l2-4h12l2 4M16 16h2"/>',
    "Магазин NS Coins": '<path d="M3 7h18l-2 6H5L3 7zM5 13v8h14v-8M10 21v-5h4v5M4 7l2-4h12l2 4"/>',
    "Панели ролей": '<path d="M4 4h16v16H4zM8 9h8M8 14h8M9 18h6"/>',
    "Правила ролей": '<path d="M9 20H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v4M8 9h8M8 13h5m2 5 2 2 4-4"/>',
    "Категории тикетов": '<path d="M3 6h18v5a2 2 0 0 0 0 4v4H3v-4a2 2 0 0 0 0-4V6z"/>',
    "Объявления": '<path d="M4 10v4h4l11 5V5L8 10H4zM8 14l2 6"/>',
    "Мероприятия": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4M17 3v4M3 10h18"/>',
    "Розыгрыши": '<rect x="3" y="9" width="18" height="13" rx="2"/><path d="M2 9h20V6H2zM5 13v8h14v-8M12 9v13"/>',
    "Предложения": '<path d="M9 18h6M10 22h4M8 14a7 7 0 1 1 8 0c-2 2-1 3-4 3s-2-1-4-3z"/>',
    "Голосовые комнаты": '<path d="M4 9v6M8 6v12m4-15v18m4-15v12m4-9v6"/>',
    "Поиск команды": '<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2m2-8h5m-2-2v5"/>',
  };
  function icon(name) {
    const node = document.createElement("span");
    node.className = "nav-svg";
    node.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
      + (glyphs[name] || glyphs["Обзор"]) + "</svg>";
    return node;
  }
  function e(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined && value !== null) node.textContent = String(value);
    return node;
  }
  function append(parent, ...children) { for (const child of children) parent.append(child); return parent; }
  function format(time) {
    return time ? new Date(time * 1000).toLocaleString("ru-RU", {dateStyle: "medium", timeStyle: "short"}) : "—";
  }
  function button(label, handler, style = "secondary") {
    const node = e("button", "button " + style, label);
    node.type = "button";
    node.addEventListener("click", handler);
    return node;
  }
  function info(title, text) {
    return append(e("div", "portal-empty"),
      e("strong", "", title), e("p", "", text));
  }
  function pill(text, kind = "neutral") { return e("span", "portal-pill " + kind, text); }
  function section(title, subtitle) {
    return append(e("div", "portal-heading"), e("h2", "", title), e("p", "", subtitle));
  }
  async function api(path, options) { return window.portalAPI(path, options); }
  const root = () => document.getElementById("portal-section");
  let latestLoad = 0;
  async function load(group, loader) {
    const loadId = ++latestLoad;
    const host = root();
    host.replaceChildren(section(group, descriptions[group] || "Данные сообщества."),
                         e("div", "portal-loader", "Получаем данные из Discord..."));
    try {
      const node = await loader();
      if (window.portalGroup() !== group || loadId !== latestLoad) return;
      host.replaceChildren(section(group, descriptions[group]), node);
    } catch (error) {
      if (window.portalGroup() !== group || loadId !== latestLoad) return;
      const failure = info("Не удалось загрузить данные", error.message);
      failure.append(button("Повторить загрузку", () => { void render(group); }));
      host.replaceChildren(section(group, descriptions[group]), failure);
    }
  }
  let liveTimer = null;
  let noticeTimer = null;
  function serviceStatus(title, ok) {
    const row = e("div", "portal-service");
    row.append(e("span", "", title), pill(ok ? "Работает" : "Недоступно", ok ? "success" : "warning"));
    return row;
  }
  async function overview() {
    const result = await api("/api/portal/dashboard");
    const box = e("div", "portal-stack portal-live overview-product");
    const top = e("div", "portal-dashboard-head");
    const headline = e("div");
    headline.append(
      e("span", "panel-kicker", "ЦЕНТР УПРАВЛЕНИЯ / LIVE"),
      e("h2", "", "Сервер в одном взгляде"),
      e("p", "portal-muted", "Обновлено " + new Date(result.generated_at * 1000).toLocaleTimeString("ru-RU")
        + " · Автоматическое обновление каждые 20 секунд")
    );
    const healthy = result.discord_connected && result.sqlite_connected
      && result.scheduler_running && result.events_running && !result.module_errors.length;
    top.append(headline, e("span", "portal-live-badge" + (healthy ? "" : " ob-health-alert"),
      healthy ? "СЛУЖБЫ РАБОТАЮТ" : "ТРЕБУЕТСЯ ВНИМАНИЕ"));
    box.append(top);

    const metrics = e("div", "portal-metrics overview-kpis");
    const items = [
      ["01", "Открытые обращения", result.open_tickets, "Тикеты в работе"],
      ["02", "Заявки на рассмотрении", result.pending_applications, "Ожидают решения"],
      ["03", "Сотрудники в сети", result.presence_available ? result.online_staff : "—",
        result.presence_available ? "С учётом статусов Discord" : "Presence Intent не включён"],
      ["04", "Ваш уровень доступа", roleLabels[result.role] || result.role, "Права веб-портала"],
    ];
    for (const [number, title, value, hint] of items) {
      const card = e("div", "portal-metric overview-kpi");
      card.append(
        e("span", "overview-kpi-index", number),
        e("span", "overview-kpi-label", title),
        e("strong", "", value),
        e("small", "overview-kpi-hint", hint));
      metrics.append(card);
    }
    box.append(metrics);

    const quick = e("nav", "ob-dashboard-actions");
    quick.setAttribute("aria-label", "Быстрые действия");
    const actionsToShow = [
      ["Заявки на рассмотрении", result.pending_applications, "Заявки"],
      ["Открытые обращения", result.open_tickets, "Тикеты"],
      ["Уведомления", "Открыть", "Уведомления"]
    ];
    for (const [title, count, destination] of actionsToShow) {
      const control = button(title + " · " + count + "  ↗",
        () => { void window.portalNavigate(destination); }, "secondary");
      control.classList.add("ob-dashboard-action");
      quick.append(control);
    }
    box.append(quick);

    const cols = e("div", "portal-dashboard-columns");
    const workers = e("section", "portal-panel overview-operations");
    const servicesHead = e("div", "overview-panel-heading");
    servicesHead.append(e("div", "", ""), pill(
      result.discord_connected && result.sqlite_connected && result.scheduler_running && result.events_running
        ? "Все службы доступны" : "Проверка состояния",
      result.discord_connected && result.sqlite_connected && result.scheduler_running && result.events_running
        ? "success" : "warning"));
    servicesHead.firstChild.append(
      e("span", "panel-kicker", "ИНФРАСТРУКТУРА"),
      e("h3", "", "Работа сервисов"));
    workers.append(servicesHead);
    workers.append(serviceStatus("Discord Gateway", result.discord_connected));
    workers.append(serviceStatus("База данных SQLite", result.sqlite_connected));
    workers.append(serviceStatus("Планировщик задач", result.scheduler_running));
    workers.append(serviceStatus("Очередь событий", result.events_running));
    if (result.module_errors.length) workers.append(
      e("p", "portal-warning-text", "Ошибка модулей: " + result.module_errors.join(", ")));

    const staffPanel = e("section", "portal-panel overview-team");
    const teamHeader = e("div", "overview-panel-heading");
    teamHeader.append(e("div", "", ""), e("span", "overview-team-count", String(result.staff.length).padStart(2, "0")));
    teamHeader.firstChild.append(e("span", "panel-kicker", "КОМАНДА СЕРВЕРА"), e("h3", "", "Сотрудники"));
    staffPanel.append(teamHeader);
    if (!result.presence_available) staffPanel.append(e("p", "portal-muted",
      "Статусы недоступны: включите Presence Intent в настройках бота и Discord."));
    const staffList = e("div", "portal-staff-mini");
    for (const member of result.staff) {
      const row = e("div", "portal-staff-row");
      const avatar = e("img", "portal-person-avatar");
      avatar.src = member.avatar_url;
      avatar.alt = "";
      avatar.loading = "lazy";
      const detail = e("div", "portal-staff-name");
      detail.append(e("strong", "", member.name), e("span", "", roleLabels[member.role] || member.role));
      const status = {online: "В сети", idle: "Неактивен", dnd: "Не беспокоить", offline: "Не в сети"};
      row.append(avatar, detail, pill(status[member.status] || "Статус недоступен",
        member.status === "online" ? "success" : "neutral"));
      staffList.append(row);
    }
    if (!result.staff.length) staffList.append(e("p", "portal-muted", "Сотрудники не найдены."));
    staffPanel.append(staffList);
    cols.append(workers, staffPanel);
    box.append(cols);

    if (result.recent_actions.length) {
      const timeline = e("section", "portal-panel overview-recent");
      const header = e("div", "overview-panel-heading");
      const title = e("div");
      title.append(e("span", "panel-kicker", "ИСТОРИЯ"), e("h3", "", "Последние действия"));
      header.append(title, button("Весь аудит", () => {void window.portalNavigate("Аудит");}, "secondary"));
      timeline.append(header);
      const names = {"settings.updated": "Изменена настройка",
        "announcements.published": "Опубликовано объявление",
        "web.events.created": "Создано мероприятие",
        "web.shop.created": "Добавлен товар"};
      for (const entry of result.recent_actions) {
        const row = e("div", "portal-service");
        const detail = e("div", "portal-staff-name");
        detail.append(e("strong", "", entry.action_title || names[entry.action] || entry.action.replaceAll(".", " / ")),
          e("span", "", "Исполнитель: " + (entry.actor_name || ("ID " + entry.actor_id))));
        row.append(detail, e("span", "portal-meta", format(entry.at)));
        timeline.append(row);
      }
      box.append(timeline);
    }
    return box;
  }
  async function refreshDashboard() {
    if (window.portalGroup() !== "Обзор" || document.getElementById("shell").hidden) return;
    const isOwner = window.portalRole() === "owner";
    const target = document.getElementById(isOwner ? "overview-live" : "portal-section");
    if (!target || target.hidden || (isOwner && document.getElementById("overview").hidden)) return;
    try {
      const node = await overview();
      if (window.portalGroup() !== "Обзор" || target.hidden) return;
      target.replaceChildren(node);
    } catch (error) {
      target.replaceChildren(info("Нет связи с системой", error.message));
    }
  }
  function startLive() {
    if (liveTimer) clearInterval(liveTimer);
    liveTimer = setInterval(() => {void refreshDashboard();}, 20000);
  }
  function stopLive() {
    if (liveTimer) clearInterval(liveTimer);
    liveTimer = null;
  }
  async function mountOwnerOverview() {
    startLive();
    await refreshDashboard();
  }
  async function supportQuality() {
    const wrap = e("div", "portal-stack");
    const toolbar = e("div", "portal-toolbar");
    const periods = e("select", "portal-select");
    for (const [value, label] of [["7", "7 дней"], ["30", "30 дней"], ["90", "90 дней"]]) {
      const option = e("option", "", label);option.value = value;
      if (value === "30") option.selected = true;
      periods.append(option);
    }
    toolbar.append(e("span", "portal-muted", "Период"), periods);
    const body = e("div", "portal-stack");
    async function update() {
      try {
        const data = await api("/api/portal/support-quality?days=" + periods.value);
        const cards = e("div", "portal-metrics");
        for (const [label, value] of [
          ["Средняя оценка", data.average_rating === null ? "—" : data.average_rating + " / 5"],
          ["Всего оценок", data.ratings],
          ["Положительных", data.positive_percent === null ? "—" : data.positive_percent + "%"],
          ["Средний первый ответ", data.avg_first_response_seconds === null ? "—"
            : Math.round(data.avg_first_response_seconds / 60) + " мин"],
        ]) cards.append(append(e("div", "portal-metric"),
          e("span", "", label), e("strong", "", value)));
        const panel = e("div", "portal-panel");
        panel.append(e("h3", "", "Качество по сотрудникам"),
          e("p", "portal-muted",
            "Оценки добровольные. Скорость первого ответа рассчитывается для всех закрытых обращений с ответом."));
        if (!data.staff.length) panel.append(info("Оценок пока нет", "После закрытия тикетов участники могут поставить оценку в ЛС."));
        for (const member of data.staff) {
          const row = e("div", "portal-quality-row");
          row.append(e("strong", "", member.name), e("span", "",
            "Закрыто: " + member.closed_tickets + " · Оценок: " + member.ratings),
            pill(member.average_rating === null ? "Нет оценок" : "★ " + member.average_rating, "success"),
            e("span", "portal-muted", "Первый ответ: " +
              (member.avg_first_response_seconds === null ? "—"
                : Math.round(member.avg_first_response_seconds / 60) + " мин")));
          panel.append(row);
        }
        body.replaceChildren(cards, panel);
      } catch (error) {
        body.replaceChildren(info("Статистика недоступна", error.message));
      }
    }
    periods.addEventListener("change", () => {void update();});
    await update();wrap.append(toolbar,body);return wrap;
  }
  async function noticePage() {
    const wrap = e("div", "portal-stack");
    const head = e("div", "portal-toolbar");
    const reload = button("Обновить", () => {void render("Уведомления");});
    const markAll = button("Прочитать все", async () => {
      try {
        await api("/api/portal/notifications/all/read", {method:"POST", body:{}});
        await refreshNoticeBadge();
        await render("Уведомления");
      } catch (error) {window.portalToast(error.message, true);}
    }, "primary");
    head.append(e("span", "portal-muted", "Уведомления доступны только вашей Discord-учётной записи."),
                reload,markAll);
    wrap.append(head);
    const result = await api("/api/portal/notifications");
    if (result.requires_discord) {
      wrap.append(info("Нужен вход через Discord", "Локальный пароль используется только для конфигурации."));
      markAll.disabled = true;return wrap;
    }
    if (!result.items.length) wrap.append(info("Всё спокойно", "Новых уведомлений нет."));
    for (const notice of result.items) {
      const row = e("div", "portal-notice" + (notice.read ? "" : " unread"));
      const main = e("div", "portal-notice-content");
      main.append(e("strong", "", notice.title),
        e("p", "", notice.description + " · #" + notice.object_id),
        e("span", "portal-meta", format(notice.created_at)));
      const actions = e("div", "portal-actions");
      if (!notice.read) actions.append(button("Прочитано", async () => {
        try {
          await api("/api/portal/notifications/" + notice.id + "/read", {
            method:"POST", body:{}});
          await refreshNoticeBadge();
          await render("Уведомления");
        } catch (error) {window.portalToast(error.message, true);}
      }));
      actions.append(button("К разделу", () => {void window.portalNavigate(notice.section);}, "primary"));
      row.append(main, actions);wrap.append(row);
    }
    return wrap;
  }
  async function refreshNoticeBadge() {
    if (document.getElementById("shell").hidden) return;
    try {
      const result = await api("/api/portal/notifications");
      const badge = document.getElementById("notification-count");
      const unread = Number(result.unread || 0);
      badge.hidden = unread === 0;
      badge.textContent = unread > 99 ? "99+" : String(unread);
      document.getElementById("notice-button").setAttribute(
        "aria-label", "Уведомления: " + unread + " непрочитанных");
    } catch {
      // Потеря соединения не должна сбрасывать счётчик как будто всё прочитано.
    }
  }
  function startNotices() {
    if (noticeTimer) return;
    void refreshNoticeBadge();
    noticeTimer = setInterval(() => {void refreshNoticeBadge();}, 30000);
  }
  function stopNotices() {
    if (noticeTimer) clearInterval(noticeTimer);
    noticeTimer = null;stopLive();
  }
  function cardList(items, renderer, emptyText) {
    const list = e("div", "portal-list");
    if (!items.length) return info("Пока пусто", emptyText);
    for (const row of items) list.append(renderer(row));
    return list;
  }
  function rowTitle(title, meta) {
    return append(e("div", "portal-list-heading"),
      e("strong", "", title), e("span", "portal-meta", meta));
  }
  async function applications() {
    const box = e("div", "portal-stack");
    const toolbar = e("div", "portal-toolbar ob-queue-toolbar");
    const filters = e("div", "ob-filter-pair");
    const caption = e("label", "sr-only", "Статус заявок");
    const select = e("select", "portal-select");
    select.setAttribute("aria-label", "Статус заявок");
    for (const [status, label] of [
      ["open", "На рассмотрении"], ["approved", "Одобренные"],
      ["rejected", "Отклонённые"], ["all", "Все"]
    ]) {
      const item = e("option", "", label);
      item.value = status;
      select.append(item);
    }
    const query = e("input", "portal-input ob-queue-search");
    query.type = "search";
    query.placeholder = "Найти заявку по имени, Discord или номеру...";
    query.maxLength = 80;
    query.setAttribute("aria-label", "Поиск по всем заявкам");
    const reload = button("Обновить", () => { void populate(); }, "secondary");
    filters.append(caption, select);
    toolbar.append(filters, query, reload);
    const summary = e("div", "ob-queue-summary");
    summary.setAttribute("role", "status");
    summary.setAttribute("aria-live", "polite");
    const body = e("div", "ob-queue-items");
    const pager = e("div", "ob-pagination");
    const previous = button("← Назад", () => { page -= 1; void populate(); });
    const pageLabel = e("span", "portal-muted", "Страница 1");
    const following = button("Далее →", () => { page += 1; void populate(); });
    pager.append(previous, pageLabel, following);
    box.append(toolbar, summary, body, pager);
    let rows = [];
    let request = 0;
    let searchTimer = null;
    let page = 1;
    let total = 0;
    let hasNext = false;
    function updatePager() {
      pager.hidden = total <= 100 && page === 1;
      pageLabel.textContent = "Страница " + page + " · всего " + total;
      previous.disabled = page <= 1 || body.getAttribute("aria-busy") === "true";
      following.disabled = !hasNext || body.getAttribute("aria-busy") === "true";
    }
    const statusLabel = {
      pending: "Новая", in_review: "На рассмотрении", needs_info: "Уточнение",
      action_failed: "Ошибка решения", approving: "Одобрение",
      rejecting: "Отклонение", approved: "Одобрена", rejected: "Отклонена"
    };
    function paint() {
      summary.textContent = "Найдено " + total + " заявок · показано " + rows.length
        + " на странице " + page + " · Поиск по всей базе";
      updatePager();
      body.replaceChildren(cardList(rows, (item) => {
        const row = e("article", "portal-list-row ob-queue-row");
        const title = rowTitle("#" + item.id + " · " + (item.name || "Без имени"),
          (item.username || "Discord-пользователь") + " · " + format(item.created_at));
        row.append(title);
        row.append(pill(statusLabel[item.status] || item.status,
          item.status === "approved" ? "success" :
          item.status === "action_failed" ? "warning" : "neutral"));
        row.append(button("Открыть заявку", () => { void applicationDetails(item.id); }, "secondary"));
        return row;
      }, query.value.trim() ? "Заявок по вашему запросу нет." : "Заявок в этой категории нет."));
    }
    async function populate() {
      const token = ++request;
      reload.disabled = true;
      select.disabled = true;
      body.setAttribute("aria-busy", "true");
      updatePager();
      body.replaceChildren(e("div", "portal-loader", "Загружаем заявки..."));
      try {
        const result = await api("/api/portal/applications?status="
          + encodeURIComponent(select.value) + "&page=" + page
          + "&q=" + encodeURIComponent(query.value.trim()));
        if (token !== request || !box.isConnected && token > 1) return;
        rows = result.items || [];
        total = Number(result.total ?? rows.length);
        hasNext = Boolean(result.has_next);
        paint();
      } catch (error) {
        if (token !== request) return;
        const failed = info("Не удалось загрузить заявки", error.message);
        failed.append(button("Повторить", () => { void populate(); }, "secondary"));
        body.replaceChildren(failed);
        summary.textContent = "Данные недоступны · Проверьте соединение";
      } finally {
        if (token === request) {
          body.removeAttribute("aria-busy");
          reload.disabled = false;
          select.disabled = false;
          updatePager();
        }
      }
    }
    select.addEventListener("change", () => { page = 1; void populate(); });
    query.addEventListener("input", () => {
      clearTimeout(searchTimer);
      request += 1; // Ignore any in-flight response for the previous query.
      page = 1;
      searchTimer = setTimeout(() => { void populate(); }, 320);
    });
    await populate();
    return box;
  }
  async function confirmAction(title, description, positive = "Подтвердить") {
    return new Promise((resolve) => {
      const overlay = e("div", "portal-modal-overlay");
      const dialog = e("div", "portal-modal");
      dialog.setAttribute("role", "alertdialog");
      dialog.setAttribute("aria-modal", "true");
      const footer = e("div", "portal-actions");
      let cleanup = () => overlay.remove();
      function done(ok) { cleanup(); resolve(ok); }
      footer.append(button("Отмена", () => done(false)),
                    button(positive, () => done(true), "danger"));
      dialog.append(e("h3", "", title), e("p", "", description), footer);
      overlay.append(dialog);
      cleanup = window.ObsidianDialog.mount(
        overlay, dialog, () => done(false), dialog.querySelector("button"));
    });
  }
  async function inputDialog(title, description, placeholder, minimum = 3) {
    return new Promise((resolve) => {
      const overlay = e("div", "portal-modal-overlay");
      const dialog = e("div", "portal-modal");
      dialog.setAttribute("role", "dialog");
      dialog.setAttribute("aria-modal", "true");
      const field = e("textarea", "portal-textarea");
      field.placeholder = placeholder;
      field.maxLength = 500;
      field.rows = 4;
      const errors = e("div", "portal-field-error");
      const footer = e("div", "portal-actions");
      let cleanup = () => overlay.remove();
      function done(value) { cleanup(); resolve(value); }
      footer.append(button("Отмена", () => done(null)));
      footer.append(button("Подтвердить", () => {
        const value = field.value.trim();
        if (value.length < minimum) {
          errors.textContent = "Нужно не менее " + minimum + " символов.";
          return;
        }
        done(value);
      }, "primary"));
      dialog.append(e("h3", "", title), e("p", "", description), field, errors, footer);
      overlay.append(dialog);
      cleanup = window.ObsidianDialog.mount(overlay, dialog, () => done(null), field);
    });
  }
  async function chooseStaff() {
    const data = await api("/api/portal/tickets/staff");
    if (!data.items.length) {
      window.portalToast("Сотрудники не найдены.", true);
      return null;
    }
    return new Promise((resolve) => {
      const overlay = e("div", "portal-modal-overlay");
      const dialog = e("div", "portal-modal");
      dialog.setAttribute("role", "dialog");
      dialog.setAttribute("aria-modal", "true");
      const select = e("select", "portal-select portal-full");
      for (const item of data.items) {
        const choice = e("option", "", item.name + " · " + (roleLabels[item.role] || item.role));
        choice.value = item.id;select.append(choice);
      }
      const footer = e("div", "portal-actions");
      let cleanup = () => overlay.remove();
      function done(result) {cleanup();resolve(result);}
      footer.append(button("Отмена", () => done(null)));
      footer.append(button("Передать", () => done(select.value), "primary"));
      dialog.append(e("h3", "", "Передать обращение"),
        e("p", "", "Выберите сотрудника с действующей ролью NewSqad."),select,footer);
      overlay.append(dialog);
      cleanup = window.ObsidianDialog.mount(overlay, dialog, () => done(null), select);
    });
  }
  async function applicationDetails(id) {
    const box = root();
    box.replaceChildren(info("Заявка #" + id, "Загружаем карточку..."));
    try {
      const data = await api("/api/portal/applications/" + id);
      const item = data.application;
      const wrap = e("div", "portal-stack");
      wrap.append(section("Заявка #" + id, "Информация кандидата и история изменений."));
      const details = e("div", "portal-detail");
      for (const [key, value] of [
        ["Имя", item.name], ["Discord", item.username], ["ID", item.user_id],
        ["Возраст", item.age], ["Пол", item.gender], ["Кто пригласил", item.inviter],
        ["Статус", item.status], ["Ответственный", item.reviewer_id || "Не назначен"],
        ["Подана", format(item.created_at)], ["Причина решения", item.decision_reason || "—"],
        ["Последняя ошибка", item.error || "—"],
      ]) {
        details.append(append(e("div", "portal-detail-field"),
          e("span", "", key), e("strong", "", value)));
      }
      wrap.append(details);
      const actions = e("div", "portal-actions");
      async function submit(action, reason = "") {
        try {
          const answer = await api("/api/portal/applications/" + id + "/action",
            {method: "POST", body: {action, reason}});
          window.portalToast(answer.message);
          await applicationDetails(id);
        } catch (error) { window.portalToast(error.message, true); }
      }
      actions.append(button("Назад", () => { void render("Заявки"); }));
      if (["pending", "in_review", "needs_info"].includes(item.status)) {
        actions.append(button("Взять в работу", () => { void submit("claim"); }));
        actions.append(button("Передать сотруднику", async () => {
          const userId = await chooseStaff();
          if (userId) {
            try {
              const result = await api("/api/portal/applications/" + id + "/action",
                {method: "POST", body: {action: "transfer", user_id: userId}});
              window.portalToast(result.message);
              await applicationDetails(id);
            } catch (error) {window.portalToast(error.message, true);}
          }
        }));
        actions.append(button("Одобрить", async () => {
          if (await confirmAction("Одобрить заявку?", "Кандидат получит роль участника и доступ к серверу.", "Одобрить")) {
            await submit("approve");
          }
        }, "success"));
        actions.append(button("Отклонить", async () => {
          const reason = await inputDialog("Причина отказа", "Кандидат получит уведомление о решении.", "Подробно укажите причину...", 3);
          if (reason && await confirmAction("Отклонить заявку?", "Кандидат будет исключён с сервера.", "Отклонить")) {
            await submit("reject", reason);
          }
        }, "danger"));
        actions.append(button("Уточнить", async () => {
          const reason = await inputDialog("Уточнение для кандидата", "Ответ сохранится в приватной истории заявки.", "Какую информацию необходимо уточнить?", 5);
          if (reason) await submit("request_info", reason);
        }));
      }
      if (["action_failed", "approving", "rejecting"].includes(item.status)) {
        actions.append(button("Повторить операцию", () => { void submit("retry"); }));
      }
      wrap.append(actions);
      wrap.append(e("h3", "portal-subheading", "История заявки"));
      wrap.append(cardList(data.history, (event) => {
        const row = e("div", "portal-history-row");
        row.append(rowTitle(event.previous + " → " + event.status, format(event.at)));
        row.append(e("p", "", event.reason));
        return row;
      }, "История пока пустая."));
      box.replaceChildren(wrap);
    } catch (error) {
      box.replaceChildren(info("Ошибка загрузки", error.message),
                          button("К списку", () => { void render("Заявки"); }));
    }
  }
  let ticketRefresh = null;
  let activeReply = null;
  function hasDraft() { return Boolean(activeReply?.isConnected && activeReply.value.trim()); }
  function stopTicketRefresh() {
    if (ticketRefresh) { clearInterval(ticketRefresh); ticketRefresh = null; }
  }
  async function tickets() {
    stopTicketRefresh();
    const box = e("div", "portal-stack");
    const toolbar = e("div", "portal-toolbar ob-queue-toolbar");
    const tabs = e("div", "ob-segmented");
    tabs.setAttribute("role", "group");
    tabs.setAttribute("aria-label", "Фильтр тикетов");
    const currentUser = window.portalData()?.viewer?.id || null;
    let activeFilter = "all";
    let rows = [];
    let page = 1;
    let total = 0;
    let hasNext = false;
    let request = 0;
    let searchTimer = null;
    const summary = e("div", "ob-queue-summary");
    summary.setAttribute("role", "status");
    summary.setAttribute("aria-live", "polite");
    const search = e("input", "portal-input ob-queue-search");
    search.type = "search";
    search.setAttribute("aria-label", "Поиск по всей очереди тикетов");
    search.placeholder = "Поиск по теме или номеру...";
    search.maxLength = 80;
    const body = e("div", "ob-queue-items");
    const reload = button("Обновить", () => { void populate(); }, "secondary");
    const filters = [
      ["all", "Все"], ["unassigned", "Без ответственного"],
      ["mine", "Мои"], ["assigned", "В работе"]
    ];
    function paint() {
      summary.textContent = "Найдено " + total + " тикетов · показано " + rows.length
        + " на странице " + page + " · Все фильтры и поиск работают по всей очереди";
      updatePager();
      body.replaceChildren(cardList(rows, (item) => {
        const row = e("article", "portal-list-row ob-queue-row");
        row.append(rowTitle("#" + item.id + " · " + (item.subject || "Без темы"),
          "Обновлён: " + format(item.updated_at || item.opened_at)));
        const status = item.status === "closing" ? "Закрывается" :
          item.status === "creating" ? "Создаётся" :
          item.assignee_id ? "В работе" : "Без ответственного";
        row.append(pill(status, item.status !== "open" ? "warning" :
          item.assignee_id ? "success" : "neutral"));
        if (item.status === "open") {
          row.append(button("Открыть чат", () => { void ticketChat(item.id); }, "secondary"));
        } else {
          row.append(e("span", "portal-muted", "Действия временно недоступны"));
        }
        return row;
      }, search.value.trim() || activeFilter !== "all"
        ? "По выбранным фильтрам обращений нет." : "Активных тикетов нет."));
    }
    for (const [value, label] of filters) {
      const control = button(label, () => {
        activeFilter = value;
        for (const tab of tabs.querySelectorAll("button")) {
          tab.setAttribute("aria-pressed", String(tab.dataset.filter === activeFilter));
        }
        page = 1;
        void populate();
      });
      control.dataset.filter = value;
      control.setAttribute("aria-pressed", String(value === activeFilter));
      if (value === "mine" && !currentUser) {
        control.disabled = true;
        control.title = "Нужен вход через Discord";
      }
      tabs.append(control);
    }
    toolbar.append(tabs, search, reload);
    const pager = e("div", "ob-pagination");
    const previous = button("← Назад", () => { page -= 1; void populate(); });
    const pageLabel = e("span", "portal-muted", "Страница 1");
    const following = button("Далее →", () => { page += 1; void populate(); });
    pager.append(previous, pageLabel, following);
    box.append(toolbar, summary, body, pager);
    function updatePager() {
      pager.hidden = total <= 100 && page === 1;
      pageLabel.textContent = "Страница " + page + " · всего " + total;
      previous.disabled = page <= 1 || body.getAttribute("aria-busy") === "true";
      following.disabled = !hasNext || body.getAttribute("aria-busy") === "true";
    }
    async function populate() {
      const token = ++request;
      reload.disabled = true;
      body.setAttribute("aria-busy", "true");
      updatePager();
      body.replaceChildren(e("div", "portal-loader", "Загружаем обращения..."));
      try {
        const data = await api("/api/portal/tickets?page=" + page
          + "&assignment=" + encodeURIComponent(activeFilter)
          + "&q=" + encodeURIComponent(search.value.trim()));
        if (token !== request) return;
        rows = data.items || [];
        total = Number(data.total ?? rows.length);
        hasNext = Boolean(data.has_next);
        paint();
      } catch (error) {
        if (token !== request) return;
        const failed = info("Не удалось получить тикеты", error.message);
        failed.append(button("Повторить", () => { void populate(); }));
        body.replaceChildren(failed);
        summary.textContent = "Данные недоступны · Проверьте соединение";
      } finally {
        if (token === request) {
          body.removeAttribute("aria-busy");
          reload.disabled = false;
          updatePager();
        }
      }
    }
    search.addEventListener("input", () => {
      clearTimeout(searchTimer);
      request += 1; // Discard stale search results while debounce is pending.
      page = 1;
      searchTimer = setTimeout(() => { void populate(); }, 320);
    });
    await populate();
    return box;
  }
  async function ticketChat(id) {
    stopTicketRefresh();
    const host = root();
    host.replaceChildren(info("Тикет #" + id, "Получаем приватную переписку напрямую из Discord..."));
    const wrap = e("div", "portal-stack");
    const header = section("Тикет #" + id, "Переписка отображается из Discord и не архивируется на сайте.");
    const actions = e("div", "portal-actions");
    const messages = e("div", "portal-chat");
    messages.setAttribute("role", "log");
    messages.setAttribute("aria-label", "Сообщения тикета");
    const form = e("form", "portal-compose");
    const textarea = e("textarea", "portal-textarea");
    textarea.placeholder = "Написать ответ участнику...";
    textarea.setAttribute("aria-label", "Ответ участнику в Discord");
    activeReply = textarea;
    textarea.maxLength = 1500;
    textarea.rows = 3;
    const send = button("Отправить в Discord", () => {}, "primary");
    send.type = "submit";
    send.disabled = true;
    textarea.addEventListener("input", () => {
      send.disabled = !textarea.value.trim() || actionPending;
    });
    form.append(textarea, send);
    const participants = e("div", "portal-participants");
    const foot = e("div", "portal-muted", "Сообщения отправляются от имени бота с подписью сотрудника. Переписка не хранится в SQLite.");
    const syncState = e("div", "portal-muted ob-chat-sync", "Загружаем переписку...");
    syncState.setAttribute("role", "status");
    syncState.setAttribute("aria-live", "polite");
    wrap.append(header, actions, participants, messages, form, syncState, foot);
    let lastMessages = null;
    let refreshPending = false;
    let actionPending = false;
    async function refreshMessages() {
      if (window.portalGroup() !== "Тикеты" || !messages.isConnected || document.hidden || refreshPending) return;
      refreshPending = true;
      try {
        const data = await api("/api/portal/tickets/" + id);
        if (window.portalGroup() !== "Тикеты" || !messages.isConnected) return;
        const ticket = data.ticket;
        participants.replaceChildren(e("span", "portal-muted", "Дополнительные участники: "));
        for (const uid of data.participants || []) participants.append(pill(uid));
        header.querySelector("p").textContent = ticket.subject + " · " + ticket.status
          + " · ответственный: " + (ticket.assignee_id || "не назначен");
        const signature = data.messages.map((m) => m.id).join("|");
        if (signature !== lastMessages) {
          const nearBottom = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 130;
          messages.replaceChildren();
          for (const message of data.messages) {
            const row = e("div", "portal-message" + (message.bot ? " from-bot" : ""));
            const author = append(e("div", "portal-message-head"),
              e("strong", "", message.author), e("span", "", format(message.at)));
            row.append(author, e("p", "", message.text || "Сообщение без доступного текста"));
            for (const att of message.attachments) {
              if (!/^https:\/\//.test(att.url)) continue;
              const a = e("a", "portal-attachment", att.name);
              a.href = att.url; a.target = "_blank"; a.rel = "noopener noreferrer";
              row.append(a);
            }
            messages.append(row);
          }
          if (!data.messages.length) messages.append(info("Сообщений пока нет",
            "Ответы появятся здесь после получения из Discord."));
          if (nearBottom) messages.scrollTop = messages.scrollHeight;
          lastMessages = signature;
        }
        syncState.textContent = "Обновлено " + new Date().toLocaleTimeString("ru-RU",
          {hour: "2-digit", minute: "2-digit"});
        syncState.classList.remove("ob-sync-error");
      } catch (error) {
        if (!messages.isConnected || window.portalGroup() !== "Тикеты") return;
        syncState.textContent = "Связь с Discord прервана: " + error.message
          + ". Сообщения на экране могут быть неактуальны.";
        syncState.classList.add("ob-sync-error");
        if (lastMessages === null) messages.replaceChildren(
          info("Переписка временно недоступна", "Повторная проверка произойдёт автоматически."));
      } finally {
        refreshPending = false;
      }
    }
    async function action(kind, additional = {}) {
      if (actionPending) return false;
      actionPending = true;
      for (const actionButton of actions.querySelectorAll("button")) actionButton.disabled = true;
      send.disabled = true;
      try {
        const data = await api("/api/portal/tickets/" + id + "/action",
          {method: "POST", body: {action: kind, ...additional}});
        window.portalToast(data.message);
        if (kind === "close") { stopTicketRefresh(); await render("Тикеты"); return true; }
        await refreshMessages();
        return true;
      } catch (error) { window.portalToast(error.message, true); return false; }
      finally {
        actionPending = false;
        for (const actionButton of actions.querySelectorAll("button")) actionButton.disabled = false;
        send.disabled = !textarea.value.trim();
      }
    }
    actions.append(button("← Все обращения", async () => {
      if (hasDraft() && !await confirmAction("Покинуть переписку?",
        "Написанный ответ не будет сохранён. Вы действительно хотите выйти?", "Выйти")) return;
      stopTicketRefresh(); void render("Тикеты");
    }));
    actions.append(button("Взять в работу", () => { void action("claim"); }, "success"));
    actions.append(button("Передать", async () => {
      const staffId = await chooseStaff();
      if (staffId) await action("transfer", {user_id: staffId});
    }));
    actions.append(button("Пригласить участника", async () => {
      const userId = await inputDialog("Добавить участника",
        "Укажи Discord ID одобренного участника. Канал станет доступен ему в Discord.",
        "Discord ID (цифры)", 15);
      if (userId) await action("add_member", {user_id: userId});
    }));
    actions.append(button("Удалить участника", async () => {
      const userId = await inputDialog("Убрать участника из тикета",
        "Можно удалить только приглашённого участника, но не автора обращения.",
        "Discord ID (цифры)", 15);
      if (userId && await confirmAction("Закрыть доступ участнику?",
          "Права на просмотр приватного канала будут сняты.", "Убрать"))
        await action("remove_member", {user_id: userId});
    }, "danger"));
    actions.append(button("Закрыть и удалить", async () => {
      if (await confirmAction("Удалить тикет безвозвратно?",
        "Discord-канал и вся переписка будут безвозвратно удалены. Транскрипт не создаётся.",
        "Удалить тикет")) await action("close", {confirm: "DELETE"});
    }, "danger"));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const text = textarea.value.trim();
      if (!text) return;
      const delivered = await action("reply", {text});
      if (delivered && textarea.value.trim() === text) textarea.value = "";
      send.disabled = !textarea.value.trim();
    });
    host.replaceChildren(wrap);
    await refreshMessages();
    if (host.contains(wrap) && window.portalGroup() === "Тикеты") {
      ticketRefresh = setInterval(() => { void refreshMessages(); }, 12000);
    }
  }
  async function staff() {
    const data = await api("/api/portal/staff");
    const box = e("div", "portal-stack");
    if (!data.presence_available) box.append(info("Статусы присутствия недоступны",
      "Чтобы показывать «в сети», нужно включить Presence Intent в Discord Developer Portal и у бота. До этого сотрудники отображаются со статусом «неизвестно»."));
    box.append(cardList(data.items, (item) => {
      const row = e("div", "portal-list-row");
      const avatar = e("img", "portal-person-avatar");avatar.src=item.avatar_url;avatar.alt="";
      row.append(avatar, rowTitle(item.name, item.id),
                 pill(roleLabels[item.role] || item.role),
                 pill(item.status === "online" ? "Онлайн" : item.status === "unknown" ? "Неизвестно" : item.status,
                      item.status === "online" ? "success" : "neutral"));
      return row;
    }, "Сотрудники пока не найдены в кэше участников."));
    return box;
  }
  async function members() {
    const box = e("div", "portal-stack");
    const form = e("form", "portal-toolbar");
    const search = e("input", "portal-input");search.placeholder="Поиск по имени или Discord ID";
    form.append(search, button("Найти", () => {}, "primary"));
    const body = e("div");
    async function query() {
      const data = await api("/api/portal/members?q=" + encodeURIComponent(search.value));
      body.replaceChildren(cardList(data.items, (item) => {
        const row = e("div", "portal-list-row");
        const avatar = e("img", "portal-person-avatar");avatar.src=item.avatar_url;avatar.alt="";
        row.append(avatar, rowTitle(item.name, item.id), e("span", "portal-meta", "На сервере: " + format(item.joined_at)));
        row.append(button("Модерация", () => {
          chosenModerTarget = item.id;
          void window.portalNavigate("Модерация");
        }));
        return row;
      }, "Участники не найдены."));
      body.prepend(e("p", "portal-muted", "В кэше: " + data.cached_members + ", участников сервера: " + (data.server_member_count || "—")));
    }
    form.addEventListener("submit", (evt) => { evt.preventDefault();void query(); });
    await query();
    box.append(form, body);
    return box;
  }
  let chosenModerTarget = "";
  function picker(options, className = "portal-select") {
    const select = e("select", className);
    for (const [value, label] of options) {
      const item = e("option", "", label);item.value = value;select.append(item);
    }
    return select;
  }
  async function moderation() {
    const wrapper = e("div", "portal-stack");
    const create = e("div", "portal-panel");
    create.append(e("h3", "", "Новое модерационное дело"));
    const form = e("form", "portal-action-form");
    const user = e("input", "portal-input");
    user.placeholder = "Discord ID участника";user.value = chosenModerTarget;
    user.maxLength = 20;user.required = true;user.inputMode = "numeric";
    chosenModerTarget = "";
    const kind = picker([["warn", "Предупреждение"], ["mute", "Тайм-аут"],
      ["kick", "Исключение"], ["ban", "Блокировка"]]);
    const reason = e("textarea", "portal-textarea");
    reason.placeholder = "Причина (не менее 3 символов)";reason.maxLength = 400;
    reason.required = true;reason.rows = 2;
    const duration = e("input", "portal-input");
    duration.placeholder = "Срок: 10m, 2h, 7d, 2w или per";
    duration.maxLength = 25;duration.hidden = true;
    kind.addEventListener("change", () => {
      duration.hidden = !["ban", "mute"].includes(kind.value);
      duration.required = !duration.hidden;
      duration.value = "";
      if (kind.value === "ban") duration.placeholder = "Срок: 1d, 2w или per";
      if (kind.value === "mute") duration.placeholder = "Срок: 10m, 2h, 7d (максимум 28d)";
    });
    form.append(user, kind, duration, reason);
    const issue = button("Выдать наказание", () => {}, "danger");
    issue.type = "submit";form.append(issue);
    form.addEventListener("submit", async (evt) => {
      evt.preventDefault();
      if (!/^[0-9]{15,20}$/.test(user.value.trim()) || reason.value.trim().length < 3) {
        window.portalToast("Укажи полный Discord ID и причину.", true);return;
      }
      const question = "Вид: " + kind.options[kind.selectedIndex].text + " · участник: "
        + user.value + (duration.hidden ? "" : " · срок: " + duration.value);
      if (!await confirmAction("Подтвердить наказание?", question, "Выполнить")) return;
      issue.disabled = true;
      try {
        const data = await api("/api/portal/moderation",
          {method: "POST", body: {kind: kind.value, user_id: user.value.trim(),
                                 reason: reason.value, duration: duration.value}});
        window.portalToast(data.message);
        reason.value = "";
        await update();
      } catch (error) {window.portalToast(error.message, true);}
      finally {issue.disabled = false;}
    });
    create.append(form);
    wrapper.append(create);
    const filters = e("div", "portal-toolbar");
    const filterKind = picker([["all", "Все типы"], ["ban", "Баны"],
      ["mute", "Тайм-ауты"], ["warn", "Предупреждения"], ["kick", "Исключения"]]);
    const status = picker([["all", "Любой статус"], ["active", "Активные"], ["closed", "Завершённые"]]);
    const searchUser = e("input", "portal-input");searchUser.placeholder="Discord ID участника";
    searchUser.inputMode="numeric";searchUser.maxLength=20;
    const number = e("input", "portal-input");number.placeholder="Номер дела";
    number.inputMode="numeric";number.maxLength=10;
    const find = button("Найти дела", () => {void update();},"primary");
    filters.append(filterKind,status,searchUser,number,find);
    const results = e("div");
    async function update() {
      const query = new URLSearchParams({kind:filterKind.value,status:status.value,
        user:searchUser.value.trim(),number:number.value.trim()});
      try {
        const data = await api("/api/portal/moderation?" + query);
        results.replaceChildren(cardList(data.items, (item) => {
          const row = e("div","portal-list-row");
          row.append(rowTitle("Дело #" + item.id + " · " + item.kind,
            "Участник " + item.user_id + " · " + format(item.created_at)));
          row.append(pill(item.status, item.status === "active" ? "warning" : "neutral"));
          row.append(e("span","portal-ellipsis",item.reason));
          row.append(button("Открыть",() => {void moderationDetail(item.id);}));
          return row;
        }, "Нет дел по выбранным фильтрам."));
      } catch (error) {results.replaceChildren(info("Ошибка поиска",error.message));}
    }
    for (const control of [filterKind,status]) control.addEventListener("change",() => {void update();});
    for (const control of [searchUser,number]) control.addEventListener("keydown",evt => {
      if (evt.key === "Enter") {evt.preventDefault();void update();}
    });
    wrapper.append(section("История модерации", "Фильтры по участнику, номеру дела, типу и статусу."),
                   filters,results);
    await update();return wrapper;
  }
  async function moderationDetail(id) {
    root().replaceChildren(info("Дело #" + id, "Загружаем историю и состояние Discord..."));
    try {
      const data = await api("/api/portal/moderation/" + id);
      const row = data.case;
      const wrap = e("div","portal-stack");
      wrap.append(section("Дело #" + id, "Карточка модерационного решения и действия."));
      const fields = e("div","portal-detail");
      for (const [key,val] of [
        ["Участник",row.user_id],["Модератор",row.moderator_id],
        ["Тип",row.kind],["Статус",row.status],["Причина",row.reason],
        ["Создано",format(row.created_at)],["До",row.expires_at ? format(row.expires_at):"Бессрочно"],
        ["Закрыто",format(row.completed_at)],["Закрыл",row.closed_by || "—"],
        ["ЛС",row.dm_status],["Ошибка",row.error || "—"],
      ]) fields.append(append(e("div","portal-detail-field"),
                              e("span","",key),e("strong","",val)));
      wrap.append(fields);
      const actions = e("div","portal-actions");
      actions.append(button("← Все дела",() => {void render("Модерация");}));
      async function mutate(action,value,confirm) {
        try {
          const result = await api("/api/portal/moderation/" + id + "/action",
            {method:"POST",body:{action,value,confirm}});
          window.portalToast(result.message);
          await moderationDetail(id);
        } catch(error){window.portalToast(error.message,true);}
      }
      if(row.status==="active"){
        actions.append(button("Изменить причину",async () => {
          const text = await inputDialog("Изменение причины",
            "Изменение будет записано в историю дела и журнал модерации.",
            row.reason,3);
          if(text) await mutate("reason",text,"");
        }));
        if(["ban","mute"].includes(row.kind)){
          actions.append(button("Изменить срок",async () => {
            const value=await inputDialog("Новый срок",
              "Используй 10m, 2h, 7d, 2w. Для бана доступно per.",
              "Например: 7d",2);
            if(value) await mutate("duration",value,"");
          }));
        }
        if(row.kind !== "kick") actions.append(button("Отменить наказание",async()=>{
          const reason=await inputDialog("Причина отмены",
            "Отмена действующего наказания требует обязательной причины.","Причина...",3);
          if(reason && await confirmAction("Точно отменить наказание?",
            "Будет выполнена операция в Discord и сохранена история.","Отменить наказание")){
            await mutate("revoke",reason,"REVOKE");
          }
        },"danger"));
      }
      wrap.append(actions);
      wrap.append(e("h3","portal-subheading","История изменений"));
      wrap.append(cardList(data.history,(step)=>{
        const entry=e("div","portal-history-row");
        entry.append(rowTitle(step.action,format(step.at)));
        entry.append(e("p","","Сотрудник: "+step.actor_id+" · "+(step.details||"Без примечаний")));
        return entry;
      },"Пока нет событий."));
      root().replaceChildren(wrap);
    } catch(error) {
      root().replaceChildren(info("Не удалось открыть дело",error.message),
        button("Назад",()=>{void render("Модерация");}));
    }
  }
  async function statsPage() {
    const box = e("div", "portal-stack analytics-product");
    const toolbar = e("div", "portal-toolbar analytics-toolbar");
    const toolbarText = e("div", "analytics-toolbar-copy");
    toolbarText.append(e("strong", "", "Период анализа"), e("span", "", "Агрегированные показатели сервера"));
    const periods = e("select", "portal-select");
    periods.setAttribute("aria-label", "Период статистики");
    for (const [value, label] of [["7d", "Последние 7 дней"], ["30d", "Последние 30 дней"], ["90d", "Последние 90 дней"]]) {
      const option = e("option", "", label);
      option.value = value;
      periods.append(option);
    }
    toolbar.append(toolbarText, periods);
    const content = e("div", "portal-stack analytics-content");

    function renderTrend(data, metric, destination) {
      const metrics = {
        messages: ["Сообщения", "Количество сообщений", "сообщений"],
        voice: ["Голосовой чат", "Время в голосовых каналах", "минут"],
        joins: ["Вступления", "Новые участники", "вступлений"]
      };
      const rows = data.series || [];
      const values = rows.map(row => Number(row[metric]) || 0);
      const max = Math.max(1, ...values);
      const total = values.reduce((sum, value) => sum + value, 0);
      const visual = e("div", "analytics-visual");
      const top = e("div", "analytics-visual-summary");
      const summary = e("div", "analytics-visual-number");
      summary.append(e("strong", "", total.toLocaleString("ru-RU")),
        e("span", "", metrics[metric][2] + " за период"));
      top.append(summary, e("span", "portal-muted", metrics[metric][1]));
      visual.append(top);

      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 900 240");
      svg.setAttribute("role", "img");
      svg.setAttribute("aria-label", metrics[metric][0] + ": динамика по дням");
      svg.classList.add("analytics-chart");
      const make = (tag, attrs) => {
        const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
        for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
        return node;
      };
      for (let step = 0; step <= 4; step++) {
        const y = 20 + step * 50;
        svg.append(make("line", {x1: 0, x2: 900, y1: y, y2: y, class: "analytics-grid-line"}));
      }
      if (values.length) {
        const points = values.map((value, index) => {
          const x = values.length === 1 ? 450 : index * (900 / (values.length - 1));
          return [x, 220 - (value / max) * 190];
        });
        const coords = points.map(point => point[0].toFixed(2) + "," + point[1].toFixed(2));
        svg.append(make("path", {d: "M0,220 L" + coords.join(" L") + " L900,220 Z", class: "analytics-area"}));
        svg.append(make("polyline", {points: coords.join(" "), class: "analytics-chart-line"}));
        if (points.length <= 14) {
          for (let index = 0; index < points.length; index++) {
            const point = points[index];
            const circle = make("circle", {cx: point[0], cy: point[1], r: 4.2, class: "analytics-chart-point"});
            const label = document.createElementNS("http://www.w3.org/2000/svg", "title");
            label.textContent = rows[index].date + ": " + values[index];
            circle.append(label);
            svg.append(circle);
          }
        }
      } else {
        svg.append(make("line", {x1: 0, x2: 900, y1: 220, y2: 220, class: "analytics-grid-line"}));
      }
      visual.append(svg);
      const axis = e("div", "analytics-axis");
      if (rows.length) {
        const count = Math.min(5, rows.length);
        for (let index = 0; index < count; index++) {
          const at = Math.round(index * (rows.length - 1) / Math.max(1, count - 1));
          axis.append(e("span", "", rows[at].date.slice(5)));
        }
      } else axis.append(e("span", "", "Нет данных за период"));
      visual.append(axis);
      destination.replaceChildren(visual);
    }

    async function show() {
      content.replaceChildren(e("div", "portal-loader", "Загружаем статистику..."));
      try {
        const data = await api("/api/portal/stats?period=" + periods.value);
        if (window.portalGroup() !== "Аналитика") return;
        const labels = {total: "Участников", approved: "Одобрены", candidates: "Кандидаты",
          joins: "Вступления", leaves: "Выходы", approvals: "Принятые заявки", rejections: "Отказы",
          messages: "Сообщения", voice_minutes: "Голос, минуты", active_users: "Активные участники",
          events: "Мероприятия", parties: "Группы", tickets: "Обращения"};
        const groups = [
          ["Участники", "Состав и движение сообщества", ["total", "approved", "candidates", "joins", "leaves"]],
          ["Активность", "Общение и вовлечённость", ["messages", "voice_minutes", "active_users"]],
          ["Работа сервера", "Заявки, события и обращения", ["approvals", "rejections", "events", "parties", "tickets"]]
        ];
        const intro = e("div", "analytics-intro");
        const introCopy = e("div");
        introCopy.append(e("span", "panel-kicker", "ОБЗОР ПОКАЗАТЕЛЕЙ"),
          e("strong", "", "Данные, которые имеют значение"),
          e("p", "", "Сводка за выбранный период без хранения содержимого сообщений."));
        const dates = data.series || [];
        intro.append(introCopy, pill(dates.length ?
          dates[0].date + " — " + dates[dates.length - 1].date : "Нет истории", "neutral"));
        const containers = [intro];
        for (const [title, subtitle, keys] of groups) {
          const section = e("section", "analytics-group");
          const heading = e("div", "analytics-group-head");
          heading.append(e("h3", "", title), e("p", "", subtitle));
          section.append(heading);
          const grid = e("div", "portal-metrics analytics-metrics");
          for (const key of keys) {
            const card = e("div", "portal-metric analytics-metric");
            const value = data.totals[key];
            card.append(e("span", "", labels[key] || key),
              e("strong", "", value == null ? "—" : Number(value).toLocaleString("ru-RU")),
              e("small", "", key === "total" || key === "approved" || key === "candidates"
                ? "Текущее состояние" : "За выбранный период"));
            grid.append(card);
          }
          section.append(grid);
          containers.push(section);
        }

        const graphPanel = e("section", "portal-panel analytics-panel");
        const graphHeader = e("div", "analytics-graph-heading");
        const graphCopy = e("div");
        graphCopy.append(e("span", "panel-kicker", "ДИНАМИКА"), e("h3", "", "Активность по дням"),
          e("p", "portal-muted", "Выбери показатель, чтобы посмотреть изменение за период."));
        const switcher = e("div", "analytics-switcher");
        switcher.setAttribute("role", "group");
        switcher.setAttribute("aria-label", "Показатель диаграммы");
        const chart = e("div", "analytics-chart-wrap");
        const switches = [
          ["messages", "Сообщения"], ["voice", "Голос"], ["joins", "Вступления"]
        ];
        for (const [metric, title] of switches) {
          const control = button(title, () => {
            for (const node of switcher.children) {
              node.classList.toggle("active", node === control);
              node.setAttribute("aria-pressed", String(node === control));
            }
            renderTrend(data, metric, chart);
          }, "secondary");
          control.classList.add("analytics-segment");
          control.classList.toggle("active", metric === "messages");
          control.setAttribute("aria-pressed", String(metric === "messages"));
          switcher.append(control);
        }
        graphHeader.append(graphCopy, switcher);
        graphPanel.append(graphHeader, chart);
        renderTrend(data, "messages", chart);
        containers.splice(1, 0, graphPanel);
        content.replaceChildren(...containers);
      } catch (error) {
        content.replaceChildren(info("Не удалось загрузить статистику", error.message));
      }
    }
    periods.addEventListener("change", () => {void show();});
    box.append(toolbar, content);
    await show();
    return box;
  }
  async function rankings() {
    const box=e("div","portal-stack");
    const toolbar=e("div","portal-toolbar");
    const metric=e("select","portal-select");
    for(const [value,label] of [["xp","XP"],["messages","Сообщения"],["voice","Голос"]]){
      const o=e("option","",label);o.value=value;metric.append(o);
    }
    const period=e("select","portal-select");
    for(const val of ["7d","30d","90d"]){const o=e("option","",val);o.value=val;period.append(o);}
    toolbar.append(metric,period);
    const body=e("div");
    async function show(){
      const d=await api("/api/portal/tops?metric="+metric.value+"&period="+period.value);
      body.replaceChildren(cardList(d.items,(item,i)=>{
        const row=e("div","portal-list-row");
        row.append(rowTitle(item.name,item.user_id),pill(item.value+" "+d.metric));return row;
      },"Нет данных рейтинга или участники скрыли свои показатели."));
    }
    metric.addEventListener("change",()=>{void show();});period.addEventListener("change",()=>{void show();});
    await show();box.append(toolbar,body);return box;
  }
  async function audit() {
    const data = await api("/api/portal/audit");
    const items = Array.isArray(data.items) ? data.items : [];
    const box = e("div", "portal-stack audit-product");
    const intro = e("div", "audit-intro");
    const introCopy = e("div");
    introCopy.append(e("span", "panel-kicker", "ИСТОРИЯ ДЕЙСТВИЙ"),
      e("strong", "", "Каждое событие — на своём месте"),
      e("p", "", "Кто выполнил действие, что изменилось и когда это произошло."));
    intro.append(introCopy, pill("Последние " + items.length + " записей"));
    box.append(intro);
    const toolbar = e("div", "portal-toolbar audit-toolbar");
    const search = e("input", "portal-input audit-search");
    search.type = "search";
    search.placeholder = "Поиск по событию, сотруднику или объекту";
    search.setAttribute("aria-label", "Поиск в журнале аудита");
    const filter = e("select", "portal-select");
    filter.setAttribute("aria-label", "Категория события");
    const options = [
      ["all", "Все события"], ["settings", "Настройки"], ["moderation", "Модерация"],
      ["communication", "Объявления"], ["roles", "Роли"], ["economy", "Экономика"],
      ["community", "Сообщество"], ["support", "Поддержка"], ["system", "Система"]
    ];
    for (const [value, label] of options) {
      const opt = e("option", "", label);
      opt.value = value;
      filter.append(opt);
    }
    const count = e("span", "portal-muted audit-count", "");
    toolbar.append(search, filter, count);
    box.append(toolbar);
    const timeline = e("div", "audit-timeline");
    box.append(timeline);

    function draw() {
      const term = search.value.trim().toLocaleLowerCase("ru-RU");
      const found = items.filter(item => {
        if (filter.value !== "all" && (item.category || "system") !== filter.value) return false;
        const searchable = [item.title, item.action, item.actor_name, item.summary,
          ...(item.fields || []).map(field => field.label + " " + field.value)].join(" ").toLocaleLowerCase("ru-RU");
        return !term || searchable.includes(term);
      });
      count.textContent = found.length + " из " + items.length;
      timeline.replaceChildren();
      if (!found.length) {
        timeline.append(info("Записей не найдено", "Измени поисковый запрос или категорию."));
        return;
      }
      let currentDay = "";
      for (const item of found) {
        const date = new Date(item.at * 1000);
        const day = date.toLocaleDateString("ru-RU", {day: "numeric", month: "long", year: "numeric"});
        if (day !== currentDay) {
          timeline.append(e("div", "audit-date-label", day));
          currentDay = day;
        }
        const card = e("article", "audit-event");
        const marker = e("span", "audit-marker audit-marker-" + (item.category || "system"));
        marker.setAttribute("aria-hidden", "true");
        const main = e("div", "audit-event-content");
        const top = e("div", "audit-event-top");
        const heading = e("div", "audit-event-title");
        heading.append(e("strong", "", item.title || item.action),
          e("span", "", item.summary || "Действие зарегистрировано"));
        top.append(heading, e("time", "audit-time", date.toLocaleTimeString("ru-RU", {hour: "2-digit", minute: "2-digit"})));
        const meta = e("div", "audit-event-meta");
        meta.append(e("span", "audit-actor-avatar", (item.actor_name || "С").slice(0, 1).toUpperCase()),
          e("strong", "", item.actor_name || "Система"),
          pill(options.find(([key]) => key === item.category)?.[1] || "Система"));
        main.append(top, meta);
        if (item.fields && item.fields.length) {
          const detail = e("details", "audit-expand");
          const toggle = e("summary", "", "Детали события");
          const fields = e("dl", "audit-fields");
          for (const field of item.fields) {
            const itemRow = e("div", "audit-field");
            itemRow.append(e("dt", "", field.label), e("dd", "", field.value));
            fields.append(itemRow);
          }
          detail.append(toggle, fields);
          main.append(detail);
        }
        card.append(marker, main);
        timeline.append(card);
      }
    }
    search.addEventListener("input", draw);
    filter.addEventListener("change", draw);
    draw();
    return box;
  }
  async function activity(){
    const data=await api("/api/portal/activity");
    return cardList(data.items,(item)=>{
      const row=e("div","portal-history-row");
      row.append(rowTitle(item.name,"Discord ID "+item.user_id));
      row.append(e("p","","Заявки: "+item.applications+" · Дела: "+item.moderation_cases
        +" · Системные действия: "+item.audit_actions+" · Активные тикеты: "+item.active_tickets));
      return row;
    },"Пока нет активности администрации.");
  }
  const loaders={"Обзор":overview,"Уведомления":noticePage,"Качество поддержки":supportQuality,
    "Заявки":applications,"Тикеты":tickets,
    "Сотрудники":staff,"Список участников":members,"Модерация":moderation,"Аналитика":statsPage,
    "Рейтинги":rankings,"Активность":activity,"Аудит":audit};
  async function render(group) {
    if (group !== "Тикеты") stopTicketRefresh();
    if (group !== "Обзор") stopLive();
    else startLive();
    if (window.StaffCenter?.loaders[group]) {
      descriptions[group] = window.StaffCenter.descriptions[group];
      await load(group, window.StaffCenter.loaders[group]);
    } else if (window.Management?.loaders[group]) {
      descriptions[group] = window.Management.descriptions[group];
      await load(group, window.Management.loaders[group]);
    } else if (loaders[group]) await load(group, loaders[group]);
  }
  window.Portal={menu,roleLabels,icon,descriptions,render,stopTicketRefresh,
    mountOwnerOverview,stopLive,startNotices,stopNotices,refreshNoticeBadge,hasDraft};
})();
