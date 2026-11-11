import type { z } from "zod";
import type {
  incidentSchema,
  validatedIncidentFixtureSchema,
} from "@/lib/incidents/schema";
import type {
  syntheticIncidentSchema,
  syntheticIncidentFixtureSchema,
} from "@/lib/incidents/synthetic-schema";

export type Incident =
  | z.infer<typeof incidentSchema>
  | z.infer<typeof syntheticIncidentSchema>;

export type IncidentFixture =
  | z.infer<typeof validatedIncidentFixtureSchema>
  | z.infer<typeof syntheticIncidentFixtureSchema>;