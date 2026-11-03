import type { z } from "zod";
import type {
  incidentSchema,
  validatedIncidentFixtureSchema,
} from "@/lib/incidents/schema";

export type Incident = z.infer<typeof incidentSchema>;

export type IncidentFixture = z.infer<
  typeof validatedIncidentFixtureSchema
>;