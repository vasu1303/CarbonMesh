import { z } from 'zod'
import {
  hash,
  record,
  timestamp,
} from '@/features/procurement/scenario-schemas'

export const eventSchema = z.object({
  id: z.uuid(),
  company_id: z.uuid(),
  event_type: z.string(),
  entity_type: z.string(),
  entity_id: z.uuid(),
  payload_hash: hash,
  analysis_signature: z.string().nullable().optional(),
  created_by: z.uuid().nullable().optional(),
  supersedes_event_id: z.uuid().nullable().optional(),
  created_at: timestamp,
})
export const eventListSchema = z.object({
  items: z.array(eventSchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().min(1).max(100),
  offset: z.number().int().min(0).max(10000),
})
export const ledgerEvidenceSchema = z.object({
  id: z.uuid(),
  evidence_type: z.string(),
  locator: z.string(),
  checksum: z.string(),
  metadata: record,
  relevance: z.string().nullable().optional(),
  source_document_id: z.uuid(),
  source_filename: z.string(),
  source_document_checksum: z.string(),
  data_source_id: z.uuid(),
  data_source_name: z.string(),
  is_synthetic: z.boolean(),
  created_at: timestamp,
})
const neighborSchema = z.object({
  edge_id: z.uuid(),
  relationship_type: z.string(),
  metadata: record,
  created_at: timestamp,
  event: eventSchema,
})
export const eventDetailSchema = eventSchema.extend({
  payload: record,
  evidence: z.array(ledgerEvidenceSchema),
  parents: z.array(neighborSchema),
  children: z.array(neighborSchema),
  evidence_truncated: z.boolean(),
  parents_truncated: z.boolean(),
  children_truncated: z.boolean(),
})
const auditEventSchema = eventSchema.omit({
  company_id: true,
  analysis_signature: true,
  created_by: true,
  supersedes_event_id: true,
})
export const auditSchema = z.object({
  company_id: z.uuid(),
  entity_type: z.string(),
  entity_id: z.uuid(),
  timeline: z.array(
    z.object({
      id: z.uuid(),
      timestamp,
      source: z.enum(['audit', 'ledger']),
      action: z.string(),
      entity_type: z.string(),
      entity_id: z.uuid(),
      actor_id: z.uuid().nullable().optional(),
      trace_id: z.string().nullable().optional(),
      payload_hash: z.string().nullable().optional(),
      details: record,
      direct: z.boolean(),
    }),
  ),
  lineage: z.object({
    events: z.array(auditEventSchema),
    edges: z.array(
      z.object({
        id: z.uuid(),
        parent_event_id: z.uuid(),
        child_event_id: z.uuid(),
        relationship_type: z.string(),
        metadata: record,
      }),
    ),
    truncated: z.boolean(),
  }),
})
const optionalUuid = z.union([z.uuid(), z.literal('')]).default('')
const optionalTime = z.union([timestamp, z.literal('')]).default('')
export const ledgerFiltersSchema = z
  .object({
    event_type: z.string().trim().max(100).default(''),
    entity_type: z.string().trim().max(100).default(''),
    entity_id: optionalUuid,
    agent_run_id: optionalUuid,
    created_from: optionalTime,
    created_to: optionalTime,
    limit: z.coerce.number().int().min(1).max(100).default(10),
    offset: z.coerce.number().int().min(0).max(10000).default(0),
    event: optionalUuid,
    audit_type: z
      .union([z.string().regex(/^[a-z][a-z0-9_]{0,99}$/), z.literal('')])
      .default(''),
    audit_id: optionalUuid,
  })
  .refine(
    (v) =>
      !v.created_from ||
      !v.created_to ||
      Date.parse(v.created_from) <= Date.parse(v.created_to),
    'The end must be on or after the start.',
  )
  .refine(
    (v) => !!v.audit_type === !!v.audit_id,
    'Choose a record type and a record to audit.',
  )
export const auditEntityTypes = [
  'measurement',
  'carbon_measurement',
  'recommendation',
  'approval',
  'procurement_scenario',
  'scenario',
  'supplier',
  'supplier_product',
  'agent_run',
  'site',
  'data_source',
  'source_document',
  'evidence_item',
  'disclosure_draft',
  'dispatch_scenario',
  'dispatch_forecast_snapshot',
] as const
export type LedgerFilters = z.infer<typeof ledgerFiltersSchema>
export type LedgerEvent = z.infer<typeof eventDetailSchema>
