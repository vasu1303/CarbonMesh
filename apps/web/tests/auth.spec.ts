import { expect, test } from '@playwright/test'
import { mockDashboard } from './dashboard-fixtures'

test('workspace opens directly without sign-in UI or authentication requests', async ({ page }) => {
  const calls = await mockDashboard(page)
  await page.goto('/dashboard')
  await expect(page.getByRole('button', { name: '12,500.125', exact: true })).toBeVisible()
  await expect(page.getByLabel('Access key', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Sign in|Sign out/i })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: /Sign in/i })).toHaveCount(0)
  expect(calls.some(({ url }) => url.pathname.startsWith('/api/auth/'))).toBe(false)
})

for (const status of [401, 403]) {
  test(`API ${status} remains a visible data error without starting authentication`, async ({ page }) => {
    const calls = await mockDashboard(page, async (route, url) => {
      if (url.pathname !== '/api/measurements') return
      await route.fulfill({ status, json: { detail: { code: 'access_denied', message: 'This data is unavailable to the workspace.', retryable: false } } })
      return true
    })
    await page.goto('/dashboard')
    await expect(page.getByRole('region', { name: 'Verified measurements', exact: true })).toContainText('This data is unavailable to the workspace.')
    await expect(page.getByRole('heading', { name: 'Overview', exact: true })).toBeVisible()
    await expect(page.getByText('12,500.125', { exact: true })).toHaveCount(0)
    await expect(page.getByRole('heading', { name: /Sign in/i })).toHaveCount(0)
    expect(calls.some(({ url }) => url.pathname.startsWith('/api/auth/'))).toBe(false)
  })
}

test('denied refresh preserves the last result with a warning and no sign-in redirect', async ({ page }) => {
  let denied = false
  const calls = await mockDashboard(page, async (route, url) => {
    if (!denied || url.pathname !== '/api/measurements') return
    await route.fulfill({ status: 403, json: { detail: { code: 'access_denied', message: 'Workspace access denied.', retryable: false } } })
    return true
  })
  await page.goto('/dashboard')
  await expect(page.getByRole('button', { name: '12,500.125', exact: true })).toBeVisible()
  denied = true
  await page.getByRole('button', { name: 'Refresh dashboard', exact: true }).click()
  const measurements = page.getByRole('region', { name: 'Verified measurements', exact: true })
  await expect(measurements).toContainText('Refresh failed. Showing previously fetched data.')
  await expect(measurements.getByText('12,500.125', { exact: true })).toBeVisible()
  await expect(page).toHaveURL(/\/dashboard$/)
  await expect(page.getByLabel('Access key', { exact: true })).toHaveCount(0)
  expect(calls.some(({ url }) => url.pathname.startsWith('/api/auth/'))).toBe(false)
})
