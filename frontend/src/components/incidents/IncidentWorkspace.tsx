"use client";

import { useState } from "react";
import type { IncidentFixture } from "@/types/incident";
import ResponsePlanner from "@/components/response/ResponsePlanner";
import CityMapLoader from "@/components/map/CityMapLoader";
import IncidentList from "./IncidentList";

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
        className="dashboard-panel response-panel"
        aria-labelledby="response-heading"
      >
        <ResponsePlanner />

        <section aria-labelledby="incidents-heading">
          <h2 id="incidents-heading">Incident reports</h2>

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

        {selectedIncident ? (
          <div className="incident-details">
            <h3>{selectedIncident.complaint_type}</h3>
            <p>{selectedIncident.descriptor || "No descriptor supplied"}</p>

            <dl>
              <dt>Report ID</dt>
              <dd>{selectedIncident.incident_id}</dd>

              <dt>Location</dt>
              <dd>
                {selectedIncident.location.location_text ||
                  "Address unavailable"}
              </dd>

              <dt>Borough</dt>
              <dd>{selectedIncident.location.borough}</dd>

              <dt>Verification</dt>
              <dd>Unverified</dd>

              <dt>Lifecycle</dt>
              <dd>Active in the supplied snapshot</dd>
            </dl>

            {selectedIncident.location.coordinate_status === "missing" && (
              <p>Map location unavailable for this report.</p>
            )}
          </div>
        ) : (
          <p className="empty-state">
            Select a report to inspect its details.
          </p>
        )}
      </aside>
    </>
  );
}