"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(
  path.join(__dirname, "..", "app", "web", "static", "obsidian.js"), "utf8");

function launch({mobile = false, authorized = false} = {}) {
  let mutations = 0;
  const listeners = new Map();

  class Node {
    constructor(id, hidden = false) {
      this.id = id;
      this._hidden = hidden;
      this.attrs = new Map();
      this.observers = [];
      this.classes = new Set(id === "sidebar" ? ["sidebar"] : []);
      this.handlers = new Map();
      this.classList = {
        contains: (name) => this.classes.has(name),
        add: (name) => { this.classes.add(name); this.notify("class"); },
        remove: (name) => {
          this.classes.delete(name);
          // Chrome fires an attribute mutation even if the token was missing.
          this.notify("class");
        },
      };
    }

    get hidden() { return this._hidden; }
    set hidden(value) {
      this._hidden = value;
      this.notify("hidden");
    }
    getAttribute(name) { return this.attrs.get(name) ?? null; }
    setAttribute(name, value) {
      this.attrs.set(name, String(value));
      this.notify(name);
    }
    removeAttribute(name) { this.attrs.delete(name); this.notify(name); }
    addEventListener(name, callback) { this.handlers.set(name, callback); }
    notify(name) {
      mutations += 1;
      if (mutations > 40) throw new Error("Infinite MutationObserver recursion");
      for (const {callback, attributeFilter} of this.observers) {
        if (attributeFilter.includes(name)) callback();
      }
    }
    focus() {}
    querySelectorAll() { return []; }
  }

  const ids = [
    "command-palette", "command-input", "command-results", "open-command",
    "close-command", "sidebar", "menu-toggle", "mobile-nav-backdrop",
    "shell", "obsidian-clock", "navigation",
  ];
  const elements = Object.fromEntries(ids.map(id => [id, new Node(id,
    id === "command-palette" || id === "mobile-nav-backdrop" ||
    id === "shell" && !authorized)]));
  elements["menu-toggle"].attrs.set("aria-expanded", "false");

  class Observer {
    constructor(callback) { this.callback = callback; }
    observe(target, options) {
      target.observers.push({callback: this.callback, attributeFilter: options.attributeFilter});
    }
  }

  const body = new Node("body");
  const document = {
    body,
    getElementById(id) { return elements[id] || null; },
    addEventListener(name, callback) { listeners.set(name, callback); },
    querySelector() { return null; },
  };
  const window = {
    matchMedia() { return {matches: mobile}; },
    addEventListener(name, callback) { listeners.set(name, callback); },
    setInterval() { return 1; },
  };
  vm.runInNewContext(source, {document, window, MutationObserver: Observer, Intl, Date,
    queueMicrotask: (fn) => fn()}, {filename: "obsidian.js", timeout: 1000});
  return {elements, mutations: () => mutations, body};
}

test("desktop: opening script on signed-out view does not enter a mutation loop", () => {
  const app = launch();
  assert.ok(app.mutations() <= 2);
  assert.equal(app.elements["menu-toggle"].getAttribute("aria-expanded"), "false");
  assert.equal(app.elements["mobile-nav-backdrop"].hidden, true);
});

test("desktop: unexpected sidebar.open closes once without feedback loop", () => {
  const app = launch({mobile: false, authorized: true});
  app.elements.sidebar.classList.add("open");
  assert.equal(app.elements.sidebar.classList.contains("open"), false);
  assert.ok(app.mutations() < 8);
});

test("mobile: opening menu and signing out settles without loop", () => {
  const app = launch({mobile: true, authorized: false});
  const {sidebar, shell} = app.elements;
  shell.hidden = false;
  sidebar.classList.add("open");
  assert.equal(app.elements["menu-toggle"].getAttribute("aria-expanded"), "true");
  assert.equal(app.elements["mobile-nav-backdrop"].hidden, false);
  shell.hidden = true;
  assert.equal(sidebar.classList.contains("open"), false);
  assert.equal(app.elements["menu-toggle"].getAttribute("aria-expanded"), "false");
  assert.equal(app.elements["mobile-nav-backdrop"].hidden, true);
  assert.ok(app.mutations() < 15);
});

test("login page uses Obsidian workspace copy instead of editorial hero", () => {
  const html = fs.readFileSync(
    path.join(__dirname, "..", "app", "web", "static", "index.html"), "utf8");
  const css = fs.readFileSync(
    path.join(__dirname, "..", "app", "web", "static", "obsidian.css"), "utf8");
  assert.match(html, /Управление <span>NewSquad[.]<\/span>/);
  assert.doesNotMatch(html, /Твой сервер[.]/);
  assert.match(css, /AUTH \/ OBSIDIAN/);
  assert.match(css, /grid-template-columns: 252px minmax/);
});
