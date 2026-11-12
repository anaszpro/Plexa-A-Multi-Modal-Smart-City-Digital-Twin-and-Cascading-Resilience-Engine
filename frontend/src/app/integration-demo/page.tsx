import type { Metadata } from "next";
import IncidentWorkspace from "@/components/incidents/IncidentWorkspace";
import { loadSyntheticIncidentFixture } from "@/lib/incidents/load-fixture";
import { loadSyntheticRouteFixture } from "@/lib/routes/load-fixture";

export const metadata: Metadata = {
  title: "Synthetic Integration Demo | PLEXA",
};

export default function IntegrationDemoPage() {
  const fixture = loadSyntheticIncidentFixture();
  const routeFixture = loadSyntheticRouteFixture();
  const routeResponse = routeFixture.response;

  const sameScenario =
    fixture.scenario_id === routeResponse.scenario_id;

  const sameClock =
    Date.parse(fixture.as_of_utc) ===
    Date.parse(routeResponse.as_of_utc);

  if (!sameScenario || !sameClock) {
    throw new Error(
      "Incident and route fixtures must share the same scenario and clock."
    );
  }

  if (routeResponse.route_found) {
    const incidentsById = new Map(
      fixture.incidents.map((incident) => [
        incident.incident_id,
        incident,
      ])
    );

    const routes = [
      routeResponse.routes.shortest,
      routeResponse.routes.recommended,
    ];

    for (const route of routes) {
      for (const warning of route.safety_warnings) {
        const incident = incidentsById.get(warning.incident_id);

        if (!incident) {
          throw new Error(
            `Route warning references an unknown incident: ${warning.incident_id}`
          );
        }

        if (
          warning.rule_id !== null &&
          warning.rule_id !== incident.classification.rule_id
        ) {
          throw new Error(
            `Route warning rule does not match incident: ${warning.incident_id}`
          );
        }
      }
    }
  }

  const scenarioTime = new Intl.DateTimeFormat("en-US", {
    timeZone: fixture.display_timezone,
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(new Date(fixture.as_of_utc));

  return (
    <div className="dashboard">
      <a className="skip-link" href="#main-content">
        Skip to dashboard
      </a>

      <header className="dashboard-header">
        <div>
          <p className="brand">PLEXA</p>
          <p className="brand-description">
            Emergency response dashboard
          </p>
        </div>

        <span className="prototype-label">
          Synthetic integration demo
        </span>
      </header>

      <main id="main-content" className="dashboard-workspace">
        <IncidentWorkspace
          fixture={fixture}
          routeResponse={routeResponse}
        />

        <section
          className="timeline-panel"
          aria-labelledby="timeline-heading"
        >
          <div>
            <h2 id="timeline-heading">Synthetic Ida example</h2>
            <p>
              Static integration fixture · America/New_York
            </p>
          </div>

          <time dateTime={fixture.as_of_utc}>{scenarioTime}</time>
        </section>
      </main>
    </div>
  );
}