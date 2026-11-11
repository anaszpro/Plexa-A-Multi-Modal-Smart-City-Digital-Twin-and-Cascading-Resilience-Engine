import type { Metadata } from "next";
import IncidentWorkspace from "@/components/incidents/IncidentWorkspace";
import { loadSyntheticIncidentFixture } from "@/lib/incidents/load-fixture";

export const metadata: Metadata = {
  title: "Synthetic Integration Demo | PLEXA",
};

export default function IntegrationDemoPage() {
  const fixture = loadSyntheticIncidentFixture();

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
        <IncidentWorkspace fixture={fixture} />

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