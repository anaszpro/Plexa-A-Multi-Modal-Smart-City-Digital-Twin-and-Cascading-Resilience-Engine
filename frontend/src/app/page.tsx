import { loadIncidentFixture } from "@/lib/incidents/load-fixture";
import ResponsePlanner from "@/components/response/ResponsePlanner";
import CityMapLoader from "@/components/map/CityMapLoader";

export default function Home() {
  const fixture = loadIncidentFixture();

  const locatedReports = fixture.incidents.filter(
    (incident) => incident.location.coordinate_status === "provided"
  ).length;

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
          Prototype · Representative fixture
        </span>
      </header>

      <main id="main-content" className="dashboard-workspace">
        <aside
          className="dashboard-panel response-panel"
          aria-labelledby="response-heading"
        >
          <ResponsePlanner />

          <section aria-labelledby="incidents-heading">
            <h2 id="incidents-heading">Incident reports</h2>

            <p className="empty-state">
              Representative sample: {fixture.incidents.length} reports
              {" · "}
              {locatedReports} located
              {" · "}
              {fixture.incidents.length - locatedReports} without map
              locations.
            </p>

            <p>
              {fixture.selection.total_reports_at_clock.toLocaleString(
                "en-US"
              )}{" "}
              reports available at the fixture’s scenario time. This
              sample is not a complete feed.
            </p>
          </section>
        </aside>

        <section className="map-panel" aria-labelledby="map-heading">
          <div className="map-heading">
            <h2 id="map-heading">City map</h2>
            <span>New York City · Base map only</span>
          </div>

          <CityMapLoader />
        </section>

        <aside
          className="dashboard-panel details-panel"
          aria-labelledby="details-heading"
        >
          <h2 id="details-heading">Details</h2>
          <p className="empty-state">
            Report selection and details will be connected next.
          </p>
        </aside>

        <section
          className="timeline-panel"
          aria-labelledby="timeline-heading"
        >
          <div>
            <h2 id="timeline-heading">Ida snapshot</h2>
            <p>
              Historical fixture · America/New_York · Playback unavailable
            </p>
          </div>

          <time dateTime={fixture.as_of_utc}>{scenarioTime}</time>
        </section>
      </main>
    </div>
  );
}