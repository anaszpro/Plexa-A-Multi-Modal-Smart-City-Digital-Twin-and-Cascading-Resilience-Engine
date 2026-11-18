import { z } from "zod";

const nullableText = z.string().nullable();
const utcTimestamp = z.iso.datetime();

export const apiIncidentSchema = z.object({
  incident_id: z.string().min(1),
  complaint_type: nullableText,
  descriptor: nullableText,
  location: z.object({
    latitude: z.number().min(-90).max(90).nullable(),
    longitude: z.number().min(-180).max(180).nullable(),
    location_text: nullableText,
    borough: nullableText,
    coordinate_status: z.enum(["provided", "missing"]),
  }),
  time: z.object({
    created_raw: nullableText,
    created_utc_assumed: utcTimestamp.nullable(),
    availability_utc_assumed: utcTimestamp,
    resolution: nullableText,
    native_utc: utcTimestamp.nullable(),
    native_resolution: nullableText,
  }),
  source: z.object({
    dataset_id: nullableText,
    record_id: nullableText,
    snapshot_id: nullableText,
    kind: z.literal("historical"),
    native_request_id: nullableText,
  }),
  classification: z.object({
    status: z.string(),
    reason_code: nullableText,
    rule_id: nullableText,
    hazard_type: nullableText,
    severity: nullableText,
    severity_basis: nullableText,
    score: z.object({
      value: z.number(),
      kind: nullableText,
      calibrated: z.boolean().nullable(),
    }).nullable(),
    warnings: z.array(z.string()),
  }),
  verification_status: z.literal("unverified"),
  lifecycle: z.object({
    state: z.literal("active"),
    cleared_at_utc: z.null(),
  }),
  road_association_status: z.enum(["matched", "not_available"]),
});

export const apiIncidentPageSchema = z.object({
  schema_id: z.literal("plexa.incidents.response.v0.1.0"),
  scenario_id: z.literal("ida"),
  as_of_utc: utcTimestamp,
  display_timezone: z.string(),
  pagination: z.object({
    offset: z.number().int().nonnegative(),
    limit: z.number().int().positive(),
    total: z.number().int().nonnegative(),
    next_offset: z.number().int().nonnegative().nullable(),
  }),
  incidents: z.array(apiIncidentSchema),
});

export type ApiIncidentPage = z.infer<typeof apiIncidentPageSchema>;