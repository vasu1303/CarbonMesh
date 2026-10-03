import { expect, test } from '@playwright/test'
import { id } from './dashboard-fixtures'
import {
  catalogResponse,
  mockCatalog,
  products,
  suppliers,
} from './procurement-fixtures'

const base = '/procurement/suppliers'

test('supplier catalog drills into API products and exact recorded facts', async ({
  page,
}, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const calls = await mockCatalog(page)
  await page.goto(base)
  await expect(
    page.getByRole('heading', { name: 'Supplier catalog' }),
  ).toBeVisible()
  await expect(
    page.getByText('Synthetic data / API', { exact: true }),
  ).toBeVisible()
  const suppliersRegion = page.getByRole('region', { name: 'Supplier results' })
  await expect(suppliersRegion.getByText('1-7 of 7')).toBeVisible()
  await page.screenshot({
    path: testInfo.outputPath('suppliers.png'),
    fullPage: true,
  })
  await page
    .getByRole('button', { name: `Browse products from ${suppliers[0].name}` })
    .click()
  const results = page.getByRole('region', { name: 'Product results' })
  await expect(results.getByText('1-2 of 2')).toBeVisible()
  await expect(results.getByText('Missing', { exact: true })).toBeVisible()
  await expect(
    results.getByText('1.123456789012', { exact: true }),
  ).toBeVisible()
  await expect(results.getByText('USD 3.123456', { exact: true })).toHaveCount(
    2,
  )
  await page.screenshot({
    path: testInfo.outputPath('products.png'),
    fullPage: true,
  })
  const inspect = page.getByRole('button', {
    name: `Inspect ${products[0].name}`,
    exact: true,
  })
  await inspect.click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('1.123456789012 kgCO2e/kg')).toBeVisible()
  await expect(
    dialog.getByText(products[0].evidence_item_id!, { exact: true }),
  ).toBeVisible()
  await expect(
    dialog.getByText('Evidence linked', { exact: true }),
  ).toBeVisible()
  await page.screenshot({
    path: testInfo.outputPath('product-details.png'),
    fullPage: true,
  })
  await page.keyboard.press('Escape')
  await expect(inspect).toBeFocused()
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true)
  for (const call of calls) {
    expect(call.method).toBe(
      call.url.pathname === '/api/context/resolve' ? 'POST' : 'GET',
    )
    if (call.url.pathname.startsWith('/api/procurement/')) {
      expect([
        '/api/procurement/suppliers',
        '/api/procurement/products',
      ]).toContain(call.url.pathname)
      expect(call.url.searchParams.get('company_id')).toBe(id(1))
      expect(call.url.searchParams.has('site_id')).toBe(false)
    }
  }
  expect(errors).toEqual([])
})

test('product filters, pagination, inactive records and browser history use server queries', async ({
  page,
}) => {
  const calls = await mockCatalog(page)
  await page.goto(`${base}?view=products&limit=5`)
  const results = page.getByRole('region', { name: 'Product results' })
  await expect(results.getByText('1-5 of 6')).toBeVisible()
  await results.getByRole('button', { name: 'Next page' }).click()
  await expect(results.getByText('6-6 of 6')).toBeVisible()
  await page.getByLabel('Material code', { exact: true }).fill('steel')
  await page.getByLabel('Category', { exact: true }).fill('purchased_material')
  await page.getByRole('button', { name: 'Apply', exact: true }).click()
  await expect(results.getByText('1-1 of 1')).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('Material code', { exact: true })).toHaveValue(
    'steel',
  )
  await page.getByRole('button', { name: 'Clear filters', exact: true }).click()
  await expect(results.getByText('1-6 of 6')).toBeVisible()
  await page.goBack()
  await expect(page.getByLabel('Material code', { exact: true })).toHaveValue(
    'steel',
  )
  await page.getByRole('button', { name: 'Clear filters', exact: true }).click()
  await page.getByRole('checkbox', { name: 'Active only' }).uncheck()
  await expect(results.getByText('1-8 of 8')).toBeVisible()
  await expect(results.getByText('Inactive', { exact: true })).toBeVisible()
  await page.getByLabel('Search products').fill('TEST-PROD-0')
  await page.getByRole('button', { name: 'Apply', exact: true }).click()
  await expect(results.getByText('1-1 of 1')).toBeVisible()
  expect(
    calls.some(
      (call) =>
        call.url.searchParams.get('material_code') === 'steel' &&
        call.url.searchParams.get('category') === 'purchased_material',
    ),
  ).toBe(true)
  expect(
    calls.some((call) => call.url.searchParams.get('search') === 'TEST-PROD-0'),
  ).toBe(true)
})

