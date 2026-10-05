import { z } from 'zod'

const uuid = z.uuid('Select a record.')
const timestamp = z.iso.datetime({ offset: true })
const hash = z.string().regex(/^[0-9a-f]{64}$/)
const decimal = z.string().regex(/^-?\d+(\.\d+)?([eE][+-]?\d+)?$/)
const confidence = decimal.refine(
  (value) => Number(value) >= 0 && Number(value) <= 1,
)
const metadata = z.record(z.string(), z.unknown())
const standardSchema = z.object({
  id: uuid,
  company_id: uuid,
  source_document_id: uuid.nullable(),
  code: z.string(),
  version: z.string(),
  name: z.string(),
  jurisdiction: z.string().nullable(),
  description: z.string().nullable(),
  effective_from: z.iso.date(),
  effective_to: z.iso.date().nullable(),
  is_active: z.boolean(),
  created_at: timestamp,
  updated_at: timestamp,
})
export const requirementSchema = z.object({
  id: uuid,
  standard_id: uuid,
  metric_definition_id: uuid.nullable(),
  requirement_code: z.string(),
  title: z.string(),
  description: z.string(),
  sequence: z.number().int().positive(),
  claim_template: z.string(),
  evidence_rules: metadata,
  minimum_confidence: confidence,
  is_required: z.boolean(),
  is_active: z.boolean(),
})
export const standardsSchema = z.object({
  items: z.array(
    standardSchema.extend({
      template: metadata,
      requirements: z.array(requirementSchema),
    }),
  ),
  total: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
})
const evidenceSchema = z.object({
  id: uuid,
  source_document_id: uuid,
  data_source_id: uuid,
  source_filename: z.string(),
  source_document_checksum: hash,
  evidence_type: z.string(),
  locator: z.string(),
  checksum: hash,
  metadata,
  embedding_model: z.string().nullable(),
  embedded_at: timestamp.nullable(),
  similarity: decimal.nullable(),
})
const citationSchema = z
  .object({
    id: uuid,
    disclosure_claim_id: uuid,
    ledger_event_id: uuid.nullable(),
    evidence_item_id: uuid.nullable(),
    locator: z.string().nullable(),
    validation_status: z.enum([
      'valid',
      'invalid',
      'stale',
      'context_mismatch',
    ]),
    validation_details: metadata,
    evidence: evidenceSchema.nullable(),
    created_at: timestamp,
  })
  .refine(
    (item) => item.ledger_event_id !== null || item.evidence_item_id !== null,
  )
export const claimSchema = z.object({
  id: uuid,
  disclosure_draft_id: uuid,
  requirement_id: uuid.nullable(),
  requirement_code: z.string().nullable(),
  fact_binding_id: uuid.nullable(),
  ledger_event_id: uuid.nullable(),
  sequence: z.number().int().positive(),
  claim_type: z.enum(['numeric', 'qualitative', 'method', 'scope']),
  claim_template: z.string(),
  rendered_text: z.string().nullable(),
  support_status: z.enum([
    'supported',
    'partially_supported',
    'unsupported',
    'citation_invalid',
    'context_mismatch',
    'stale_fact',
    'policy_blocked',
  ]),
  confidence,
  validation_details: metadata,
  validated_at: timestamp.nullable(),
  created_at: timestamp,
  updated_at: timestamp,
  citations: z.array(citationSchema),
})
const gapSchema = z.object({
  id: uuid,
  disclosure_draft_id: uuid,
  disclosure_claim_id: uuid.nullable(),
  requirement_id: uuid.nullable(),
  code: z.string(),
  severity: z.enum(['info', 'warning', 'error']),
  status: z.enum(['open', 'resolved', 'waived']),
  message: z.string(),
  details: metadata,
  resolved_at: timestamp.nullable(),
  created_at: timestamp,
  updated_at: timestamp,
})
export const draftSchema = z
  .object({
    id: uuid,
    company_id: uuid,
    standard: standardSchema,
    site_id: uuid,
    reporting_period_id: uuid,
    measurement_id: uuid,
    agent_run_id: uuid.nullable(),
    ledger_event_id: uuid.nullable(),
    version: z.number().int().positive(),
    title: z.string(),
    narrative_template: z.string(),
    rendered_text: z.string().nullable(),
    context_hash: hash,
    payload_hash: hash,
    status: z.enum([
      'draft',
      'validating',
      'blocked',
      'pending_approval',
      'approved',
      'rejected',
      'invalidated',
    ]),
    validation_summary: z
      .object({
        terminal_state: z.string().optional(),
        state: z.string().optional(),
      })
      .catchall(z.unknown()),
    invalidated_at: timestamp.nullable(),
    created_at: timestamp,
    updated_at: timestamp,
    claims: z.array(claimSchema),
    gaps: z.array(gapSchema),
    approval: z
      .object({
        id: uuid,
        target_type: z.literal('disclosure_draft'),
        target_id: uuid,
        status: z.enum([
          'pending',
          'approved',
          'rejected',
          'invalidated',
          'expired',
        ]),
        preview_hash: hash,
        analysis_signature: hash,
        context_hash: hash.nullable(),
        idempotency_key: z.string(),
        expires_at: timestamp,
        created_at: timestamp,
      })
      .nullable(),
  })
  .refine(
    (draft) =>
      draft.claims.every(
        (claim) =>
          claim.disclosure_draft_id === draft.id &&
          claim.citations.every(
            (citation) => citation.disclosure_claim_id === claim.id,
          ),
      ) &&
      draft.gaps.every((gap) => gap.disclosure_draft_id === draft.id) &&
      (!draft.approval || draft.approval.target_id === draft.id),
  )
export const validationSchema = z.object({
  draft: draftSchema,
  terminal_state: z.enum([
    'success',
    'approval_required',
    'unsupported',
    'validation_failed',
    'stale',
    'no_data',
  ]),
  supported_claims: z.number().int().nonnegative(),
  partially_supported_claims: z.number().int().nonnegative(),
  unsupported_claims: z.number().int().nonnegative(),
  open_gaps: z.number().int().nonnegative(),
  idempotent: z.boolean(),
})
export const evidencePackSchema = z.object({
  schema_version: z.literal('1.0'),
  generated_at: timestamp,
  company_id: uuid,
  draft_id: uuid,
  standard_code: z.string(),
  standard_version: z.string(),
  context_hash: hash,
  payload_hash: hash,
  claims: z.array(claimSchema),
  gaps: z.array(gapSchema),
  fact_bindings: z.array(
    z.object({
      id: uuid,
      artifact_type: z.string(),
      artifact_id: uuid.nullable(),
      agent_run_id: uuid.nullable(),
      ledger_event_id: uuid,
      evidence_item_id: uuid.nullable(),
      placeholder: z.string(),
      value_snapshot: metadata,
      display_value: z.string(),
      unit: z.string().nullable(),
      context_hash: hash.nullable(),
      binding_hash: hash.nullable(),
      created_at: timestamp,
    }),
  ),
  evidence: z.array(evidenceSchema),
  disclaimer: z.literal('POC draft; not an assurance opinion or filing.'),
})
export const createDraftFormSchema = z.object({
  standard_id: uuid,
  measurement_id: uuid,
  requested_by: uuid,
  requirement_ids: z.array(uuid).max(100),
  title: z.string().trim().max(255),
})
export type Draft = z.infer<typeof draftSchema>
export type CreateDraftForm = z.infer<typeof createDraftFormSchema>
export type Claim = z.infer<typeof claimSchema>
