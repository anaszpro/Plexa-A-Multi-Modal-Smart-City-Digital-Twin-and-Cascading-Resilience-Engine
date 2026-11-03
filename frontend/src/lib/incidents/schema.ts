import { z } from "zod";

const utcTimestamp = z.iso.datetime();

const locationSchema = z.object({
  latitude: z.number().min(-90).max(90).nullable(),
  longitude: z.number().min(-180).max(180).nullable(),
  location_text: z.string().nullable(),
  borough: z.string(),
  coordinate_status: z.enum(["provided", "missing"]),
}).superRefine((location, context) => {
  const hasCoordinates =
    location.latitude !== null && location.longitude !== null;

  const hasNoCoordinates =
    location.latitude === null && location.longitude === null;

  if (
    (location.coordinate_status === "provided" && !hasCoordinates) ||
    (location.coordinate_status === "missing" && !hasNoCoordinates)
  ) {
    context.addIssue({
      code: "custom",
      message: "Coordinate values do not match coordinate_status.",
      path: ["coordinate_status"],
    });
  }
});

const timeSchema = z.object({
  created_raw: z.string().min(1),
  created_utc_assumed: utcTimestamp,
  availability_utc_assumed: utcTimestamp,
  resolution: z.literal("assumed"),
  native_utc: utcTimestamp.nullable(),
  native_resolution: z.string(),
});

const sourceSchema = z.object({
  dataset_id: z.string().min(1),
  record_id: z.string().min(1),
  snapshot_id: z.string().min(1),
  kind: z.literal("historical"),
  native_request_id: z.string().min(1),
});


const ruleScoreSchema = z.object({
  kind: z.literal("rule_estimate"),
  value: z.number().min(0).max(1),
  calibrated: z.literal(false),
});

const classificationSchema = z.discriminatedUnion("status", [
  z.object({
    status: z.literal("ok"),
    reason_code: z.null(),
    rule_id: z.string().min(1),
    hazard_type: z.enum(["flood", "road_blockage"]),
    severity: z.string().min(1),
    severity_basis: z.string().min(1),
    score: ruleScoreSchema,
    warnings: z.array(z.string()),
  }),

  z.object({
    status: z.literal("skipped"),
    reason_code: z.string().min(1),
    rule_id: z.null(),
    hazard_type: z.null(),
    severity: z.null(),
    severity_basis: z.null(),
    score: z.null(),
    warnings: z.array(z.string()),
  }),
]);

export const incidentSchema = z.object({
  incident_id: z.string().min(1),
  complaint_type: z.string().min(1),
  descriptor: z.string(),

  location: locationSchema,
  time: timeSchema,
  source: sourceSchema,
  classification: classificationSchema,

  verification_status: z.literal("unverified"),

  lifecycle: z.object({
    state: z.literal("active"),
    cleared_at_utc: z.null(),
  }),

  road_association_status: z.literal("not_assessed"),
});


const sha256Schema = z.string().regex(/^[a-f0-9]{64}$/);

export const incidentFixtureSchema = z.object({
  schema_id: z.literal("plexa.incidents.fixture.v0.1.0"),
  contract_status: z.literal("draft_for_frontend_review"),
  is_fixture: z.literal(true),

  scenario_id: z.string().min(1),
  as_of_utc: utcTimestamp,
  display_timezone: z.literal("America/New_York"),

  availability_policy: z.object({
    policy_id: z.string().min(1),
    timezone_assumed: z.literal("America/New_York"),
    release_at_creation_assumed: z.literal(true),
    historical_publication_times_verified: z.literal(false),
  }),

  selection: z.object({
    mode: z.literal("representative_subset"),
    total_reports_at_clock: z.number().int().nonnegative(),
    returned_reports: z.number().int().nonnegative(),
    is_complete_feed: z.literal(false),
    is_paginated_response: z.literal(false),
  }),

  lifecycle_basis: z.literal(
    "initial_active_view_without_operator_clear_actions"
  ),

  capabilities: z.object({
    live_api: z.literal(false),
    report_clearing: z.literal(false),
    routing: z.literal(false),
    road_association: z.literal(false),
  }),

  provenance: z.object({
    classifier_package_id: z.string().min(1),
    native_scenario_id: z.string().min(1),
    native_run_id: z.string().min(1),
    source_request_sha256: sha256Schema,
    source_result_sha256: sha256Schema,
    timed_view_id: z.string().min(1),
  }),

  incidents: z.array(incidentSchema),
});


export const validatedIncidentFixtureSchema =
  incidentFixtureSchema.superRefine((fixture, context) => {
    const reports = fixture.incidents;

    if (fixture.selection.returned_reports !== reports.length) {
      context.addIssue({
        code: "custom",
        message: "Returned report count does not match the incidents array.",
        path: ["selection", "returned_reports"],
      });
    }

    if (fixture.selection.total_reports_at_clock < reports.length) {
      context.addIssue({
        code: "custom",
        message: "Available report count is smaller than the returned sample.",
        path: ["selection", "total_reports_at_clock"],
      });
    }

    const seenIds = new Set<string>();
    const scenarioTime = Date.parse(fixture.as_of_utc);

    reports.forEach((incident, index) => {
      if (seenIds.has(incident.incident_id)) {
        context.addIssue({
          code: "custom",
          message: "Duplicate incident ID.",
          path: ["incidents", index, "incident_id"],
        });
      }

      seenIds.add(incident.incident_id);

      const createdTime = Date.parse(incident.time.created_utc_assumed);
      const availableTime = Date.parse(
        incident.time.availability_utc_assumed
      );

      if (availableTime < createdTime) {
        context.addIssue({
          code: "custom",
          message: "Assumed availability precedes assumed creation.",
          path: ["incidents", index, "time", "availability_utc_assumed"],
        });
      }

      if (availableTime > scenarioTime) {
        context.addIssue({
          code: "custom",
          message: "Report availability is later than the scenario clock.",
          path: ["incidents", index, "time", "availability_utc_assumed"],
        });
      }
    });
  });