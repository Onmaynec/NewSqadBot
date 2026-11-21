"use strict";

/* Obsidian interaction layer.
 * All command options are built from the server-authorized navigation already rendered by app.js.
 * This script never requests extra permissions or caches private Discord data. */
(() => {
  const byId = (id) => document.getElementById(id);
  const modal = byId("command-palette");
  const search = byId("command-input");
  const results = byId("command-results");
  const trigger = byId("open-command");
  const closeButton = byId("close-command");
  const sidebar = byId("sidebar");
  const menu = byId("menu-toggle");
  const backdrop = byId("mobile-nav-backdrop");
  if (!modal || !search || !results || !trigger || !closeButton || !sidebar) return;

  let entries = [];
  let filtered = [];
  let selected = 0;
  let restoreFocus = null;

  const normalize = (value) => String(value || "").trim().toLocaleLowerCase("ru-RU");
  const isOpen = () => !modal.hidden;
  const isAuthorized = () => !byId("shell").hidden;

  function availableEntries() {
    // The existing app constructs this list using the authenticated Discord role.
    // Restricted destinations are therefore not added to the search UI.
    const sections = Array.from(byId("navigation").querySelectorAll(".nav-item")).map((button) => ({
      label: button.querySelector(".nav-text")?.textContent?.trim() || "",
      searchText: button.querySelector(".nav-text")?.textContent?.trim() || "",
      icon: button.querySelector(".nav-svg"),
      kind: "section",
    })).filter((item) => item.label);
    if (window.portalRole?.() !== "owner") return sections;
    const data = window.portalData?.();
    const allowed = new Set(sections.map((item) => item.label));
    const settings = (data?.settings || [])
      .filter((spec) => allowed.has(spec.group))
      .map((spec) => ({
        label: spec.label,
        searchText: spec.label + " " + spec.key + " " + spec.group,
        group: spec.group,
        key: spec.key,
        kind: "setting",
        icon: null,
      }));
    return sections.concat(settings);
  }

  function highlight(index) {
    if (!filtered.length) {
      search.removeAttribute("aria-activedescendant");
      return;
    }
    selected = Math.max(0, Math.min(index, filtered.length - 1));
    for (const [i, option] of Array.from(results.querySelectorAll('[role="option"]')).entries()) {
      option.setAttribute("aria-selected", String(i === selected));
    }
    const active = byId("command-option-" + selected);
    if (active) {
      search.setAttribute("aria-activedescendant", active.id);
      active.scrollIntoView({block: "nearest"});
    }
  }

  function close(shouldRestoreFocus = true) {
    if (modal.hidden) return;
    modal.hidden = true;
    document.body.classList.remove("command-is-open");
    search.value = "";
    search.removeAttribute("aria-activedescendant");
    if (shouldRestoreFocus && restoreFocus?.isConnected) restoreFocus.focus();
    restoreFocus = null;
  }

  function goTo(index) {
    const choice = filtered[index];
    if (!choice) return;
    // Return focus to the palette opener before an unsaved-changes dialog appears.
    // Otherwise cancelling navigation may leave keyboard focus on a hidden input.
    close();
    if (choice.kind === "setting" && typeof window.portalFindSetting === "function") {
      void window.portalFindSetting(choice.group, choice.key);
    } else if (typeof window.portalNavigate === "function") {
      void window.portalNavigate(choice.label);
    }
    // Navigation may show the existing unsaved-changes confirmation.
  }

  function render() {
    const query = normalize(search.value);
    const matches = entries.filter((item) => (query || item.kind === "section")
      && normalize(item.searchText).includes(query));
    filtered = matches.sort((a, b) => {
      const aStart = normalize(a.label).startsWith(query) ? 0 : 1;
      const bStart = normalize(b.label).startsWith(query) ? 0 : 1;
      if (aStart !== bStart) return aStart - bStart;
      return a.kind === b.kind ? 0 : (a.kind === "section" ? -1 : 1);
    }).slice(0, 45);
    selected = 0;
    results.replaceChildren();
    if (!filtered.length) {
      const empty = document.createElement("div");
      empty.className = "command-empty";
      empty.textContent = "Нет доступных разделов по этому запросу.";
      results.append(empty);
      search.removeAttribute("aria-activedescendant");
      return;
    }
    const fragment = document.createDocumentFragment();
    filtered.forEach((item, index) => {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "command-result";
      option.id = "command-option-" + index;
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", String(index === 0));

      const icon = item.icon?.cloneNode(true) || document.createElement("span");
      icon.classList.add("nav-svg");
      icon.setAttribute("aria-hidden", "true");
      if (!item.icon) icon.textContent = "⌘";
      option.append(icon);
      const label = document.createElement("span");
      label.textContent = item.label;
      option.append(label);
      if (item.kind === "setting") {
        const hint = document.createElement("small");
        hint.className = "command-kind";
        hint.textContent = item.group;
        option.append(hint);
      }
      const arrow = document.createElement("span");
      arrow.className = "command-arrow";
      arrow.textContent = "↗";
      arrow.setAttribute("aria-hidden", "true");
      option.append(arrow);
      option.addEventListener("mouseenter", () => highlight(index));
      option.addEventListener("click", () => goTo(index));
      fragment.append(option);
    });
    results.append(fragment);
    highlight(0);
  }

  function open() {
    if (!isAuthorized()) return;
    entries = availableEntries();
    if (!entries.length) return;
    restoreFocus = document.activeElement;
    modal.hidden = false;
    document.body.classList.add("command-is-open");
    search.value = "";
    render();
    search.focus();
  }

  trigger.addEventListener("click", open);
  closeButton.addEventListener("click", () => close());
  search.addEventListener("input", render);
  modal.addEventListener("pointerdown", (event) => {
    if (event.target === modal) close();
  });

  document.addEventListener("keydown", (event) => {
    // Do not intercept Ctrl+K on the login form.
    if ((event.ctrlKey || event.metaKey) && !event.altKey && normalize(event.key) === "k") {
      if (!isAuthorized()) return;
      event.preventDefault();
      isOpen() ? close() : open();
      return;
    }
    if (!isOpen()) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopImmediatePropagation();
      close();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      highlight((selected + 1) % Math.max(filtered.length, 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      highlight((selected + Math.max(filtered.length, 1) - 1) % Math.max(filtered.length, 1));
    } else if (event.key === "Enter" && filtered.length) {
      event.preventDefault();
      goTo(selected);
    } else if (event.key === "Tab") {
      // Keep focus inside the dialog when tabbing from the search field.
      const focusables = [search, closeButton,
        ...Array.from(results.querySelectorAll(".command-result"))];
      const current = focusables.indexOf(document.activeElement);
      if (current === -1) {
        event.preventDefault();
        search.focus();
      } else if (event.shiftKey && current === 0) {
        event.preventDefault();
        focusables[focusables.length - 1].focus();
      } else if (!event.shiftKey && current === focusables.length - 1) {
        event.preventDefault();
        search.focus();
      }
    }
  }, true);

  function syncMobileNavigation() {
    const mobile = window.matchMedia("(max-width: 900px)").matches;
    const authorized = isAuthorized();
    // Do not write the observed 'class' attribute if nothing changed:
    // DOMTokenList.remove() emits a mutation even when the class is absent.
    // Unconditional removal inside a MutationObserver callback caused a
    // self-sustaining microtask loop and froze the entire browser tab.
    if ((!mobile || !authorized) && sidebar.classList.contains("open")) {
      sidebar.classList.remove("open");
    }
    if (!authorized && isOpen()) close(false);
    const opened = mobile && authorized && sidebar.classList.contains("open");
    if (backdrop.hidden !== !opened) backdrop.hidden = !opened;
    if (menu.getAttribute("aria-expanded") !== String(opened)) {
      menu.setAttribute("aria-expanded", String(opened));
    }
  }
  backdrop.addEventListener("click", () => {
    sidebar.classList.remove("open");
    syncMobileNavigation();
    menu.focus();
  });
  menu.addEventListener("click", syncMobileNavigation);
  sidebar.addEventListener("click", (event) => {
    if (event.target.closest(".nav-item")) {
      // navigate() closes the sidebar only after confirming pending edits.
      queueMicrotask(syncMobileNavigation);
    }
  });
  const observer = new MutationObserver(syncMobileNavigation);
  observer.observe(sidebar, {attributes: true, attributeFilter: ["class"]});
  observer.observe(byId("shell"), {attributes: true, attributeFilter: ["hidden"]});
  window.addEventListener("resize", syncMobileNavigation);
  syncMobileNavigation();

  function updateClock() {
    const output = byId("obsidian-clock");
    if (!output) return;
    output.textContent = new Intl.DateTimeFormat("ru-RU", {
      day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
    }).format(new Date());
  }
  updateClock();
  window.setInterval(updateClock, 60_000);

  // Shared keyboard/focus management for operational confirmation and form dialogs.
  let dialogIndex = 0;
  window.ObsidianDialog = {
    mount(layer, dialog, onCancel, preferred) {
      const previous = document.activeElement;
      const heading = dialog.querySelector("h3");
      if (heading) {
        heading.id ||= "ob-dialog-title-" + (++dialogIndex);
        dialog.setAttribute("aria-labelledby", heading.id);
      }
      dialog.tabIndex = -1;
      let removed = false;
      const focusables = () => Array.from(dialog.querySelectorAll(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]),'
        + ' textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )).filter((element) => element.getClientRects().length > 0);
      function onKeydown(event) {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          onCancel();
          return;
        }
        if (event.key !== "Tab") return;
        const list = focusables();
        if (!list.length) {
          event.preventDefault();
          dialog.focus();
          return;
        }
        const current = document.activeElement;
        if (event.shiftKey && (current === list[0] || !dialog.contains(current))) {
          event.preventDefault();
          list[list.length - 1].focus();
        } else if (!event.shiftKey && (current === list[list.length - 1] || !dialog.contains(current))) {
          event.preventDefault();
          list[0].focus();
        }
      }
      function onBackdrop(event) {
        if (event.target === layer) onCancel();
      }
      layer.addEventListener("keydown", onKeydown);
      layer.addEventListener("pointerdown", onBackdrop);
      document.body.append(layer);
      document.body.classList.add("ob-modal-open");
      (preferred || focusables()[0] || dialog).focus();
      return () => {
        if (removed) return;
        removed = true;
        layer.removeEventListener("keydown", onKeydown);
        layer.removeEventListener("pointerdown", onBackdrop);
        layer.remove();
        if (!document.querySelector(".portal-modal-overlay, .dialog-backdrop:not([hidden])")) {
          document.body.classList.remove("ob-modal-open");
        }
        if (previous?.isConnected) previous.focus();
      };
    }
  };
})();
