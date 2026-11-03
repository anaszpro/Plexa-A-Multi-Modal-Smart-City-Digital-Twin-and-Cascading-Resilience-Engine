import fixtureJson from "@/data/fixtures/ida_incidents_20210902T020000Z.json";
import { validatedIncidentFixtureSchema } from "./schema";

export function loadIncidentFixture() {
  return validatedIncidentFixtureSchema.parse(fixtureJson);
}