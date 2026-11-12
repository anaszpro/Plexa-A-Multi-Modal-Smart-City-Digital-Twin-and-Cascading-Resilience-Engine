import { z } from "zod";

const nonnegativeNumber = z.number().nonnegative();

const timestamp = z.iso.datetime({ offset: true });

const predictionHour = timestamp.refine(
  (value) => Date.parse(value) % 3_600_000 === 0,
  "Prediction time must be an exact hour."
);

const nodeSchema = z.object({
  node_id: z.string().min(1),
  node_level: z.string().min(1),
});

const endpointSchema = nodeSchema.extend({
  latitude: z.number().min(-90).max(90),
  longitude: z.number().min(-180).max(180),
});

// GeoJSON positions use longitude first, then latitude.
const positionSchema = z.tuple([
  z.number().min(-180).max(180),
  z.number().min(-90).max(90),
]);

const edgeSchema = z.object({
  type: z.literal("Feature"),

  geometry: z.object({
    type: z.literal("LineString"),
    coordinates: z.array(positionSchema).min(2),
  }),

  properties: z.object({
    lion_segment_id: z.string().min(1),
    street: z.string(),
    distance_m: nonnegativeNumber,
    estimated_vehicles_per_hour: nonnegativeNumber.nullable(),
    climate_flood_report_score: z.number().min(0).max(1).nullable(),
    climate_alert: z.boolean().nullable(),
    has_safety_warning: z.boolean(),
  }),
});

export const routeSchema = z.object({
  distance_m: nonnegativeNumber,
  routing_cost_m: nonnegativeNumber,

  penalties_m: z.object({
    traffic: nonnegativeNumber,
    missing_traffic: nonnegativeNumber,
    road_warning: nonnegativeNumber,
  }),

  traffic_coverage: z.object({
    covered_distance_m: nonnegativeNumber,
    percent: z.number().min(0).max(100),
  }),

  safety_warnings: z.array(
    z.object({
      incident_id: z.string().min(1),
      rule_id: z.string().min(1).nullable(),
    })
  ),

  geometry: z.object({
    type: z.literal("FeatureCollection"),
    features: z.array(edgeSchema).min(1),
  }),
});

export const routeRequestSchema = z.object({
  scenario: z.enum(["ida", "2024"]),
  as_of_utc: predictionHour,
  origin: nodeSchema,
  destination: nodeSchema,
});

const responseFields = {
  schema_version: z.literal("plexa.route.response.v0.1.0"),
  scenario_id: z.enum(["ida", "2024"]),
  as_of_utc: predictionHour,
  target_start_utc: timestamp,
  target_end_utc: timestamp,
  display_timezone: z.literal("America/New_York"),
  road_access: z.string().min(1),
  origin: endpointSchema,
  destination: endpointSchema,

  climate_road_statuses: z.record(
    z.string(),
    z.number().int().nonnegative()
  ),
};

export const routeResponseSchema = z
  .discriminatedUnion("route_found", [
    z.object({
      ...responseFields,
      route_found: z.literal(true),
      changed_from_shortest: z.boolean(),
      routes: z.object({
        shortest: routeSchema,
        recommended: routeSchema,
      }),
    }),

    z.object({
      ...responseFields,
      route_found: z.literal(false),
      changed_from_shortest: z.literal(false),
      routes: z.object({
        shortest: z.null(),
        recommended: z.null(),
      }),
    }),
  ])
  .superRefine((response, context) => {
    const predictionTime = Date.parse(response.as_of_utc);
    const start = Date.parse(response.target_start_utc);
    const end = Date.parse(response.target_end_utc);

    if (start !== predictionTime || end - start !== 3_600_000) {
      context.addIssue({
        code: "custom",
        message: "Target interval must be the hour starting at as_of_utc.",
        path: ["target_start_utc"],
      });
    }
  });

export const syntheticRouteFixtureSchema = z
  .object({
    is_fixture: z.literal(true),
    fixture_kind: z.literal("synthetic"),
    request: routeRequestSchema,
    response: routeResponseSchema,
  })
  .superRefine(({ request, response }, context) => {
    const matchesRequest =
      request.scenario === response.scenario_id &&
      Date.parse(request.as_of_utc) === Date.parse(response.as_of_utc) &&
      request.origin.node_id === response.origin.node_id &&
      request.origin.node_level === response.origin.node_level &&
      request.destination.node_id === response.destination.node_id &&
      request.destination.node_level === response.destination.node_level;

    if (!matchesRequest) {
      context.addIssue({
        code: "custom",
        message: "Route response does not match its request.",
        path: ["response"],
      });
    }
  });

export type Route = z.infer<typeof routeSchema>;
export type RouteResponse = z.infer<typeof routeResponseSchema>;