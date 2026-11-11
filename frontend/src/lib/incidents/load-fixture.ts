import historicalFixtureJson from "@/data/fixtures/ida_incidents_20210902T020000Z.json";
import syntheticFixtureJson from "@/data/fixtures/cascading_incidents.synthetic.json";
import { validatedIncidentFixtureSchema } from "./schema";
import { syntheticIncidentFixtureSchema } from "./synthetic-schema";

export function loadIncidentFixture() {
  return validatedIncidentFixtureSchema.parse(historicalFixtureJson);
}

export function loadSyntheticIncidentFixture() {
  return syntheticIncidentFixtureSchema.parse(syntheticFixtureJson);
}