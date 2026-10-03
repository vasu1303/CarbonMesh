import { z } from 'zod'

const decimal = z
  .string()
  .regex(/^\d+(\.\d+)?([eE][+-]?\d+)?$/)
  .refine((value) => Number.isFinite(Number(value)))
const score = decimal.refine((value) => Number(value) <= 100)
const timestamp = z.iso.datetime({ offset: true })
const pageFields = {
  total: z.number().int().nonnegative(),
  limit: z.number().int().min(1).max(100),
  offset: z.number().int().nonnegative(),
}

export const supplierSchema = z.object({
  id: z.uuid(),
  supplier_code: z.string(),
  name: z.string(),
  country_code: z.string().regex(/^[A-Z]{2}$/),
  status: z.string(),
  risk: z.string(),
  metadata: z.record(z.string(), z.unknown()),
  product_count: z.number().int().nonnegative(),
  active_product_count: z.number().int().nonnegative(),
  created_at: timestamp,
  updated_at: timestamp,
})

export const productSchema = z
  .object({
    id: z.uuid(),
    supplier_id: z.uuid(),
    supplier_code: z.string(),
    supplier_name: z.string(),
    supplier_country_code: z.string().regex(/^[A-Z]{2}$/),
    supplier_status: z.string(),
    risk: z.string(),
    product_code: z.string(),
    name: z.string(),
    material_code: z.string(),
    category: z.string(),
    description: z.string().nullable(),
    pcf_kgco2e_per_unit: decimal,
    pcf_unit: z.string(),
    circularity_score: score,
    recycled_content_pct: score,
    recyclable_pct: score,
    evidence_quality_score: score,
    lead_time_days: z.number().int().nonnegative(),
    unit_cost: decimal,
    currency: z.string().regex(/^[A-Z]{3}$/),
    effective_from: z.iso.date(),
    effective_to: z.iso.date().nullable(),
    is_active: z.boolean(),
    evidence_item_id: z.uuid().nullable(),
    evidence_available: z.boolean(),
  })
  .refine(
    (item) => item.evidence_available === (item.evidence_item_id !== null),
  )

export const supplierListSchema = z.object({
  items: z.array(supplierSchema),
  ...pageFields,
})
export const productListSchema = z.object({
  items: z.array(productSchema),
  ...pageFields,
})

export const catalogFormSchema = z.object({
  search: z.string().trim().max(200, 'Use at most 200 characters.'),
  country: z
    .string()
    .trim()
    .toUpperCase()
    .regex(/^([A-Z]{2})?$/, 'Enter a two-letter country code.'),
  material: z.string().trim().max(100, 'Use at most 100 characters.'),
  category: z.string().trim().max(100, 'Use at most 100 characters.'),
})

export const catalogFiltersSchema = z.object({
  view: z.enum(['suppliers', 'products']).catch('suppliers'),
  supplier_search: catalogFormSchema.shape.search.default(''),
  product_search: catalogFormSchema.shape.search.default(''),
  country: catalogFormSchema.shape.country.default(''),
  material: catalogFormSchema.shape.material.default(''),
  category: catalogFormSchema.shape.category.default(''),
  supplier: z.union([z.uuid(), z.literal('')]).default(''),
  active: z.enum(['true', 'false']).default('true'),
  limit: z.coerce
    .number()
    .pipe(z.union([z.literal(5), z.literal(10), z.literal(25), z.literal(50)]))
    .catch(10),
  supplier_offset: z.coerce
    .number()
    .int()
    .nonnegative()
    .max(Number.MAX_SAFE_INTEGER)
    .catch(0),
  product_offset: z.coerce
    .number()
    .int()
    .nonnegative()
    .max(Number.MAX_SAFE_INTEGER)
    .catch(0),
})

export type CatalogFilters = z.infer<typeof catalogFiltersSchema>
export type Supplier = z.infer<typeof supplierSchema>
export type Product = z.infer<typeof productSchema>
