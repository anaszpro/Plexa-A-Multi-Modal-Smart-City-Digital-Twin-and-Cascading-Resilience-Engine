"use client";

import type { Incident } from "@/types/incident";

interface IncidentListProps {
  incidents: Incident[];
  selectedIncidentId: string | null;
  displayTimezone: string;
  onSelect: (incidentId: string) => void;
}

export default function IncidentList({
  incidents,
  selectedIncidentId,
  displayTimezone,
  onSelect,
}: IncidentListProps) {
  const timeFormatter = new Intl.DateTimeFormat("en-US", {
    timeZone: displayTimezone,
    calendar: "gregory",
    numberingSystem: "latn",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });

  function formatReportTime(timestamp: string) {
    const parts = timeFormatter.formatToParts(new Date(timestamp));

    function part(type: Intl.DateTimeFormatPartTypes) {
      const value = parts.find((item) => item.type === type)?.value;

      if (value === undefined) {
        throw new Error(`Missing date component: ${type}`);
      }

      return value;
    }

    return (
      `${part("year")}-${part("month")}-${part("day")} ` +
      `${part("hour")}:${part("minute")} · ${displayTimezone}`
    );
  }

  if (incidents.length === 0) {
    return <p className="empty-state">No reports in this selection.</p>;
  }

  return (
    <ul className="incident-list" aria-label="Incident reports">
      {incidents.map((incident) => {
        const classificationLabel =
          incident.classification.status === "skipped"
            ? "Outside current safety rules"
            : incident.classification.hazard_type === "flood"
              ? "Flood-rule match"
              : "Road-blockage-rule match";

        return (
          <li key={incident.incident_id}>
            <button
              type="button"
              className="incident-item"
              aria-pressed={
                selectedIncidentId === incident.incident_id
              }
              onClick={() => onSelect(incident.incident_id)}
            >
              <span className="incident-title">
                {incident.complaint_type}
              </span>

              <span className="incident-description">
                {incident.descriptor || "No descriptor supplied"}
              </span>

              <span className="incident-location">
                {incident.location.location_text ||
                  incident.location.borough}
              </span>

              <span className="incident-classification">
                {classificationLabel} · Unverified
              </span>

              <span className="incident-time">
                Reported{" "}
                <time dateTime={incident.time.created_utc_assumed}>
                  {formatReportTime(
                    incident.time.created_utc_assumed
                  )}
                </time>
                {" · Assumed time"}
              </span>

              {incident.location.coordinate_status === "missing" && (
                <span className="incident-location-warning">
                  Map location unavailable
                </span>
              )}
            </button>
          </li>
        );
      })}
    </ul>
  );
}