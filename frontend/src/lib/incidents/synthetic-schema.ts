import { z } from "zod";
import {
  incidentSchema,
  incidentFixtureSchema,
} from "./schema";

const syntheticLocationSchema = z
  .object({
    ...incidentSchema.shape.location.shape,
    borough: z.string().nullable(),
    coordinate_status: z.enum(["range_valid_pair", "missing"]),
  })
  .superRefine((location, context) => {
    const hasCoordinates =
      location.latitude !== null && location.longitude !== null;

    const hasNoCoordinates =
      location.latitude === null && location.longitude === null;

    if (
      (location.coordinate_status === "range_valid_pair" &&
        !hasCoordinates) ||
      (location.coordinate_status === "missing" && !hasNoCoordinates)
    ) {
      context.addIssue({
        code: "custom",
        message: "Coordinate values do not match coordinate_status.",
        path: ["coordinate_status"],
      });
    }
  })
  .transform((location) => ({
    ...location,
    coordinate_status:
      location.coordinate_status === "range_valid_pair"
        ? ("provided" as const)
        : ("missing" as const),
  }));

const historicalClassification =
  incidentSchema.shape.classification;

const syntheticClassificationSchema = z.discriminatedUnion("status", [
  historicalClassification.options[0].extend({
    severity:
      historicalClassification.options[0].shape.severity.nullable(),
    severity_basis:
      historicalClassification.options[0].shape.severity_basis.nullable(),
    score: historicalClassification.options[0].shape.score.nullable(),
  }),
  historicalClassification.options[1],
]);

export const syntheticIncidentSchema = incidentSchema.extend({
  location: syntheticLocationSchema,

  time: incidentSchema.shape.time.extend({
    resolution: z.literal("unique_assumed"),
    native_resolution: z.string().nullable(),
  }),

  source: incidentSchema.shape.source.extend({
    kind: z.literal("synthetic"),
    native_request_id: z.string().min(1).nullable(),
  }),

  classification: syntheticClassificationSchema,

  road_association_status: z.enum(["matched", "not_assessed"]),
});

const historicalProvenance =
  incidentFixtureSchema.shape.provenance.shape;

export const syntheticIncidentFixtureSchema = incidentFixtureSchema
  .extend({
    fixture_kind: z.literal("synthetic"),
    contract_status: z.literal("draft"),

    selection: incidentFixtureSchema.shape.selection.extend({
      mode: z.literal("synthetic_example"),
    }),

    lifecycle_basis: z.string().min(1),

    provenance: z.object({
      classifier_package_id:
        historicalProvenance.classifier_package_id.nullable(),
      native_scenario_id:
        historicalProvenance.native_scenario_id.nullable(),
      native_run_id: historicalProvenance.native_run_id.nullable(),
      source_request_sha256:
        historicalProvenance.source_request_sha256.nullable(),
      source_result_sha256:
        historicalProvenance.source_result_sha256.nullable(),
      timed_view_id: historicalProvenance.timed_view_id.nullable(),
    }),

    incidents: z.array(syntheticIncidentSchema),
  })
  .superRefine((fixture, context) => {
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