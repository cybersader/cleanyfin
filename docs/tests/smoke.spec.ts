import { test, expect } from '@playwright/test';

const BASE = '/cleanyfin';

test.describe('Smoke', () => {
  test('landing page loads with the right title', async ({ page }) => {
    await page.goto(`${BASE}/`);
    await expect(page).toHaveTitle(/cleanyfin/i);
  });

  test('start-here page renders', async ({ page }) => {
    await page.goto(`${BASE}/start-here/`);
    await expect(page.locator('h1')).toBeVisible();
  });

  test('a design page renders with sidebar', async ({ page }) => {
    await page.goto(`${BASE}/design/architecture/`);
    const sidebar = page.locator('#starlight__sidebar, aside nav, nav ul');
    await expect(sidebar.first()).toBeVisible();
    await expect(page.locator('h1')).toBeVisible();
  });

  test('a research deep-dive renders', async ({ page }) => {
    await page.goto(`${BASE}/research/legal/`);
    await expect(page.locator('h1')).toBeVisible();
  });

  test('search button is present', async ({ page }) => {
    await page.goto(`${BASE}/`);
    const searchButton = page.locator('site-search button, button[data-open-modal]');
    await expect(searchButton.first()).toBeVisible();
  });
});
