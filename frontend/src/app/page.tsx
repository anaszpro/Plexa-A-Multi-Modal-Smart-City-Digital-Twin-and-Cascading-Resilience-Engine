import ResponsePlanner from "@/components/response/ResponsePlanner";
export default function Home() {
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

        <span className="prototype-label">Prototype · Layout preview</span>
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
              Reports will appear here when a scenario is loaded.
            </p>
          </section>
        </aside>

        <section className="map-panel" aria-labelledby="map-heading">
          <div className="map-heading">
            <h2 id="map-heading">City map</h2>
            <span>New York City</span>
          </div>

          <div className="map-placeholder">
            <p>Map workspace</p>
            <span>
              Roads, reports and response routes will appear here.
            </span>
          </div>
        </section>

        <aside
          className="dashboard-panel details-panel"
          aria-labelledby="details-heading"
        >
          <h2 id="details-heading">Details</h2>
          <p className="empty-state">
            Select a report, road or route to inspect its details.
          </p>
        </aside>

        <section
          className="timeline-panel"
          aria-labelledby="timeline-heading"
        >
          <div>
            <h2 id="timeline-heading">Scenario timeline</h2>
            <p>Historical replay · America/New_York</p>
          </div>

          <span>No scenario loaded</span>
        </section>
      </main>
    </div>
  );
}