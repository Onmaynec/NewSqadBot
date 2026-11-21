"use strict";

/* Obsidian Staff Command Center. Text from Discord/SQLite is never assigned
 * to innerHTML; confirmations and reply drafts are local to this browser. */
(() => {
  const labels = {
    "Единая очередь": "Заявки, обращения, жалобы и открытые дела в одном списке.",
    "Мои задачи": "Назначенные обращения и задачи локального оператора.",
    "История сотрудников": "Хронология действий сотрудников с доступом только владельцу.",
    "Шаблоны ответов": "Готовые ответы с обязательным редактируемым предпросмотром.",
    "Центр состояния": "Gateway, SQLite, фоновые процессы, модули и резервные копии.",
    "Аналитика команды": "Динамика обращений, нагрузка сотрудников и скорость ответа.",
  };
  const api = (path, opts) => window.portalAPI(path, opts);
  const el = (tag, cls = "", value) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (value !== undefined && value !== null) node.textContent = String(value);
    return node;
  };
  const append = (parent, ...children) => { parent.append(...children); return parent; };
  const title = (name, text) => append(el("div", "portal-heading"), el("h2", "", name), el("p", "", text));
  const btn = (name, onClick, variant = "secondary") => {
    const node = el("button", "button " + variant, name);
    node.type = "button"; node.addEventListener("click", onClick); return node;
  };
  const field = (hint, type = "text", placeholder = "") => {
    const node = el("input", "portal-input"); node.type = type;
    node.placeholder = placeholder; node.setAttribute("aria-label", hint); return node;
  };
  const chooser = (hint, choices) => {
    const node = el("select", "portal-select");
    node.setAttribute("aria-label", hint);
    for (const [value, caption] of choices) {
      const option = el("option", "", caption); option.value = value; node.append(option);
    }
    return node;
  };
  const area = (hint, max = 1500) => {
    const node = el("textarea", "portal-textarea");
    node.maxLength = max; node.rows = 5; node.setAttribute("aria-label", hint); return node;
  };
  const caption = (text) => el("p", "portal-muted", text);
  const pretty = (timestamp) => timestamp
    ? new Date(timestamp * 1000).toLocaleString("ru-RU", {dateStyle: "medium", timeStyle: "short"}) : "—";
  const error = e => window.portalToast(e.message || "Ошибка операции", true);
  const notify = text => window.portalToast(text);
  const nav = name => { void window.portalNavigate(name); };
  const card = (heading, text) => append(el("article", "staff-card"), el("h3", "", heading), caption(text));
  const row = (heading, meta) => append(el("div", "staff-row"),
    append(el("div", "staff-row-text"), el("strong", "", heading), el("small", "", meta)));
  let draft = null;
  let draftSaved = "";
  const hasDraft = () => Boolean(draft?.isConnected && draft.value.trim() && draft.value !== draftSaved);

  function modal(question, detail = "") {
    return new Promise(resolve => {
      const overlay = el("div", "portal-modal-overlay");
      const panel = el("section", "portal-modal");
      panel.setAttribute("role", "dialog"); panel.setAttribute("aria-modal", "true");
      const yes = btn("Подтвердить", () => done(true), "primary");
      const no = btn("Отмена", () => done(false));
      append(panel, el("h2", "", question), caption(detail),
        append(el("div", "portal-actions"), no, yes));
      overlay.append(panel);
      let teardown = () => overlay.remove();
      function done(value) { teardown(); resolve(value); }
      document.body.append(overlay);
      if (window.ObsidianDialog) teardown = window.ObsidianDialog.mount(overlay, panel, () => done(false));
      else yes.focus();
    });
  }
  function reload() { window.portalReload(); }

  async function unified() {
    const host = el("div", "portal-stack");
    const top = el("div", "staff-kpis");
    const controls = el("div", "staff-toolbar");
    const kind = chooser("Тип обращений", [
      ["all", "Все типы"], ["application", "Заявки"], ["ticket", "Тикеты"],
      ["complaint", "Жалобы"], ["moderation", "Модерация"],
    ]);
    const assigned = chooser("Назначение", [["all", "Все"], ["mine", "Мои"], ["unassigned", "Без ответственного"]]);
    const search = field("Поиск", "search", "Номер, имя, Discord ID");
    const list = el("div", "staff-list");
    const selected = new Set();
    const action = chooser("Массовое действие", [
      ["claim", "Назначить мне"], ["approve", "Одобрить"], ["reject", "Отклонить"],
    ]);
    const reason = field("Причина отказа", "text", "Для отклонения (обязательно)");
    reason.maxLength = 400;
    const send = btn("Выполнить с подтверждением", async () => {
      if (!selected.size) { notify("Выберите заявки галочками."); return; }
      const ids = [...selected];
      if (!await modal("Массовое действие: " + ids.length, "Операции выполняются последовательно. Отправка в Discord может быть необратимой.")) return;
      send.disabled = true;
      const key = globalThis.crypto?.randomUUID?.() || String(Date.now()) + "bulk" + Math.random().toString(36).slice(2);
      try {
        const result = await api("/api/staff/bulk", {method: "POST", body: {
          request_key: key, ids, action: action.value, reason: reason.value, confirm: "CONFIRM",
        }});
        selected.clear();
        const report = card("Результаты операции", result.success + " успешно · " +
          result.needs_review + " требуют проверки · ключ " + result.request_key);
        for (const item of result.items) report.append(caption("#" + item.id + " · " + item.status + " · " + item.message));
        host.querySelector(".staff-bulk-report")?.remove();
        report.classList.add("staff-bulk-report");
        host.append(report);
        await loadItems();
      } catch (err) { error(err); }
      finally { send.disabled = false; }
    }, "primary");
    const bulk = append(el("div", "staff-bulk"), el("strong", "", "МАССОВЫЕ ОПЕРАЦИИ ПО ЗАЯВКАМ"),
      caption("До 20 заявок за один запрос. Повторное нажатие с тем же ключом не выполняет действие заново."),
      append(el("div", "staff-toolbar"), action, reason, send));
    append(controls, kind, assigned, search, btn("Обновить", () => { void loadItems(); }));
    append(host, top, controls, list, bulk);
    let requestNumber = 0;
    async function loadItems() {
      const current = ++requestNumber;
      try {
        const params = new URLSearchParams({kind: kind.value, assignment: assigned.value, q: search.value});
        const data = await api("/api/staff/inbox?" + params);
        if (current !== requestNumber) return;
        top.replaceChildren();
        for (const [label, value] of [["ВСЕГО", data.total], ["ЗАЯВКИ", data.counts.application],
          ["ТИКЕТЫ", data.counts.ticket], ["ЖАЛОБЫ", data.counts.complaint],
          ["МОДЕРАЦИЯ", data.counts.moderation]]) {
          top.append(append(el("div", "staff-kpi"), el("span", "", label), el("strong", "", value)));
        }
        list.replaceChildren();
        for (const item of data.items) {
          const itemRow = row("#" + item.id + " · " + item.title,
            item.details + " · " + item.status + " · " + pretty(item.created_at));
          const details = el("div", "staff-row-side");
          details.append(el("span", "portal-pill", item.kind));
          details.append(el("small", "", item.assigned_to ? "Ответственный: " + item.assigned_to : "Не назначен"));
          details.append(btn("Открыть раздел", () => nav(item.link)));
          if (item.kind === "application") {
            const box = field("Выбрать заявку №" + item.id, "checkbox");
            box.className = "staff-check"; box.checked = selected.has(item.id);
            box.addEventListener("change", () => {
              if (box.checked && selected.size >= 20) {box.checked = false; notify("Не более 20 заявок."); return;}
              if (box.checked) selected.add(item.id); else selected.delete(item.id);
            });
            details.prepend(box);
          }
          itemRow.append(details);
          list.append(itemRow);
        }
        if (!data.items.length) list.append(card("Очередь пуста", "Для выбранных фильтров нет активных обращений."));
      } catch (err) { error(err); }
    }
    kind.addEventListener("change", () => { selected.clear(); void loadItems(); });
    assigned.addEventListener("change", () => { selected.clear(); void loadItems(); });
    let pending = 0;
    search.addEventListener("input", () => { clearTimeout(pending); pending = setTimeout(() => {
      selected.clear(); void loadItems();
    }, 250); });
    await loadItems();
    return host;
  }

  async function myTasks() {
    const host = el("div", "portal-stack");
    const data = await Promise.all([api("/api/staff/tasks?mine=1"), api("/api/staff/inbox?assignment=mine")]);
    const my = data[0], inbox = data[1];
    const overview = card("Назначенные обращения", inbox.total + " обращений у локального оператора " +
      (my.operator_id || "не определён"));
    for (const item of inbox.items.slice(0, 12)) {
      overview.append(row("#" + item.id + " · " + item.title, item.kind + " · " + item.status));
    }
    const create = card("Создать задачу", "Задачи сохраняются в SQLite и доступны после перезапуска.");
    const titleInput = field("Заголовок", "text", "Проверить обращение"); titleInput.maxLength = 120;
    const description = area("Описание задачи", 600); description.rows = 2;
    const assignee = field("Discord ID сотрудника", "text", my.operator_id || "");
    assignee.value = my.operator_id || ""; assignee.maxLength = 20;
    const due = field("Срок выполнения", "datetime-local");
    const save = btn("Добавить задачу", async () => {
      save.disabled = true;
      try {
        await api("/api/staff/tasks", {method: "POST", body: {
          title: titleInput.value, description: description.value,
          assigned_to: assignee.value, due_at: due.value ? Math.floor(new Date(due.value).getTime()/1000) : null,
        }});
        notify("Задача создана"); reload();
      } catch (err) { error(err); } finally { save.disabled = false; }
    }, "primary");
    create.append(append(el("div", "staff-form"), titleInput, description, assignee, due, save));
    const tasks = card("Мои задачи", my.items.length + " задач");
    for (const item of my.items) {
      const itemRow = row("#" + item.id + " · " + item.title,
        item.status + " · назначено " + item.assigned_to + " · срок: " + pretty(item.due_at));
      const side = el("div", "staff-row-side");
      side.append(btn(item.status === "open" ? "Завершить" : "Вернуть в работу", async () => {
        const status = item.status === "open" ? "done" : "open";
        try {
          await api("/api/staff/tasks/" + item.id + "/status",
            {method: "POST", body: {status}});
          notify("Статус задачи обновлён"); reload();
        } catch (err) { error(err); }
      }));
      itemRow.append(side); tasks.append(itemRow);
    }
    if (!my.items.length) tasks.append(caption("Пока нет назначенных задач."));
    host.append(overview, create, tasks);
    return host;
  }

  async function staffTimeline() {
    const host = el("div", "portal-stack");
    const editor = card("История конкретного сотрудника",
      "Доступна только локальному владельцу. Содержимое тикетов и личных сообщений не сохраняется.");
    const input = field("Discord ID сотрудника", "text", "Введите Discord ID");
    input.maxLength = 20;
    const results = el("div", "staff-list");
    const search = btn("Открыть историю", async () => {
      try {
        const data = await api("/api/staff/timeline?user_id=" + encodeURIComponent(input.value));
        results.replaceChildren();
        for (const item of data.items) results.append(row(item.action,
          pretty(item.at) + " · " + item.kind + " · " + item.detail +
          (item.target ? " #" + item.target : "")));
        if (!data.items.length) results.append(caption("Действий не найдено."));
      } catch (err) { error(err); }
    }, "primary");
    editor.append(append(el("div", "staff-toolbar"), input, search));
    host.append(editor, results);
    return host;
  }

  async function responseTemplates() {
    const data = await api("/api/staff/templates");
    const host = el("div", "portal-stack");
    const create = card("Шаблоны ответов",
      "Шаблон никогда не отправляется автоматически. Перед отправкой текст можно полностью изменить.");
    const scope = chooser("Для", [["ticket", "Тикеты / жалобы"], ["application", "Заявки"]]);
    const name = field("Название шаблона", "text", "Первый ответ"); name.maxLength = 100;
    const body = area("Текст шаблона", 1500);
    let editingId = null;
    scope.addEventListener("change", () => { body.maxLength = scope.value === "ticket" ? 1500 : 500; });
    const save = btn("Сохранить шаблон", async () => {
      try {
        await api("/api/staff/templates", {method: "POST", body: {
          id: editingId, scope: scope.value, title: name.value, body: body.value,
        }});
        notify("Шаблон сохранён"); reload();
      } catch (err) {error(err);}
    }, "primary");
    create.append(append(el("div", "staff-form"), scope, name, body, save));
    const templates = card("Сохранённые шаблоны", data.items.length + " шаблонов");
    for (const item of data.items) {
      const entry = row(item.title, item.scope + " · " + pretty(item.updated_at));
      const side = el("div", "staff-row-side");
      side.append(btn("Редактировать", () => {
        editingId = item.id; scope.value = item.scope; name.value = item.title;
        body.maxLength = item.scope === "ticket" ? 1500 : 500;
        body.value = item.body; body.focus();
      }));
      side.append(btn("Удалить", async () => {
        if (!await modal("Удалить шаблон?", "Это не удалит отправленные сообщения.")) return;
        try { await api("/api/staff/templates/" + item.id + "/delete",
          {method: "POST", body: {}}); reload(); } catch (err) {error(err);}
      }, "danger"));
      templates.append(entry); entry.append(side);
    }
    const send = card("Написать ответ",
      "Выберите обращение, вставьте шаблон и при необходимости отредактируйте текст.");
    const targetKind = chooser("Тип обращения", [["ticket", "Тикет / жалоба"], ["application", "Заявка"]]);
    const targetId = field("Номер обращения", "number", "Номер заявки или тикета");
    const template = chooser("Шаблон", [["", "Без шаблона"], ...data.items.map(x => [String(x.id), x.title])]);
    const message = area("Редактируемый текст ответа", 1500);
    draft = message; draftSaved = "";
    template.addEventListener("change", () => {
      const selected = data.items.find(x => String(x.id) === template.value);
      if (selected) { targetKind.value = selected.scope; message.value = selected.body; }
    });
    targetKind.addEventListener("change", () => {message.maxLength = targetKind.value === "ticket" ? 1500 : 500;});
    const publish = btn("Отправить в Discord", async () => {
      if (!await modal("Подтвердить отправку ответа?", "Повторная отправка может продублировать сообщение, поэтому применяется уникальный ключ.")) return;
      publish.disabled = true;
      try {
        const request_key = globalThis.crypto?.randomUUID?.() ||
          String(Date.now()) + "reply" + Math.random().toString(36).slice(2);
        const result = await api("/api/staff/reply", {method: "POST", body: {
          kind: targetKind.value, id: targetId.value,
          text: message.value, request_key,
        }});
        draftSaved = message.value;
        notify(result.message); reload();
      } catch (err) {error(err);} finally {publish.disabled = false;}
    }, "primary");
    send.append(append(el("div", "staff-form"), targetKind, targetId, template, message, publish));
    host.append(create, templates, send);
    return host;
  }

  async function health() {
    const data = await api("/api/staff/health");
    const host = el("div", "portal-stack");
    const overview = el("div", "staff-kpis");
    const journal = data.operations || {};
    for (const [label, value] of [
      ["DISCORD GATEWAY", data.gateway_ready ? "ONLINE" : "OFFLINE"],
      ["HTTP-ОШИБКИ", journal.http_errors ?? "—"],
      ["ЛИМИТЫ 429", journal.http_rate_limits ?? "—"],
      ["МОДУЛИ С ОШИБКОЙ", Object.values(data.modules || {}).filter(x => x === "error").length],
    ]) overview.append(append(el("div", "staff-kpi"), el("span", "", label), el("strong", "", value)));
    const checks = card("Состояние сервисов", "Подробная диагностика Discord, SQLite и фоновых процессов.");
    for (const x of data.items) {
      const item = row(x.name, x.description);
      item.append(el("span", "portal-pill " + (x.level === "ok" ? "success" : "warning"), x.level));
      checks.append(item);
    }
    const events = card("Журнал восстановления", "Последние служебные события");
    for (const x of (journal.history || []).slice(-15).reverse())
      events.append(row(x.kind + (x.module ? " · " + x.module : ""), pretty(x.at)));
    host.append(overview, checks, events);
    return host;
  }

  async function analytics() {
    const host = el("div", "portal-stack");
    const filter = chooser("Период", [["7", "7 дней"], ["14", "14 дней"],
      ["30", "30 дней"], ["90", "90 дней"]]);
    const result = el("div", "portal-stack");
    host.append(append(el("div", "staff-toolbar"), filter), result);
    async function draw() {
      try {
        const data = await api("/api/staff/analytics?days=" + filter.value);
        result.replaceChildren();
        const cards = el("div", "staff-kpis");
        for (const [name, number] of [
          ["ЗАЯВКИ", data.totals.applications], ["ТИКЕТЫ", data.totals.tickets],
          ["ДЕЛА МОДЕРАЦИИ", data.totals.moderation],
          ["ПЕРВЫЙ ОТВЕТ", data.avg_first_response_seconds === null
            ? "—" : Math.round(data.avg_first_response_seconds / 60) + " мин"],
        ]) cards.append(append(el("div", "staff-kpi"), el("span", "", name), el("strong", "", number)));
        const chart = card("Динамика обращений", "По дням UTC; количество новых обращений.");
        const max = Math.max(1, ...data.trend.map(x => x.applications + x.tickets + x.moderation));
        for (const point of data.trend) {
          const total = point.applications + point.tickets + point.moderation;
          const bar = el("div", "staff-chart-row");
          const meter = el("div", "staff-chart-track");
          const fill = el("span", "staff-chart-fill");
          fill.style.width = (total / max * 100) + "%";
          meter.append(fill);
          bar.append(el("small", "", point.day), meter, el("strong", "", total));
          chart.append(bar);
        }
        const load = card("Нагрузка команды", "Активные задачи и обращения сотрудников");
        for (const person of data.staff)
          load.append(row(person.user_id,
            "Заявки: " + person.applications + " · Тикеты: " + person.tickets));
        if (!data.staff.length) load.append(caption("Нет назначенных активных обращений."));
        result.append(cards, chart, load);
      } catch (err) {error(err);}
    }
    filter.addEventListener("change", () => { void draw(); });
    await draw();
    return host;
  }

  const loaders = {
    "Единая очередь": unified,
    "Мои задачи": myTasks,
    "История сотрудников": staffTimeline,
    "Шаблоны ответов": responseTemplates,
    "Центр состояния": health,
    "Аналитика команды": analytics,
  };
  window.StaffCenter = {loaders, descriptions: labels, hasDraft};
})();
