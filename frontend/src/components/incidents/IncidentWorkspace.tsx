"use client";

import { useState } from "react";
import type { IncidentFixture } from "@/types/incident";
import type { RouteResponse } from "@/lib/routes/schema";
import ResponsePlanner from "@/components/response/ResponsePlanner";
import CityMapLoader from "@/components/map/CityMapLoader";
import RouteComparison from "@/components/routes/RouteComparison";
import IncidentList from "./IncidentList";
import IncidentDetails from "./IncidentDetails";

interface IncidentWorkspaceProps {
  fixture: IncidentFixture;
  routeResponse?: RouteResponse;
}

export default function IncidentWorkspace({
  fixture,
  routeResponse,
}: IncidentWorkspaceProps) {
  const [selectedIncidentId, setSelectedIncidentId] =
    useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [classificationFilter, setClassificationFilter] =
    useState("all");

  const isSynthetic =
    "fixture_kind" in fixture && fixture.fixture_kind === "synthetic";

  const reportCount = fixture.incidents.length;
  const reportNoun = reportCount === 1 ? "report" : "reports";
  const query = search.trim().toLowerCase();

  const filteredIncidents = fixture.incidents.filter((incident) => {
    const searchableText = [
      incident.incident_id,
      incident.source.record_id,
      incident.complaint_type,
      incident.descriptor,
      incident.location.location_text,
      incident.location.borough,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();

    const matchesSearch = searchableText.includes(query);

    const matchesClassification =
      classificationFilter === "all" ||
      (classificationFilter === "skipped"
        ? incident.classification.status === "skipped"
        : incident.classification.status === "ok" &&
          incident.classification.hazard_type === classificationFilter);

    return matchesSearch && matchesClassification;
  });

  const selectedIncident =
    filteredIncidents.find(
      (incident) => incident.incident_id === selectedIncidentId
    ) ?? null;

  const locatedReports = filteredIncidents.filter(
    (incident) => incident.location.coordinate_status === "provided"
  ).length;

  function selectRouteWarning(incidentId: string) {
    setSearch("");
    setClassificationFilter("all");
    setSelectedIncidentId(incidentId);
  }

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
            {isSynthetic ? (
              <>
                Synthetic example with {reportCount} {reportNoun}.
                {" "}For integration testing.
              </>
            ) : (
              <>
                Representative sample of {reportCount} {reportNoun} from{" "}
                {fixture.selection.total_reports_at_clock.toLocaleString(
                  "en-US"
                )}{" "}
                available at this scenario time.
              </>
            )}
          </p>

          <div className="incident-filters">
            <div className="planner-field">
              <label htmlFor="incident-search">Search reports</label>
              <input
                id="incident-search"
                type="search"
                placeholder="ID, complaint or location"
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setSelectedIncidentId(null);
                }}
                aria-controls="incident-results"
              />
            </div>

            <div className="planner-field">
              <label htmlFor="incident-classification">
                Safety classification
              </label>
              <select
                id="incident-classification"
                value={classificationFilter}
                onChange={(event) => {
                  setClassificationFilter(event.target.value);
                  setSelectedIncidentId(null);
                }}
                aria-controls="incident-results"
              >
                <option value="all">All reports</option>
                <option value="flood">Flood-rule matches</option>
                <option value="road_blockage">
                  Road-blockage-rule matches
                </option>
                <option value="skipped">
                  Outside current safety rules
                </option>
              </select>
            </div>
          </div>

          <p role="status" aria-live="polite" aria-atomic="true">
            Showing {filteredIncidents.length} of {reportCount}{" "}
            {isSynthetic ? "synthetic" : "sample"} {reportNoun}
            {" · "}
            {locatedReports} located
            {" · "}
            {filteredIncidents.length - locatedReports} without map
            locations.
          </p>

          <div id="incident-results" className="incident-results">
            <IncidentList
              incidents={filteredIncidents}
              selectedIncidentId={selectedIncidentId}
              displayTimezone={fixture.display_timezone}
              onSelect={setSelectedIncidentId}
            />
          </div>
        </section>

        <details
          className="planner-disclosure"
          open={routeResponse !== undefined}
        >
          <summary>
            {routeResponse ? "Route comparison" : "Response planner"}
          </summary>

          <div className="response-panel planner-content">
            {routeResponse ? (
              <RouteComparison
                response={routeResponse}
                onSelectIncident={selectRouteWarning}
              />
            ) : (
              <ResponsePlanner />
            )}
          </div>
        </details>
      </aside>

      <section className="map-panel" aria-labelledby="map-heading">
        <div className="map-heading">
          <h2 id="map-heading">City map</h2>
          <span>
            New York City ·{" "}
            {isSynthetic
              ? "Synthetic integration example"
              : "Representative report sample"}
          </span>
        </div>

        <CityMapLoader
          incidents={filteredIncidents}
          selectedIncidentId={selectedIncidentId}
          onSelect={setSelectedIncidentId}
          routeResponse={routeResponse}
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