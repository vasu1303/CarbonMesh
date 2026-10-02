import { expect, test } from '@playwright/test'
import { fixtureFor, id, mockDashboard } from './dashboard-fixtures'

const accessKey = 'browser-test-only-not-a-real-access-key'
const principal = {
  company_id: id(1),
  actor_id: id(4),
  role: 'sustainability_analyst',
}
const unauthorized = {
  detail: {
    code: 'invalid_credentials',
    message: 'The supplied access key is invalid.',
    trace_id: null,
  },
}

test('sign-in gates data, rejects invalid keys, signs out and clears account data', async ({
  page,
}) => {
  let authenticated = false
  let empty = false
  const calls = await mockDashboard(page, async (route, url) => {
    if (url.pathname !== '/api/auth/session') {
      if (empty && url.pathname !== '/api/health') {
        await route.fulfill({ json: fixtureFor(url, true) })
        return true
      }
      return
    }
    if (route.request().method() === 'DELETE') {
      authenticated = false
      await route.fulfill({ status: 204 })
    } else if (route.request().method() === 'POST') {
      authenticated = route.request().postDataJSON().access_key === accessKey
      await route.fulfill(
        authenticated
          ? { json: { principal, access_token: 'discard-this-test-token' } }
          : { status: 401, json: unauthorized },
      )
    } else {
      await route.fulfill(
        authenticated
          ? { json: principal }
          : { status: 401, json: unauthorized },
      )
    }
    return true
  })
  await page.goto('/dashboard')
  await expect(
    page.getByRole('heading', { name: 'Sign in to CarbonMesh' }),
  ).toBeVisible()
  expect(
    calls.every(({ url }) =>
      ['/api/health', '/api/auth/session'].includes(url.pathname),
    ),
  ).toBe(true)
  await page.getByLabel('Access key', { exact: true }).fill('invalid-test-key')
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByRole('alert')).toHaveText(unauthorized.detail.message)
  await page.getByLabel('Access key', { exact: true }).fill(accessKey)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(
    page.getByRole('button', { name: '12,500.125', exact: true }),
  ).toBeVisible()
  expect(
    await page.evaluate(() =>
      JSON.stringify({ ...localStorage, ...sessionStorage }),
    ),
  ).not.toMatch(/browser-test-only|discard-this-test-token/)
  await page.getByRole('button', { name: 'Sign out', exact: true }).click()
  await expect(
    page.getByRole('heading', { name: 'Sign in to CarbonMesh' }),
  ).toBeVisible()
  await expect(page.getByText('12,500.125', { exact: true })).toHaveCount(0)
  empty = true
  await page.getByLabel('Access key', { exact: true }).fill(accessKey)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(
    page.getByText('No verified result', { exact: true }),
  ).toBeVisible()
  await expect(page.getByText('12,500.125', { exact: true })).toHaveCount(0)
})

test('expired API session removes cached workspace and permits fresh sign-in', async ({
  page,
}) => {
  let expired = false
  await mockDashboard(page, async (route, url) => {
    if (
      url.pathname === '/api/auth/session' &&
      route.request().method() === 'POST'
    ) {
      expired = false
      await route.fulfill({ json: { principal } })
      return true
    }
    if (!expired || url.pathname === '/api/health') return
    await route.fulfill({ status: 401, json: unauthorized })
    return true
  })
  await page.goto('/dashboard')
  await expect(
    page.getByRole('button', { name: '12,500.125', exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Refresh data', exact: true }),
  ).toBeEnabled()
  expired = true
  await page.getByRole('button', { name: 'Refresh data', exact: true }).click()
  await expect(
    page.getByRole('heading', { name: 'Sign in to CarbonMesh' }),
  ).toBeVisible()
  await expect(page.getByText('12,500.125', { exact: true })).toHaveCount(0)
  await page.getByLabel('Access key', { exact: true }).fill(accessKey)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(
    page.getByRole('button', { name: '12,500.125', exact: true }),
  ).toBeVisible()
})

test('another tenant cannot mount workspace queries', async ({ page }) => {
  const calls = await mockDashboard(page, async (route, url) => {
    if (url.pathname !== '/api/auth/session') return
    await route.fulfill({ json: { ...principal, company_id: id(999) } })
    return true
  })
  await page.goto('/measurement')
  await expect(page.getByRole('alert')).toContainText(
    'does not match the configured workspace',
  )
  expect(
    calls.every(({ url }) =>
      ['/api/health', '/api/auth/session'].includes(url.pathname),
    ),
  ).toBe(true)
})
