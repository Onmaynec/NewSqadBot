"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = fs.readFileSync(
  path.join(__dirname, "..", "app", "web", "static", "app.js"), "utf8").split("/* BEGIN CONSOLE_SHELL_JS */")[1];

function harness({authorized = true, mobile = false, role = "owner", dirty = false} = {}) {
  const listeners = new Map();
  const nodes = new Map();
  const transitions = [];
  let mutations = 0;
  let activeElement = null;

  class Node {
    constructor(tag = "div", id = "") {
      this.tagName = tag.toUpperCase();
      this.id = id;
      this.children = [];
      this.handlers = new Map();
      this.observers = [];
      this.attrs = new Map();
      this.classes = new Set();
      this._hidden = false;
      this.isConnected = true;
      this.value = "";
      this.textContent = "";
      this.classList = {
        contains: (name) => this.classes.has(name),
        add: (name) => { this.classes.add(name); this.notify("class"); },
        remove: (name) => { this.classes.delete(name); this.notify("class"); },
      };
    }
    get hidden() { return this._hidden; }
    set hidden(value) { this._hidden = value; this.notify("hidden"); }
    getAttribute(key) { return this.attrs.get(key) ?? null; }
    setAttribute(key, value) { this.attrs.set(key, String(value)); this.notify(key); }
    removeAttribute(key) { this.attrs.delete(key); this.notify(key); }
    addEventListener(kind, callback) {
      const current = this.handlers.get(kind) || [];
      current.push(callback);
      this.handlers.set(kind, current);
    }
    removeEventListener(kind, callback) {
      this.handlers.set(kind, (this.handlers.get(kind) || []).filter(x => x !== callback));
    }
    fire(kind, event = {}) {
      for (const listener of this.handlers.get(kind) || []) listener(event);
    }
    notify(key) {
      mutations++;
      if (mutations > 70) throw new Error("MutationObserver feedback loop");
      for (const {callback, filters} of this.observers) {
        if (filters.includes(key)) callback();
      }
    }
    append(...children) {
      for (const child of children) {
        if (child.tagName === "#FRAGMENT") this.append(...child.children);
        else this.children.push(child);
      }
    }
    replaceChildren(...children) { this.children = []; this.append(...children); }
    querySelectorAll(query) {
      if (query === ".nav-item") return this.children.filter(x => x.classes.has("nav-item"));
      if (query === '[role="option"]') return this.children.filter(x => x.getAttribute("role") === "option");
      if (query === ".command-result") return this.children.filter(x => x.classes.has("command-result"));
      if (query.startsWith("button:not([disabled])")) {
        return this.children.filter(x => x.tagName === "BUTTON");
      }
      return [];
    }
    querySelector(query) {
      if (query === ".nav-text") return this.children.find(x => x.classes.has("nav-text")) || null;
      if (query === ".nav-svg") return this.children.find(x => x.classes.has("nav-svg")) || null;
      if (query === "h3") return this.children.find(x => x.tagName === "H3") || null;
      return null;
    }
    cloneNode() { return new Node(this.tagName.toLowerCase(), this.id); }
    scrollIntoView() {}
    getClientRects() { return this.hidden ? [] : [{}]; }
    contains(target) { return this === target || this.children.some(x => x.contains(target)); }
    remove() { this.isConnected = false; }
    focus() { activeElement = this; }
  }

  const ids = ["command-palette", "command-input", "command-results", "open-command",
    "close-command", "sidebar", "menu-toggle", "mobile-nav-backdrop", "shell",
    "obsidian-clock", "navigation"];
  ids.forEach(id => nodes.set(id, new Node("div", id)));
  nodes.get("command-palette").hidden = true;
  nodes.get("mobile-nav-backdrop").hidden = true;
  nodes.get("shell").hidden = !authorized;
  nodes.get("menu-toggle").attrs.set("aria-expanded", "false");
  const nav = nodes.get("navigation");
  for (const name of ["Обзор", "Роли", "Тикеты"]) {
    const item = new Node("button");
    item.classes.add("nav-item");
    const label = new Node("span");
    label.classes.add("nav-text");
    label.textContent = name;
    const icon = new Node("svg");
    icon.classes.add("nav-svg");
    item.append(label, icon);
    nav.append(item);
  }
  const body = new Node("body");
  const document = {
    body,
    get activeElement() { return activeElement; },
    getElementById(id) {
      if (nodes.has(id)) return nodes.get(id);
      return nodes.get("command-results").children.find(x => x.id === id) || null;
    },
    createElement: (tag) => new Node(tag),
    createDocumentFragment: () => new Node("#fragment"),
    querySelector: () => null,
    addEventListener(kind, callback) {
      listeners.set(kind, [...(listeners.get(kind) || []), callback]);
    },
  };
  const window = {
    matchMedia: () => ({matches: mobile}),
    addEventListener(kind, callback) {
      listeners.set("window:" + kind, [...(listeners.get("window:" + kind) || []), callback]);
    },
    setInterval() { return 1; },
    portalRole: () => role,
    portalData: () => ({settings: [
      {group: "Роли", key: "candidate_role_id", label: "Роль кандидата"},
    ]}),
    portalNavigate: async label => { transitions.push(["section", label]); return !dirty; },
    portalFindSetting: async (group, key) => {
      transitions.push(["setting", group, key]); return !dirty;
    },
  };
  class MutationObserver {
    constructor(callback) { this.callback = callback; }
    observe(node, options) {
      node.observers.push({callback: this.callback, filters: options.attributeFilter});
    }
  }
  vm.runInNewContext(script, {document, window, MutationObserver, Date, Intl,
    queueMicrotask: fn => fn()}, {filename: "obsidian.js", timeout: 1500});

  function key(key, props = {}) {
    const event = {key, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
      prevented: false, preventDefault() { this.prevented = true; },
      stopImmediatePropagation() {}, stopPropagation() {}, ...props};
    for (const callback of listeners.get("keydown") || []) callback(event);
    return event;
  }
  return {nodes, key, transitions, window, body, focus: () => activeElement,
    mutations: () => mutations, dirty: () => dirty};
}

