import { expect, test } from '@playwright/test'

/** Smoke check against the real dashboard; this spec never launches a server. */
test('loads the dashboard', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page).toHaveTitle(/Hermes/i)
})
