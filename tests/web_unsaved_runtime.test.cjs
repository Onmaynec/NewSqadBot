"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const pathToApp = path.join(__dirname, "..", "app", "web", "static", "app.js");
const app = fs.readFileSync(pathToApp, "utf8");
// Do not call the async site bootstrap: these tests exercise the actual
// unsaved-change navigation code in isolation, not a fake reimplementation.
assert.match(app, /void initialize\(\);\s*$/);
const script = app.replace(/void initialize\(\);\s*$/,
  "window.__test = {ui, confirmDiscard, navigate};");

function launch() {
  const nodes = new Map();
  const listeners = new Map();
  let focused = null;
  class Node {
    constructor() {
      this.hidden = true;
      this.textContent = "";
      this.value = "";
      this.isConnected = true;
      this.listeners = new Map();
      this.classList = {add() {}, remove() {}, toggle() {}};
    }
    addEventListener(name, handler) {
      this.listeners.set(name, handler);
    }
    removeEventListener(name, handler) {
      if (this.listeners.get(name) === handler) this.listeners.delete(name);
    }
    focus() { focused = this; }
    replaceChildren() {}
  }
  const document = {
    get activeElement() { return focused; },
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, new Node());
      return nodes.get(id);
    },
    createElement() { return new Node(); },
    addEventListener(name, handler) { listeners.set(name, handler); },
  };
  const window = {
    addEventListener(name, handler) { listeners.set(name, handler); },
    scrollTo() {},
    Portal: {hasDraft: () => false},
  };
  vm.runInNewContext(script, {document, window, setTimeout: () => 1,
    clearTimeout: () => {}, AbortController, JSON, Map, Set, URL},
  {filename: "app.js", timeout: 1500});
  return {window, nodes, listeners, focus: () => focused,
    node: id => document.getElementById(id)};
}

test("cancelled navigation preserves pending setting values, current group and focus", async () => {
  const view = launch();
  const {ui, navigate} = view.window.__test;
  const source = view.node("open-command");
  source.focus();
  ui.dirty.set("candidate_role_id", "123456789012345678");
  const pending = navigate("Роли");
  assert.equal(view.node("leave-dialog").hidden, false);
  assert.equal(view.node("dialog-title").textContent, "Несохранённые изменения");
  view.node("stay-here").onclick();
  assert.equal(await pending, false);
  assert.equal(ui.group, "Обзор");
  assert.equal(ui.dirty.get("candidate_role_id"), "123456789012345678");
  assert.equal(view.node("leave-dialog").hidden, true);
  assert.equal(view.focus(), source);
});

test("confirmed navigation discards only after explicit confirmation", async () => {
  const view = launch();
  const {ui, navigate} = view.window.__test;
  ui.dirty.set("candidate_role_id", "42");
  const pending = navigate("Роли");
  assert.equal(ui.dirty.size, 1);
  view.node("leave-anyway").onclick();
  assert.equal(await pending, true);
  assert.equal(ui.group, "Роли");
  assert.equal(ui.dirty.size, 0);
  assert.equal(view.node("leave-dialog").hidden, true);
});

test("navigation while saving is rejected without opening a second dialog", async () => {
  const view = launch();
  const {ui, navigate} = view.window.__test;
  ui.saving = true;
  ui.dirty.set("voice_enabled", false);
  assert.equal(await navigate("Роли"), false);
  assert.equal(ui.group, "Обзор");
  assert.equal(ui.dirty.get("voice_enabled"), false);
  assert.equal(view.node("leave-dialog").hidden, true);
});

test("browser unload is blocked for unsaved settings and in-flight saves", () => {
  const view = launch();
  const {ui} = view.window.__test;
  const unload = view.listeners.get("beforeunload");
  const event = () => ({prevented: false, preventDefault() {this.prevented = true;}});
  const clean = event();
  unload(clean);
  assert.equal(clean.prevented, false);
  ui.dirty.set("module_enabled", true);
  const modified = event();
  unload(modified);
  assert.equal(modified.prevented, true);
  assert.equal(modified.returnValue, "");
  ui.dirty.clear();
  ui.saving = true;
  const saving = event();
  unload(saving);
  assert.equal(saving.prevented, true);
});