test("Ctrl+K is ignored until authenticated and never intercepts the login form", () => {
  const ui = harness({authorized: false});
  const event = ui.key("k", {ctrlKey: true});
  assert.equal(event.prevented, false);
  assert.equal(ui.nodes.get("command-palette").hidden, true);
});

test("Ctrl+K opens the owner palette, searches settings and restores focus after Enter", () => {
  const ui = harness();
  const trigger = ui.nodes.get("open-command");
  trigger.focus();
  const open = ui.key("k", {ctrlKey: true});
  assert.equal(open.prevented, true);
  assert.equal(ui.nodes.get("command-palette").hidden, false);
  assert.equal(ui.focus(), ui.nodes.get("command-input"));

  const search = ui.nodes.get("command-input");
  search.value = "candidate_role_id";
  search.fire("input");
  const results = ui.nodes.get("command-results");
  assert.equal(results.children.length, 1);
  assert.equal(results.children[0].getAttribute("aria-selected"), "true");
  ui.key("Enter");
  assert.deepEqual(ui.transitions, [["setting", "Роли", "candidate_role_id"]]);
  assert.equal(ui.nodes.get("command-palette").hidden, true);
  assert.equal(ui.focus(), trigger);
});

test("role-limited palette does not expose owner-only settings", () => {
  const ui = harness({role: "moder"});
  ui.key("k", {metaKey: true});
  const search = ui.nodes.get("command-input");
  search.value = "candidate_role_id";
  search.fire("input");
  assert.equal(ui.nodes.get("command-results").children.length, 1);
  assert.equal(ui.nodes.get("command-results").children[0].textContent,
    "Нет доступных разделов по этому запросу.");
  ui.key("Escape");
  assert.equal(ui.nodes.get("command-palette").hidden, true);
});

test("keyboard navigation does not mutate pending settings and respects rejected navigation", () => {
  const ui = harness({dirty: true});
  const trigger = ui.nodes.get("open-command");
  trigger.focus();
  ui.key("k", {ctrlKey: true});
  ui.key("ArrowDown");
  ui.key("Enter");
  assert.deepEqual(ui.transitions, [["section", "Роли"]]);
  assert.equal(ui.dirty(), true);
  assert.equal(ui.focus(), trigger);
  assert.equal(ui.nodes.get("command-palette").hidden, true);
});

test("repeated mobile sidebar and auth transitions never restart a mutation loop", () => {
  const ui = harness({mobile: true});
  for (let n = 0; n < 3; n++) {
    ui.nodes.get("sidebar").classList.add("open");
    ui.nodes.get("sidebar").classList.remove("open");
  }
  ui.nodes.get("shell").hidden = true;
  assert.equal(ui.nodes.get("menu-toggle").getAttribute("aria-expanded"), "false");
  assert.equal(ui.nodes.get("mobile-nav-backdrop").hidden, true);
  assert.ok(ui.mutations() < 70);
});

test("operational modal traps focus, cancels on Escape and restores focus on cleanup", () => {
  const ui = harness();
  const before = ui.nodes.get("open-command");
  before.focus();
  const layer = new (ui.body.constructor)("div");
  const dialog = new (ui.body.constructor)("div");
  const title = new (ui.body.constructor)("h3");
  const first = new (ui.body.constructor)("button");
  const last = new (ui.body.constructor)("button");
  dialog.append(title, first, last);
  let cancelled = 0;
  const cleanup = ui.window.ObsidianDialog.mount(layer, dialog, () => cancelled++);
  assert.equal(ui.focus(), first);
  last.focus();
  const tab = {key: "Tab", shiftKey: false, preventDefault() { this.prevented = true; }};
  layer.fire("keydown", tab);
  assert.equal(tab.prevented, true);
  assert.equal(ui.focus(), first);
  layer.fire("keydown", {key: "Escape", preventDefault() {}, stopPropagation() {}});
  assert.equal(cancelled, 1);
  cleanup();
  cleanup();
  assert.equal(ui.focus(), before);
});
