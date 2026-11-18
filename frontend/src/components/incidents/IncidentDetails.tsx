import type { Incident } from "@/types/incident";
import type { ApiIncidentPage } from "@/lib/incidents/api-schema";

interface IncidentDetailsProps {
  incident: Incident | ApiIncidentPage["incidents"][number] | null;
}

const roadAssociationLabels = {
  matched: "Matched",
  not_assessed: "Not assessed",
  not_available: "No road association available",
};

export default function IncidentDetails({
  incident,
}: IncidentDetailsProps) {
  if (!incident) {
    return (
      <p className="empty-state">
        Select a report to inspect its details.
      </p>
    );
  }

  const classification = incident.classification;
  const isSynthetic = incident.source.kind === "synthetic";
  const reportedAt = incident.time.created_utc_assumed;

  let classificationLabel = "Safety classification unavailable";

  if (classification.status === "skipped") {
    classificationLabel = "Outside current safety rules";
  } else if (classification.status === "ok") {
    if (classification.hazard_type === "flood") {
      classificationLabel = "Flood-rule match";
    } else if (classification.hazard_type === "road_blockage") {
      classificationLabel = "Road-blockage-rule match";
    }
  }

  const roadAssociationLabel =
    roadAssociationLabels[incident.road_association_status];

  return (
    <div className="incident-details">
      <h3>{incident.complaint_type ?? "Unknown complaint type"}</h3>
      <p>{incident.descriptor || "No descriptor supplied"}</p>

      {isSynthetic && (
        <p className="incident-interpretation">
          Synthetic example for integration testing.
        </p>
      )}

      <dl>
        <dt>Report ID</dt>
        <dd>{incident.incident_id}</dd>

        <dt>Location</dt>
        <dd>{incident.location.location_text || "Address unavailable"}</dd>

        <dt>Borough</dt>
        <dd>{incident.location.borough || "Not provided"}</dd>

        <dt>Safety classification</dt>
        <dd>{classificationLabel}</dd>

        <dt>Verification</dt>
        <dd>Unverified</dd>

        <dt>Lifecycle</dt>
        <dd>Active in the supplied snapshot</dd>

        <dt>Road association</dt>
        <dd>{roadAssociationLabel}</dd>
      </dl>

      {incident.location.coordinate_status === "missing" && (
        <p className="incident-location-warning">
          Location unavailable on the map.
        </p>
      )}

      <p className="incident-interpretation">
        A report or rule match does not confirm a physical hazard
        or road closure.
      </p>

      <details className="incident-evidence">
        <summary>Time and source evidence</summary>

        <dl>
          <dt>Original reported timestamp</dt>
          <dd>{incident.time.created_raw ?? "Not provided"}</dd>

          <dt>Assumed original timezone</dt>
          <dd>America/New_York</dd>

          <dt>Assumed creation time — UTC</dt>
          <dd>
            {reportedAt ? (
              <time dateTime={reportedAt}>{reportedAt}</time>
            ) : (
              "Report time unavailable"
            )}
          </dd>

          <dt>Assumed availability time — UTC</dt>
          <dd>
            <time dateTime={incident.time.availability_utc_assumed}>
              {incident.time.availability_utc_assumed}
            </time>
          </dd>

          <dt>Time interpretation</dt>
          <dd>
            {isSynthetic
              ? "Times are supplied by the synthetic example and do not establish historical publication times."
              : "UTC conversion and release at creation are assumed. Historical publication times have not been verified."}
          </dd>

          <dt>Source type</dt>
          <dd>{isSynthetic ? "Synthetic" : "Historical"}</dd>

          <dt>Source dataset</dt>
          <dd>{incident.source.dataset_id ?? "Not provided"}</dd>

          <dt>Source record ID</dt>
          <dd>{incident.source.record_id ?? "Not provided"}</dd>

          <dt>Source snapshot</dt>
          <dd>{incident.source.snapshot_id ?? "Not provided"}</dd>

          <dt>Matched rule</dt>
          <dd>{classification.rule_id ?? "No rule matched"}</dd>

          {classification.reason_code && (
            <>
              <dt>Classification reason</dt>
              <dd>{classification.reason_code}</dd>
            </>
          )}

          <dt>Rule score</dt>
          <dd>
            {classification.score?.value ?? "Not provided"}
            {classification.score?.calibrated === false &&
              " (uncalibrated rule estimate)"}
          </dd>

          <dt>Rule-assigned severity</dt>
          <dd>{classification.severity ?? "Not provided"}</dd>

          <dt>Severity basis</dt>
          <dd>{classification.severity_basis ?? "Not provided"}</dd>
        </dl>

        {classification.warnings.length > 0 && (
          <>
            <h4>Source and processing warnings</h4>
            <ul>
              {classification.warnings.map((warning, index) => (
                <li key={`${warning}-${index}`}>{warning}</li>
              ))}
            </ul>
          </>
        )}
      </details>
    </div>
  );
}