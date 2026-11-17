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
  };
  const menu = {
    support: ["Обзор", "Заявки", "Тикеты"],
    moder: ["Обзор", "Заявки", "Тикеты", "Сотрудники", "Список участников", "Модерация"],
    admin: ["Обзор", "Заявки", "Тикеты", "Сотрудники", "Список участников", "Модерация",
            "Аналитика", "Рейтинги", "Активность", "Аудит"],
    owner: ["Обзор", "Заявки", "Тикеты", "Сотрудники", "Список участников", "Модерация",
            "Аналитика", "Рейтинги", "Активность", "Аудит"],
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
  async function load(group, loader) {
    const host = root();
    host.replaceChildren(section(group, descriptions[group] || "Данные сообщества."),
                         e("div", "portal-loader", "Получаем данные из Discord..."));
    try {
      const node = await loader();
      if (window.portalGroup() !== group) return;
      host.replaceChildren(section(group, descriptions[group]), node);
    } catch (error) {
      if (window.portalGroup() !== group) return;
      host.replaceChildren(section(group, descriptions[group]),
                           info("Не удалось загрузить данные", error.message));
    }
  }
  async function overview() {
    const result = await api("/api/portal/overview");
    const box = e("div", "portal-stack");
    const grid = e("div", "portal-metrics");
    for (const [label, value] of [
      ["Открытые обращения", result.open_tickets],
      ["Заявки на рассмотрении", result.pending_applications],
      ["Моя роль на сайте", roleLabels[result.role] || result.role],
    ]) {
      grid.append(append(e("div", "portal-metric"),
        e("span", "", label), e("strong", "", value)));
    }
    box.append(grid);
    box.append(info("Рабочая панель NewSqad",
      "Открой раздел «Заявки» или «Тикеты» для работы. Доступные разделы зависят от твоих текущих Discord-ролей."));
    return box;
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
    const toolbar = e("div", "portal-toolbar");
    const select = e("select", "portal-select");
    for (const status of ["open", "approved", "rejected", "all"]) {
      const item = e("option", "", {open: "Открытые", approved: "Одобренные",
        rejected: "Отклонённые", all: "Все"}[status]);
      item.value = status; select.append(item);
    }
    toolbar.append(e("span", "portal-muted", "Фильтр заявок"), select);
    const body = e("div");
    async function populate() {
      const result = await api("/api/portal/applications?status=" + select.value);
      body.replaceChildren(cardList(result.items, (item) => {
        const row = e("div", "portal-list-row");
        row.append(rowTitle("#" + item.id + " · " + item.name + " · " + item.username,
          format(item.created_at)));
        row.append(pill(item.status, item.status === "approved" ? "success" : "neutral"));
        row.append(button("Рассмотреть", () => { void applicationDetails(item.id); }));
        return row;
      }, "Новых заявок сейчас нет."));
    }
    select.addEventListener("change", () => { void populate(); });
    await populate();
    box.append(toolbar, body);
    return box;
  }
  async function confirmAction(title, description, positive = "Подтвердить") {
    return new Promise((resolve) => {
      const overlay = e("div", "portal-modal-overlay");
      const dialog = e("div", "portal-modal");
      dialog.setAttribute("role", "alertdialog");
      dialog.setAttribute("aria-modal", "true");
      const footer = e("div", "portal-actions");
      function done(ok) { overlay.remove(); resolve(ok); }
      footer.append(button("Отмена", () => done(false)),
                    button(positive, () => done(true), "danger"));
      dialog.append(e("h3", "", title), e("p", "", description), footer);
      overlay.append(dialog); document.body.append(overlay);
      dialog.querySelector("button").focus();
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
      function done(value) { overlay.remove(); resolve(value); }
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
      overlay.append(dialog);document.body.append(overlay);field.focus();
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
      function done(result) {overlay.remove();resolve(result);}
      footer.append(button("Отмена", () => done(null)));
      footer.append(button("Передать", () => done(select.value), "primary"));
      dialog.append(e("h3", "", "Передать обращение"),
        e("p", "", "Выберите сотрудника с действующей ролью NewSqad."),select,footer);
      overlay.append(dialog);document.body.append(overlay);select.focus();
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
  function stopTicketRefresh() {
    if (ticketRefresh) { clearInterval(ticketRefresh); ticketRefresh = null; }
  }
  async function tickets() {
    stopTicketRefresh();
    const data = await api("/api/portal/tickets");
    const result = e("div", "portal-stack");
    result.append(cardList(data.items, (item) => {
      const row = e("div", "portal-list-row");
      row.append(rowTitle("#" + item.id + " · " + item.subject,
        "Создан: " + format(item.opened_at)));
      row.append(pill(item.assignee_id ? "В работе" : "Не назначен",
                      item.assignee_id ? "success" : "neutral"));
      if (item.status === "open") row.append(button("Открыть чат", () => { void ticketChat(item.id); }));
      return row;
    }, "Открытых обращений нет."));
    return result;
  }
  async function ticketChat(id) {
    stopTicketRefresh();
    const host = root();
    host.replaceChildren(info("Тикет #" + id, "Получаем приватную переписку напрямую из Discord..."));
    const wrap = e("div", "portal-stack");
    const header = section("Тикет #" + id, "Переписка отображается из Discord и не архивируется на сайте.");
    const actions = e("div", "portal-actions");
    const messages = e("div", "portal-chat");
    const form = e("form", "portal-compose");
    const textarea = e("textarea", "portal-textarea");
    textarea.placeholder = "Написать ответ участнику...";
    textarea.maxLength = 1500;
    textarea.rows = 3;
    const send = button("Отправить в Discord", () => {}, "primary");
    send.type = "submit";
    form.append(textarea, send);
    const foot = e("div", "portal-muted", "Сообщения отправляются от имени бота с подписью сотрудника. Переписка не хранится в SQLite.");
    wrap.append(header, actions, messages, form, foot);
    let lastMessages = "";
    async function refreshMessages() {
      if (window.portalGroup() !== "Тикеты" || !messages.isConnected) return;
      try {
        const data = await api("/api/portal/tickets/" + id);
        const ticket = data.ticket;
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
          if (nearBottom) messages.scrollTop = messages.scrollHeight;
          lastMessages = signature;
        }
      } catch (error) {
        messages.replaceChildren(info("Чат недоступен", error.message));
      }
    }
    async function action(kind, additional = {}) {
      try {
        const data = await api("/api/portal/tickets/" + id + "/action",
          {method: "POST", body: {action: kind, ...additional}});
        window.portalToast(data.message);
        if (kind === "close") { stopTicketRefresh(); await render("Тикеты"); return true; }
        await refreshMessages();
        return true;
      } catch (error) { window.portalToast(error.message, true); return false; }
    }
    actions.append(button("← Все обращения", () => { stopTicketRefresh(); void render("Тикеты"); }));
    actions.append(button("Взять в работу", () => { void action("claim"); }, "success"));
    actions.append(button("Передать", async () => {
      const staffId = await chooseStaff();
      if (staffId) await action("transfer", {user_id: staffId});
    }));
    actions.append(button("Закрыть и удалить", async () => {
      if (await confirmAction("Удалить тикет безвозвратно?",
        "Discord-канал и вся переписка будут безвозвратно удалены. Транскрипт не создаётся.",
        "Удалить тикет")) await action("close", {confirm: "DELETE"});
    }, "danger"));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const text = textarea.value.trim();
      if (!text) return;
      send.disabled = true;
      const delivered = await action("reply", {text});
      if (delivered) textarea.value = "";
      send.disabled = false;
    });
    host.replaceChildren(wrap);
    await refreshMessages();
    ticketRefresh = setInterval(() => { void refreshMessages(); }, 12000);
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
        return row;
      }, "Участники не найдены."));
      body.prepend(e("p", "portal-muted", "В кэше: " + data.cached_members + ", участников сервера: " + (data.server_member_count || "—")));
    }
    form.addEventListener("submit", (evt) => { evt.preventDefault();void query(); });
    await query();
    box.append(form, body);
    return box;
  }
  async function moderation() {
    const data = await api("/api/portal/moderation");
    return cardList(data.items, (item) => {
      const row = e("div", "portal-list-row");
      row.append(rowTitle("Дело #" + item.id + " · " + item.kind,
        "Участник " + item.user_id + " · " + format(item.created_at)));
      row.append(pill(item.status, item.status === "active" ? "warning" : "neutral"));
      row.append(e("span", "portal-ellipsis", item.reason));
      return row;
    }, "Дел не найдено.");
  }
  async function statsPage() {
    const box = e("div", "portal-stack");
    const toolbar = e("div", "portal-toolbar");
    const periods = e("select", "portal-select");
    for (const [v, name] of [["7d", "7 дней"], ["30d", "30 дней"], ["90d", "90 дней"]]) {
      const o = e("option", "", name);o.value=v;periods.append(o);
    }
    toolbar.append(e("span", "", "Период"), periods);
    const content = e("div");
    async function show() {
      const data=await api("/api/portal/stats?period="+periods.value);
      const grid=e("div","portal-metrics");
      for (const [name,value] of Object.entries(data.totals)) {
        const labels={total:"Список участников",approved:"Одобрены",candidates:"Кандидаты",
          joins:"Вступления",leaves:"Выходы",approvals:"Принятые заявки",rejections:"Отказы",
          messages:"Сообщения",voice_minutes:"Голос, мин.",active_users:"Активные участники",
          events:"Мероприятия",parties:"Группы",tickets:"Обращения"};
        grid.append(append(e("div","portal-metric"),e("span","",labels[name]||name),e("strong","",value)));
      }
      const series=e("div","portal-panel");
      series.append(e("h3","","Сообщения по дням"));
      const max=Math.max(1,...data.series.map(x=>x.messages));
      const graph=e("div","portal-bars");
      for(const day of data.series) {
        const row=e("div","portal-bar-row");
        row.append(e("span","",day.date));
        const bar=e("div","portal-bar-track");
        const progress=e("div","portal-bar-fill");
        progress.style.width=(100*day.messages/max)+"%";
        bar.append(progress);
        row.append(bar,e("strong","",day.messages));
        graph.append(row);
      }
      series.append(graph);
      content.replaceChildren(grid,series);
    }
    periods.addEventListener("change",()=>{void show();});
    await show(); box.append(toolbar,content);return box;
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
  async function audit(){
    const data=await api("/api/portal/audit");
    return cardList(data.items,(item)=>{
      const row=e("div","portal-history-row");
      row.append(rowTitle(item.action,format(item.at)));
      row.append(e("p","","Исполнитель "+item.actor_id+" · "+JSON.stringify(item.details)));
      return row;
    },"Системных записей нет.");
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
  const loaders={"Обзор":overview,"Заявки":applications,"Тикеты":tickets,
    "Сотрудники":staff,"Список участников":members,"Модерация":moderation,"Аналитика":statsPage,
    "Рейтинги":rankings,"Активность":activity,"Аудит":audit};
  async function render(group) {
    if (group !== "Тикеты") stopTicketRefresh();
    if (loaders[group]) await load(group, loaders[group]);
  }
  window.Portal={menu,roleLabels,icon,descriptions,render,stopTicketRefresh};
})();