test('supplier search validates country codes and normalizes valid filters', async ({
  page,
}) => {
  const calls = await mockCatalog(page)
  await page.goto(base)
  await page.getByLabel('Country code', { exact: true }).fill('1')
  await page.getByRole('button', { name: 'Apply', exact: true }).click()
  await expect(page.getByText('Enter a two-letter country code.')).toBeVisible()
  await page.getByLabel('Country code', { exact: true }).fill('in')
  await page.getByLabel('Search suppliers').fill('TEST-SUP-0')
  await page.getByRole('button', { name: 'Apply', exact: true }).click()
  await expect(
    page
      .getByRole('region', { name: 'Supplier results' })
      .getByText('1-1 of 1'),
  ).toBeVisible()
  expect(
    calls.some(
      (call) =>
        call.url.searchParams.get('country_code') === 'IN' &&
        call.url.searchParams.get('search') === 'TEST-SUP-0',
    ),
  ).toBe(true)
})

test('products load without waiting for suppliers or workspace context', async ({
  page,
}) => {
  let release!: () => void
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  await mockCatalog(page, async (route, url) => {
    if (
      url.pathname === '/api/procurement/suppliers' ||
      url.pathname === '/api/context/resolve'
    ) {
      await gate
      if (url.pathname.endsWith('/suppliers')) {
        await route.fulfill({ json: catalogResponse(url) })
        return true
      }
    }
  })
  await page.goto(`${base}?view=products`)
  try {
    await expect(
      page
        .getByRole('region', { name: 'Catalog context' })
        .getByRole('status', { name: 'Loading data' }),
    ).toBeVisible()
    await expect(
      page.getByRole('button', {
        name: `Inspect ${products[0].name}`,
        exact: true,
      }),
    ).toBeVisible()
    await page.getByRole('tab', { name: 'Suppliers', exact: true }).click()
    await expect(
      page
        .getByRole('region', { name: 'Supplier results' })
        .getByRole('status', { name: 'Loading data' }),
    ).toBeVisible()
  } finally {
    release()
  }
  await expect(
    page.getByRole('button', {
      name: `Browse products from ${suppliers[0].name}`,
    }),
  ).toBeVisible()
})

test('errors support retry and cached refresh failures stay explicit', async ({
  page,
}) => {
  let fail = true
  await mockCatalog(page, async (route, url) => {
    if (!fail || url.pathname !== '/api/procurement/products') return
    await route.fulfill({
      status: 503,
      json: {
        detail: {
          code: 'provider_unavailable',
          message: 'Catalog temporarily unavailable.',
          trace_id: 'catalog-test',
          retryable: false,
        },
      },
    })
    return true
  })
  await page.goto(`${base}?view=products`)
  const results = page.getByRole('region', { name: 'Product results' })
  await expect(
    results.getByText('Catalog temporarily unavailable.'),
  ).toBeVisible()
  await expect(results.getByText('Trace: catalog-test')).toBeVisible()
  fail = false
  await results.getByRole('button', { name: 'Retry', exact: true }).click()
  await expect(results.getByText('1-6 of 6')).toBeVisible()
  fail = true
  await page
    .getByRole('button', { name: 'Refresh products', exact: true })
    .click()
  await expect(
    results.getByText('Refresh failed. Showing previously fetched data.'),
  ).toBeVisible()
  await expect(results.getByText('1-6 of 6')).toBeVisible()
})

