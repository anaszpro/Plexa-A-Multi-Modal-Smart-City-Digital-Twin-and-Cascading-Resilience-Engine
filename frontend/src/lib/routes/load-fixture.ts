import fixtureJson from "@/data/fixtures/cascading_route.synthetic.json";
import { syntheticRouteFixtureSchema } from "./schema";

export function loadSyntheticRouteFixture() {
  return syntheticRouteFixtureSchema.parse(fixtureJson);
}