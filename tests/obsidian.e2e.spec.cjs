"use strict";

const {test, expect} = require("@playwright/test");

async function login(page) {
  await page.goto("/");
  await expect(page.locator("#login-screen")).toBeVisible();
  await page.locator("#password").fill("Local test password 2026!");
  await page.locator("#login-submit").click();
  await expect(page.locator("#shell")).toBeVisible();
  await expect(page.locator("#navigation .nav-item").first()).toBeVisible();
}

test("real Chromium: Ctrl+K opens palette, searches and navigates by keyboard", async ({page}) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("response", response => {
    if (response.url().startsWith("http://127.0.0.1:9876/api/") && response.status() >= 500)
      errors.push("HTTP " + response.status() + " " + response.url());
  });
  await login(page);
  await page.keyboard.press("Control+k");
  await expect(page.locator("#command-palette")).toBeVisible();
  await page.locator("#command-input").fill("Роли");
  await expect(page.locator("#command-results .command-result").first()).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.locator("#command-palette")).toBeHidden();
  await expect(page.locator("#breadcrumb")).toHaveText("Роли");
  expect(errors).toEqual([]);
});

test("real Chromium: unsaved settings survive cancelled navigation", async ({page}) => {
  await login(page);
  await page.locator('#navigation .nav-item[aria-label="Голосовые"]').click();
  await expect(page.locator("#settings-section")).toBeVisible();
  const checkbox = page.locator('#settings-grid input[type="checkbox"]').first();
  await expect(checkbox).toBeVisible();
  await checkbox.click();
  await expect(page.locator("#bulk-save")).toBeVisible();

  await page.keyboard.press("Control+k");
  await page.locator("#command-input").fill("Роли");
  await page.keyboard.press("Enter");
  await expect(page.locator("#leave-dialog")).toBeVisible();
  await page.locator("#stay-here").click();
  await expect(page.locator("#breadcrumb")).toHaveText("Голосовые");
  await expect(page.locator("#bulk-save")).toBeVisible();
  await page.locator('#navigation .nav-item[aria-label="Роли"]').click();
  await page.locator("#leave-anyway").click();
  await expect(page.locator("#breadcrumb")).toHaveText("Роли");
  await expect(page.locator("#bulk-save")).toBeHidden();
});

test("real Chromium mobile: menu and palette do not hang after toggles", async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  await login(page);
  for (let i = 0; i < 3; i++) {
    await page.locator("#menu-toggle").click();
    await expect(page.locator("#mobile-nav-backdrop")).toBeVisible();
    await page.locator("#mobile-nav-backdrop").click();
    await expect(page.locator("#mobile-nav-backdrop")).toBeHidden();
  }
  await page.keyboard.press("Control+k");
  await expect(page.locator("#command-palette")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator("#command-palette")).toBeHidden();
});


test("real Chromium: local password Staff Center inbox, tasks and templates", async ({page}) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("response", response => {
    if (response.url().includes("/api/") && response.status() >= 500)
      errors.push("HTTP " + response.status() + " " + response.url());
  });
  await login(page);
  await page.locator('#navigation .nav-item[aria-label="Единая очередь"]').click();
  await expect(page.locator("#breadcrumb")).toHaveText("Единая очередь");
  await expect(page.locator("#portal-section .staff-kpis")).toBeVisible();
  await expect(page.locator('#portal-section select[aria-label="Тип обращений"]')).toBeVisible();

  await page.locator('#navigation .nav-item[aria-label="Мои задачи"]').click();
  await expect(page.locator("#portal-section .staff-card").first()).toBeVisible();
  await page.locator('#portal-section input[aria-label="Заголовок"]').fill("Проверить тестовую заявку");
  await page.locator('#portal-section textarea[aria-label="Описание задачи"]').fill("Проверить перед одобрением");
  await page.locator("#portal-section").getByRole("button", {name: "Добавить задачу"}).click();
  await expect(page.locator("#portal-section")).toContainText("Проверить тестовую заявку");

  await page.locator('#navigation .nav-item[aria-label="Шаблоны ответов"]').click();
  await expect(page.locator("#portal-section")).toContainText("Шаблоны ответов");
  await page.locator('#portal-section input[aria-label="Название шаблона"]').fill("Ответ администратора");
  await page.locator('#portal-section textarea[aria-label="Текст шаблона"]').fill("Здравствуйте, ваша заявка на рассмотрении.");
  await page.locator("#portal-section").getByRole("button", {name: "Сохранить шаблон"}).click();
  await expect(page.locator("#portal-section")).toContainText("Ответ администратора");
  expect(errors).toEqual([]);
});

test("real Chromium: local-only authentication removes OAuth entry points", async ({page}) => {
  await page.goto("/");
  await expect(page.locator("#login-form")).toBeVisible();
  await expect(page.locator("#discord-login")).toHaveCount(0);
  const oauth = await page.request.get("/auth/discord");
  const callback = await page.request.get("/auth/callback");
  const protectedApi = await page.request.get("/api/staff/inbox");
  expect(oauth.status()).toBe(404);
  expect(callback.status()).toBe(404);
  expect(protectedApi.status()).toBe(401);
});
