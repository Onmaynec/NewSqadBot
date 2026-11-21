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
