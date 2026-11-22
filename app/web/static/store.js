"use strict";

/* v2.2 seller storefront. No third-party scripts, no HTML from product titles. */
(() => {
  const api = (path, opts) => window.portalAPI(path, opts);
  const el = (tag, cls = "", value) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (value !== undefined) node.textContent = String(value);
    return node;
  };
  const add = (parent, ...children) => { parent.append(...children); return parent; };
  const card = (name, sub = "") => add(el("article", "staff-card"),
    el("h3", "", name), el("p", "portal-muted", sub));
  const row = (name, sub = "") => add(el("div", "staff-row"),
    add(el("div", "staff-row-text"), el("strong", "", name), el("small", "", sub)));
  const group = () => el("div", "portal-stack");
  const form = (...nodes) => add(el("div", "staff-form"), ...nodes);
  const button = (label, onClick, variant = "secondary") => {
    const node = el("button", "button " + variant, label);
    node.type = "button"; node.addEventListener("click", onClick); return node;
  };
  const input = (label, value = "", type = "text", max = 100) => {
    const node = el("input", "portal-input");
    node.type = type; node.value = String(value);
    node.maxLength = max; node.placeholder = label; node.setAttribute("aria-label", label);
    return node;
  };
  const textarea = (label, value = "", max = 600) => {
    const node = el("textarea", "portal-textarea");
    node.rows = 3; node.value = String(value); node.maxLength = max;
    node.placeholder = label; node.setAttribute("aria-label", label);
    return node;
  };
  const select = (label, choices, selected) => {
    const node = el("select", "portal-select");
    node.setAttribute("aria-label", label);
    for (const [value, title] of choices) {
      const option = el("option", "", title);
      option.value = value; node.append(option);
    }
    if (selected !== undefined) node.value = String(selected);
    return node;
  };
  const notify = message => window.portalToast(message);
  const fail = err => window.portalToast(err.message || "Операция не выполнена", true);
  const drafts = new Map();
  const track = (node) => {
    drafts.set(node, node.value); return node;
  };
  const changed = () => [...drafts].some(([node, value]) => node.isConnected && node.value !== value);
  const saved = (nodes) => { for (const node of nodes) drafts.set(node, node.value); };
  const refresh = () => window.portalReload();
  const currencies = [["RUB", "RUB · ₽"], ["EUR", "EUR · €"], ["USD", "USD · $"]];
  const types = [["digital", "Цифровой товар"], ["nitro_gift", "Подарок Nitro"],
    ["server_boost", "Буст сервера"], ["service", "Услуга"], ["other", "Другое"]];
  const statuses = [["", "Все статусы"], ["awaiting_payment", "Ожидает оплаты"],
    ["payment_reported", "Покупатель сообщил об оплате"], ["processing", "Выполняется"],
    ["completed", "Выполнен"], ["cancelled", "Отменён"]];
  const captions = Object.fromEntries(statuses);
  const money = (value, code) => (Math.floor(value / 100)).toLocaleString("ru-RU") +
    "," + String(value % 100).padStart(2, "0") + " " + code;
  const priceMinor = value => {
    if (!/^\d{1,7}([.,]\d{1,2})?$/.test(value)) throw new Error("Цена: например 399 или 399.50");
    const [major, fractional = ""] = value.replace(",", ".").split(".");
    const minor = Number(major) * 100 + Number(fractional.padEnd(2, "0"));
    if (minor < 1 || minor > 999999999) throw new Error("Цена вне диапазона.");
    return minor;
  };
  const priceText = minor => (Math.floor(minor / 100)) + "." + String(minor % 100).padStart(2, "0");

  async function ask(question) {
    return new Promise(resolve => {
      const overlay = el("div", "portal-modal-overlay");
      const dialog = el("section", "portal-modal");
      dialog.setAttribute("role", "dialog"); dialog.setAttribute("aria-modal", "true");
      const yes = button("Подтвердить", () => done(true), "primary");
      const no = button("Отмена", () => done(false));
      dialog.append(el("h2", "", question), form(no, yes));
      overlay.append(dialog);
      let cleanup = () => overlay.remove();
      function done(value) { cleanup(); resolve(value); }
      if (window.ObsidianDialog) cleanup = window.ObsidianDialog.mount(
        overlay, dialog, () => done(false), no);
      else { document.body.append(overlay); no.focus(); }
    });
  }

  async function productSection() {
    const section = card("Каталог товаров", "Отдельный от NS Coins магазин. Цены вводятся в реальной валюте; оплата вручную.");
    const products = await api("/api/v1/store/products");
    const sku = input("Артикул, например nitro_1m", "", "text", 40);
    const name = input("Название товара", "", "text", 90);
    const description = textarea("Описание и условия", "", 600);
    const category = select("Категория", types, "digital");
    const price = input("Цена (например 399.00)", "", "text", 12);
    const currency = select("Валюта", currencies, "RUB");
    const stock = input("Остаток (-1: под заказ)", "-1", "number");
    stock.min = "-1"; stock.max = "1000000";
    const visible = input("Показывать в каталоге", "", "checkbox");
    visible.checked = true;
    const position = input("Позиция в каталоге", "0", "number");
    position.min = "-999"; position.max = "999";
    const controls = [sku, name, description, category, price, currency, stock, position];
    const save = button("Сохранить товар", async () => {
      try {
        const amount = Number(stock.value), sortOrder = Number(position.value);
        if (!Number.isInteger(amount) || !Number.isInteger(sortOrder)) {
          throw new Error("Остаток и позиция должны быть целыми.");
        }
        await api("/api/v1/store/products", {method:"POST", body: {
          sku:sku.value.trim(), title:name.value.trim(), description:description.value.trim(),
          category:category.value, price_minor:priceMinor(price.value.trim()),
          currency:currency.value, stock:amount === -1 ? null : amount,
          active:visible.checked, sort_order:sortOrder,
        }});
        notify("Товар сохранён");
        saved(controls); refresh();
      } catch (err) {fail(err);}
    }, "primary");
    section.append(form(track(sku), track(name), track(description), track(category),
      track(price), track(currency), track(stock), visible, track(position), save));
    const list = el("div", "staff-list");
    for (const item of products.items) {
      const entry = row(item.title + " · " + item.sku,
        money(item.price_minor, item.currency) + " · " + (item.active ? "На витрине" : "Скрыт") +
        " · Остаток: " + (item.stock === null ? "Под заказ" : item.stock));
      entry.append(button("Редактировать", () => {
        sku.value = item.sku; name.value = item.title; description.value = item.description;
        category.value = item.category; price.value = priceText(item.price_minor);
        currency.value = item.currency; stock.value = item.stock === null ? "-1" : item.stock;
        position.value = item.sort_order; visible.checked = item.active;
        name.focus();
      }));
      entry.append(button(item.active ? "Скрыть" : "Показать", async () => {
        try {
          await api("/api/v1/store/products", {method:"POST", body: {
            sku:item.sku, title:item.title, description:item.description,
            category:item.category, price_minor:item.price_minor, currency:item.currency,
            stock:item.stock, active:!item.active, sort_order:item.sort_order,
          }});
          refresh();
        } catch (err) {fail(err);}
      }));
      list.append(entry);
    }
    if (!products.items.length) list.append(el("p", "portal-muted",
      "Каталог пуст. Создайте первый товар — например подарочный Nitro или буст."));
    section.append(list);
    return section;
  }

  async function orderSection() {
    const section = card("Заказы покупателей",
      "Отметка покупателя «Я оплатил» НЕ подтверждает реальное получение денег. Проверяйте оплату вручную.");
    const filter = select("Фильтр заказов", statuses, "");
    const area = el("div", "staff-list");
    const count = el("p", "portal-muted");
    async function load() {
      const url = "/api/v1/store/orders" + (filter.value ? "?status=" + encodeURIComponent(filter.value) : "");
      const result = await api(url);
      area.replaceChildren();
      count.textContent = "Показано заказов: " + result.items.length;
      if (!result.items.length) area.append(el("p", "portal-muted", "Заказов нет."));
      for (const item of result.items) {
        const entry = row("#" + item.id + " · " + item.product_title + " × " + item.quantity,
          money(item.price_minor, item.currency) + " · " + captions[item.status] +
          " · Discord ID " + item.user_id);
        const details = button("История", async () => {
          try {
            const result = await api("/api/v1/store/orders/" + item.id);
            const timeline = el("div", "staff-list");
            for (const event of result.history) timeline.append(row(
              (captions[event.to] || event.to) + " · " + new Date(event.at * 1000).toLocaleString("ru-RU"),
              "Оператор Discord ID " + event.actor_id));
            entry.append(timeline);
          } catch (err) {fail(err);}
        });
        entry.append(details);
        const commands = [
          ["confirm_payment", "Подтвердить оплату", "success"],
          ["complete", "Выполнен", "primary"],
          ["cancel", "Отменить", "danger"],
        ];
        for (const [action, title, style] of commands) {
          if ((action === "complete" && item.status !== "processing") ||
              (action === "confirm_payment" && !["awaiting_payment", "payment_reported"].includes(item.status)) ||
              (action === "cancel" && ["completed", "cancelled"].includes(item.status))) continue;
          entry.append(button(title, async () => {
            if (!await ask("Заказ #" + item.id + ": " + title + "?")) return;
            try {
              await api("/api/v1/store/orders/" + item.id + "/action",
                {method:"POST",body:{action}});
              notify("Статус заказа обновлён"); await load();
            } catch (err) {fail(err);}
          }, style));
        }
        area.append(entry);
      }
    }
    filter.addEventListener("change", () => {void load().catch(fail);});
    const refreshButton = button("Обновить заказы", () => {void load().catch(fail);});
    section.append(form(filter, refreshButton), count, area);
    await load();
    return section;
  }

  async function settingsSection() {
    const settings = await api("/api/v1/store/settings");
    const section = card("Настройка магазина",
      "Приватный канал принимает новые заказы. Панель продаж можно опубликовать через /store panel.");
    const channel = track(input("Discord ID приватного канала заказов",
      settings.staff_channel_id, "text", 20));
    const payment = track(textarea("Инструкция по оплате для покупателей",
      settings.payment_instructions, 650));
    const saveButton = button("Сохранить настройки", async () => {
      try {
        await api("/api/v1/store/settings", {method:"POST", body:{
          staff_channel_id:channel.value.trim(), payment_instructions:payment.value.trim(),
        }});
        saved([channel,payment]); notify("Настройки магазина обновлены");
      } catch (err) {fail(err);}
    }, "primary");
    section.append(form(channel, payment, saveButton),
      el("p", "portal-muted", "Канал обязан быть скрыт от @everyone. "
        + "Перед публикацией убедитесь, что бот видит канал. Карточки и банковские реквизиты не сохраняются."));
    return section;
  }

  async function storePage() {
    const host = group();
    const analytics = await api("/api/v1/store/analytics");
    const metrics = el("div", "staff-kpis");
    for (const [status, label] of [["awaiting_payment", "Ожидают оплаты"],
      ["payment_reported", "Заявили оплату"], ["processing", "В работе"],
      ["completed", "Выполнены"]]) {
      metrics.append(add(el("div", "staff-kpi"),
        el("span", "", label.toUpperCase()), el("strong", "", analytics.counts[status] || 0)));
    }
    host.append(metrics);
    const totals = Object.entries(analytics.completed_minor_by_currency).map(
      ([currency, minor]) => money(minor, currency)).join(" · ") || "Нет завершённых заказов";
    host.append(el("p", "portal-muted", "Сумма выполненных заказов: " + totals +
      " · Не заменяет банковскую сверку."));
    host.append(await productSection(), await orderSection(), await settingsSection());
    return host;
  }

  window.StaffCenter.loaders["Магазин заказов"] = storePage;
  window.StaffCenter.descriptions["Магазин заказов"] =
    "Реальные товары, частные заказы, ручная оплата и история продавца.";
  window.StoreConsole = {hasDraft: changed};
})();
