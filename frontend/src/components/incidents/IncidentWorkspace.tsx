"use client";

import { useState } from "react";
import type { IncidentFixture } from "@/types/incident";
import ResponsePlanner from "@/components/response/ResponsePlanner";
import CityMapLoader from "@/components/map/CityMapLoader";
import IncidentList from "./IncidentList";
import IncidentDetails from "./IncidentDetails";

interface IncidentWorkspaceProps {
  fixture: IncidentFixture;
}

export default function IncidentWorkspace({
  fixture,
}: IncidentWorkspaceProps) {
  const [selectedIncidentId, setSelectedIncidentId] =
    useState<string | null>(null);

  const selectedIncident =
    fixture.incidents.find(
      (incident) => incident.incident_id === selectedIncidentId
    ) ?? null;

  const locatedReports = fixture.incidents.filter(
    (incident) => incident.location.coordinate_status === "provided"
  ).length;

  return (
    <>
      <aside
        className="dashboard-panel incidents-panel"
        aria-labelledby="incidents-heading"
      >
        <section
          className="incident-feed"
          aria-labelledby="incidents-heading"
        >
          <h1 id="incidents-heading">Incident reports</h1>

          <p>
            Sample: {fixture.incidents.length} reports · {locatedReports}{" "}
            located · {fixture.incidents.length - locatedReports} without
            map locations.
          </p>

          <p>
            {fixture.selection.total_reports_at_clock.toLocaleString(
              "en-US"
            )}{" "}
            available at this scenario time. This is a representative
            sample.
          </p>

          <IncidentList
            incidents={fixture.incidents}
            selectedIncidentId={selectedIncidentId}
            displayTimezone={fixture.display_timezone}
            onSelect={setSelectedIncidentId}
          />
        </section>

        <details className="planner-disclosure">
          <summary>Response planner</summary>

          <div className="response-panel planner-content">
            <ResponsePlanner />
          </div>
        </details>
      </aside>

      <section className="map-panel" aria-labelledby="map-heading">
        <div className="map-heading">
          <h2 id="map-heading">City map</h2>
          <span>New York City · Representative report sample</span>
        </div>

        <CityMapLoader
          incidents={fixture.incidents}
          selectedIncidentId={selectedIncidentId}
          onSelect={setSelectedIncidentId}
        />
      </section>

      <aside
        className="dashboard-panel details-panel"
        aria-labelledby="details-heading"
      >
        <h2 id="details-heading">Report details</h2>
        <IncidentDetails incident={selectedIncident} />
      </aside>
    </>
  );
}