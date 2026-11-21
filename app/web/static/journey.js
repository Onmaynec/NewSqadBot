"use strict";

/* v2.1.0 Member Journey & Support extensions for Obsidian Staff Command Center.
 * All Discord-origin text enters via textContent, never via innerHTML.
 */
(() => {
  const api = (url, options) => window.portalAPI(url, options);
  const e = (tag, cls = "", text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = String(text);
    return node;
  };
  const add = (parent, ...items) => { parent.append(...items); return parent; };
  const button = (label, callback) => {
    const node = e("button", "button secondary", label);
    node.type = "button"; node.addEventListener("click", callback); return node;
  };
  const field = (name, value = "", type = "text") => {
    const node = e("input", "portal-input"); node.type = type; node.value = String(value);
    node.setAttribute("aria-label", name); node.placeholder = name; return node;
  };
  const area = (name, value = "", max = 1200) => {
    const node = e("textarea", "portal-textarea"); node.value = value;
    node.setAttribute("aria-label", name); node.maxLength = max; node.rows = 3; return node;
  };
  const card = (name, detail = "") => add(e("article", "staff-card"),
    e("h3", "", name), e("p", "portal-muted", detail));
  const row = (title, text = "") => add(e("div", "staff-row"),
    add(e("div", "staff-row-text"), e("strong", "", title), e("small", "", text)));
  const group = () => e("div", "portal-stack");
  const form = (...els) => add(e("div", "staff-form"), ...els);
  const toast = message => window.portalToast(message);
  const fail = error => window.portalToast(error.message || "Операция не выполнена", true);
  const savedDraft = new WeakMap();
  const drafts = new Set();
  function track(node) { savedDraft.set(node, node.value); drafts.add(node); return node; }
  const hasDraft = () => [...drafts].some(node => node.isConnected && node.value !== savedDraft.get(node));
  function cleanDraft(nodes) { for (const node of nodes) savedDraft.set(node, node.value); }
  const reload = () => window.portalReload();

  async function candidates() {
    const root = group();
    const forms = await api("/api/v1/membership/forms");
    const section = card("Типы заявок", "Каждый тип добавляет собственный вопрос в Discord-анкету. Базовая анкета остаётся доступной.");
    const key = field("Ключ типа (латиница)", "", "text");
    const name = field("Название типа");
    const question = area("Дополнительный вопрос", "", 100);
    key.maxLength = 32; name.maxLength = 80;
    const save = button("Сохранить тип", async () => {
      try {
        await api("/api/v1/membership/forms", {method: "POST", body: {
          key: key.value, title: name.value, question: question.value, enabled: true,
        }});
        toast("Тип заявки сохранён"); reload();
      } catch (err) {fail(err);}
    });
    section.append(form(key, name, question, save));
    for (const item of forms.items) {
      const record = row(item.title + " · " + item.key,
        item.question || "Стандартная форма" + (item.enabled ? "" : " · отключена"));
      if (item.key !== "standard") {
        record.append(button("Редактировать", () => {
          key.value = item.key; name.value = item.title; question.value = item.question;
          question.focus();
        }));
        record.append(button(item.enabled ? "Отключить" : "Включить", async () => {
          try {
            await api("/api/v1/membership/forms", {method: "POST", body: {
              key: item.key, title: item.title, question: item.question, enabled: !item.enabled,
            }});
            reload();
          } catch (err) {fail(err);}
        }));
      }
      section.append(record);
    }
    const checks = card("Индивидуальный чек-лист кандидата",
      "Отмечайте проверенные пункты без хранения копий документов и личных комментариев.");
    const applicationId = field("Номер заявки", "", "number");
    const list = e("div", "staff-list");
    const load = button("Открыть проверки", async () => {
      try {
        const data = await api("/api/v1/membership/applications/" +
          encodeURIComponent(applicationId.value) + "/checklist");
        list.replaceChildren();
        for (const item of data.items) {
          const check = field("Проверка: " + item.title, "", "checkbox");
          check.checked = item.completed; check.className = "staff-check";
          check.addEventListener("change", async () => {
            try {
              await api("/api/v1/membership/applications/" +
                encodeURIComponent(applicationId.value) + "/checklist",
              {method: "POST", body: {key: item.key, completed: check.checked}});
              toast("Результат проверки записан");
            } catch (err) {check.checked = !check.checked; fail(err);}
          });
          const r = row(item.title, item.actor_id ? "Проверил: " + item.actor_id : "Ожидает");
          r.prepend(check); list.append(r);
        }
        if (!data.items.length) list.append(e("p", "portal-muted", "Проверок нет или заявка не найдена."));
      } catch (err) {fail(err);}
    });
    checks.append(form(applicationId, load), list);
    const newKey = field("Ключ проверки", "", "text");
    const newTitle = field("Название новой проверки");
    const createCheck = button("Добавить пункт", async () => {
      try {
        await api("/api/v1/membership/applications/" +
          encodeURIComponent(applicationId.value) + "/checklist",
          {method: "POST", body: {key: newKey.value, title: newTitle.value, completed: false}});
        toast("Проверка добавлена"); await load.click();
      } catch (err) {fail(err);}
    });
    checks.append(form(newKey, newTitle, createCheck));

    const onboarding = card("Панель адаптации новичка",
      "Содержимое команды /application guide: правила, первые задания и только доступные пользователю каналы.");
    const cfg = (await api("/api/v1/membership/onboarding")).config;
    const rules = track(area("Правила", cfg.rules, 1200));
    const tasks = track(area("Задания (по одному на строку)", cfg.tasks.join("\n"), 900));
    const channels = track(field("Discord ID каналов через запятую", cfg.channel_ids.join(",")));
    const saveOnboarding = button("Сохранить адаптацию", async () => {
      const values = channels.value.split(",").map(x => x.trim()).filter(Boolean);
      try {
        await api("/api/v1/membership/onboarding", {method: "POST", body: {config: {
          rules: rules.value, tasks: tasks.value.split("\n").map(x => x.trim()).filter(Boolean),
          channel_ids: values,
        }}});
        cleanDraft([rules, tasks, channels]); toast("Адаптация обновлена");
      } catch (err) {fail(err);}
    });
    onboarding.append(form(rules, tasks, channels, saveOnboarding));
    root.append(section, checks, onboarding);
    return root;
  }

  async function support() {
    const root = group();
    const result = await Promise.all([
      api("/api/v1/support/sla"), api("/api/staff/inbox?kind=ticket"),
      api("/api/portal/support-quality?days=30"),
    ]);
    const [sla, inbox, quality] = result;
    const rules = card("Целевые показатели поддержки",
      "В минутах: первый ответ и бездействие. Уведомления не закрывают обращения автоматически.");
    const fields = {};
    for (const [key, label] of [
      ["urgent", "Срочный"], ["high", "Высокий"],
      ["normal", "Обычный"], ["low", "Низкий"],
    ]) {
      const first = field("Первый ответ — " + label, sla.rules[key].first_minutes, "number");
      const idle = field("Бездействие — " + label, sla.rules[key].idle_minutes, "number");
      first.min = idle.min = "5"; first.max = idle.max = "43200";
      fields[key] = {first, idle};
      rules.append(add(e("div", "staff-sla-rule"), e("strong", "", label), first, idle));
    }
    rules.append(button("Сохранить правила SLA", async () => {
      const values = {};
      for (const [key, parts] of Object.entries(fields)) {
        values[key] = {first_minutes: Number(parts.first.value),
                       idle_minutes: Number(parts.idle.value)};
      }
      try { await api("/api/v1/support/sla", {method:"POST",body:{rules:values}});
        toast("Правила SLA обновлены"); } catch(err){fail(err);}
    }));
    const urgent = card("Срочные обращения", "Очередь упорядочена с учётом приоритета.");
    const select = e("select", "portal-select");
    for (const value of ["low", "normal", "high", "urgent"]) {
      const opt = e("option", "", value);opt.value = value;select.append(opt);
    }
    for (const t of inbox.items.slice(0, 60)) {
      const entry = row("#" + t.id + " · " + t.title,
                        (t.priority || "normal") + " · " + t.status);
      const change = select.cloneNode(true); change.value = t.priority || "normal";
      const apply = button("Приоритет", async () => {
        try {
          await api("/api/v1/support/tickets/" + t.id + "/priority",
            {method: "POST", body: {priority: change.value}});
          toast("Приоритет изменён"); reload();
        } catch (err) {fail(err);}
      });
      entry.append(change, apply); urgent.append(entry);
    }
    const stats = card("Качество поддержки · 30 дней",
      "Оценки остаются анонимизированными, без сохранения текстов переписки.");
    const summary = quality.summary || quality;
    for (const [label, value] of [
      ["Средняя оценка", summary.average_rating ?? "—"],
      ["Оценок", summary.ratings ?? 0],
      ["Закрытых обращений", summary.closed_tickets ?? 0],
      ["Первый ответ", summary.avg_first_response_seconds == null
        ? "—" : Math.round(summary.avg_first_response_seconds / 60) + " мин"],
    ]) stats.append(row(label, value));
    root.append(rules, urgent, stats);
    return root;
  }

  async function voice() {
    const root = group();
    const data = await api("/api/v1/voice/usage?days=30");
    const total = data.items.reduce((acc, x) => ({
      rooms: acc.rooms + x.rooms_created, seconds: acc.seconds + x.occupied_seconds,
    }), {rooms: 0, seconds: 0});
    const overview = e("div", "staff-kpis");
    overview.append(add(e("div", "staff-kpi"), e("span", "", "КОМНАТ СОЗДАНО"),
      e("strong", "", total.rooms)), add(e("div", "staff-kpi"),
      e("span", "", "ВРЕМЯ СУЩЕСТВОВАНИЯ"), e("strong", "", (total.seconds / 3600).toFixed(1) + " ч")));
    const history = card("Статистика голосовых комнат · 30 дней",
      "Счётчики создания и времени существования. Разговоры и персональные списки посещений не записываются.");
    for (const day of data.items) history.append(row(day.day,
      day.rooms_created + " новых · " + Math.round(day.occupied_seconds / 60) + " мин"));
    const guide = card("Голосовые комнаты 2.0",
      "Шаблоны и персональные ACL настраиваются владельцем внутри Discord.");
    for (const [name, description] of [
      ["/voice preset public", "Общая — открыта всем допущенным участникам"],
      ["/voice preset private", "Закрытая — не видна и закрыта до приглашения"],
      ["/voice preset team", "Командная — открывается приглашённым, лимит 10"],
      ["/voice access", "Список разрешённых и заблокированных участников"],
      ["/voice diagnose", "Диагностика разрешений Discord и генератора комнат"],
    ]) guide.append(row(name, description));
    root.append(overview, history, guide);
    return root;
  }

  Object.assign(window.StaffCenter.loaders, {
    "Кандидаты и адаптация": candidates,
    "SLA поддержки": support,
    "Голосовые 2.0": voice,
  });
  Object.assign(window.StaffCenter.descriptions, {
    "Кандидаты и адаптация": "Настраиваемые формы, чек-листы кандидатов и адаптация новых участников.",
    "SLA поддержки": "Приоритеты, время ответа, бездействие и оценки качества поддержки.",
    "Голосовые 2.0": "Шаблоны личных комнат, диагностика и обезличенная статистика.",
  });
  window.MemberSupportVoice = {hasDraft};
})();