test('empty pages and invalid supplier identifiers never fall back to demo data', async ({
  page,
}) => {
  const calls = await mockCatalog(page)
  await page.goto(`${base}?view=products&supplier=invalid`)
  await expect(
    page.getByRole('heading', { name: 'Invalid catalog filters' }),
  ).toBeVisible()
  expect(
    calls.some((call) => call.url.pathname.startsWith('/api/procurement/')),
  ).toBe(false)
  await page.getByRole('button', { name: 'Reset filters' }).click()
  await page.getByRole('tab', { name: /^Products/ }).click()
  await page.getByLabel('Search products').fill('does-not-exist')
  await page.getByRole('button', { name: 'Apply', exact: true }).click()
  await expect(
    page.getByText('No products found', { exact: true }),
  ).toBeVisible()
  await page.goto(`${base}?view=products&product_offset=10`)
  await expect(
    page.getByText('No products on this page', { exact: true }),
  ).toBeVisible()
  await page.getByRole('button', { name: 'Return to first page' }).click()
  await expect(
    page.getByRole('button', {
      name: `Inspect ${products[0].name}`,
      exact: true,
    }),
  ).toBeVisible()
})

test('unexpected decimals and mismatched supplier responses fail closed', async ({
  page,
}) => {
  let mismatch = false
  await mockCatalog(page, async (route, url) => {
    if (url.pathname !== '/api/procurement/products') return
    const result = catalogResponse(url)!
    await route.fulfill({
      json: {
        ...result,
        items: [
          {
            ...products[0],
            ...(mismatch
              ? { supplier_id: id(9999) }
              : { pcf_kgco2e_per_unit: 1.25 }),
          },
        ],
      },
    })
    return true
  })
  await page.goto(`${base}?view=products`)
  const results = page.getByRole('region', { name: 'Product results' })
  await expect(
    results.getByText(
      'The API response does not match the supported contract.',
    ),
  ).toBeVisible()
  mismatch = true
  await page.goto(`${base}?view=products&supplier=${suppliers[0].id}`)
  await expect(
    results.getByText(
      'The API response does not match the supported contract.',
    ),
  ).toBeVisible()
  await expect(results.getByRole('button', { name: /^Inspect / })).toHaveCount(
    0,
  )
})

test('product details and navigation fit narrow screens', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 })
  const longName = `Synthetic-${'x'.repeat(245)}`
  await mockCatalog(page, async (route, url) => {
    const result = catalogResponse(url)
    if (!result) return
    await route.fulfill({
      json: {
        ...result,
        items: result.items.map((item, index) =>
          index
            ? item
            : {
                ...item,
                name: longName,
                supplier_name: longName,
                description: longName,
              },
        ),
      },
    })
    return true
  })
  await page.goto(`${base}?view=products`)
  await page
    .getByRole('button', { name: `Inspect ${longName}`, exact: true })
    .click()
  const dialog = page.getByRole('dialog')
  await expect(
    dialog.getByText('Evidence linked', { exact: true }),
  ).toBeVisible()
  expect(
    await dialog.evaluate(
      (element) => element.scrollWidth <= element.clientWidth,
    ),
  ).toBe(true)
  await page.keyboard.press('Escape')
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true)
  await expect(
    page
      .getByRole('navigation', { name: 'Mobile navigation' })
      .getByRole('link', { name: 'Procurement', exact: true }),
  ).toHaveAttribute('aria-current', 'page')
  await page.getByRole('tab', { name: /^Suppliers/ }).click()
  await expect(
    page.getByRole('button', { name: `Browse products from ${longName}` }),
  ).toBeVisible()
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true)
})

test('unauthenticated visitors do not send catalog requests', async ({
  page,
}) => {
  const calls = await mockCatalog(page, async (route, url) => {
    if (url.pathname !== '/api/auth/session') return
    await route.fulfill({
      status: 401,
      json: {
        detail: {
          code: 'authentication_required',
          message: 'Sign in required.',
          retryable: false,
        },
      },
    })
    return true
  })
  await page.goto(base)
  await expect(
    page.getByRole('heading', { name: 'Sign in to CarbonMesh' }),
  ).toBeVisible()
  expect(
    calls.some((call) => call.url.pathname.startsWith('/api/procurement/')),
  ).toBe(false)
})
