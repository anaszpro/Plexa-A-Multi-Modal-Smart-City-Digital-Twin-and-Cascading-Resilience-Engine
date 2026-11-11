import type { Incident } from "@/types/incident";

interface IncidentDetailsProps {
  incident: Incident | null;
}

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

  const classificationLabel =
    classification.status === "skipped"
      ? "Outside current safety rules"
      : classification.hazard_type === "flood"
        ? "Flood-rule match"
        : "Road-blockage-rule match";

  return (
    <div className="incident-details">
      <h3>{incident.complaint_type}</h3>
      <p>{incident.descriptor || "No descriptor supplied"}</p>

      <dl>
        <dt>Report ID</dt>
        <dd>{incident.incident_id}</dd>

        <dt>Location</dt>
        <dd>
          {incident.location.location_text || "Address unavailable"}
        </dd>

        <dt>Borough</dt>
        <dd>{incident.location.borough}</dd>

        <dt>Safety classification</dt>
        <dd>{classificationLabel}</dd>

        <dt>Verification</dt>
        <dd>Unverified</dd>

        <dt>Lifecycle</dt>
        <dd>Active in the supplied snapshot</dd>
      </dl>

      {incident.location.coordinate_status === "missing" && (
        <p className="incident-location-warning">
          Map location unavailable for this report.
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
          <dd>{incident.time.created_raw}</dd>

          <dt>Assumed original timezone</dt>
          <dd>America/New_York</dd>

          <dt>Assumed creation time — UTC</dt>
          <dd>
            <time dateTime={incident.time.created_utc_assumed}>
              {incident.time.created_utc_assumed}
            </time>
          </dd>

          <dt>Assumed availability time — UTC</dt>
          <dd>
            <time dateTime={incident.time.availability_utc_assumed}>
              {incident.time.availability_utc_assumed}
            </time>
          </dd>

          <dt>Time interpretation</dt>
          <dd>
            UTC conversion and release at creation are assumed.
            Historical publication times have not been verified.
          </dd>

          <dt>Source dataset</dt>
          <dd>{incident.source.dataset_id}</dd>

          <dt>Source record ID</dt>
          <dd>{incident.source.record_id}</dd>

          <dt>Source snapshot</dt>
          <dd>{incident.source.snapshot_id}</dd>

          <dt>Matched rule</dt>
          <dd>{classification.rule_id ?? "No rule matched"}</dd>

          {classification.reason_code && (
            <>
              <dt>Classification reason</dt>
              <dd>{classification.reason_code}</dd>
            </>
          )}

          {classification.score && (
            <>
              <dt>Uncalibrated rule score</dt>
              <dd>
                {classification.score.value} — a rule estimate,
                not a probability.
              </dd>
            </>
          )}

          {classification.severity && (
            <>
              <dt>Rule-assigned severity</dt>
              <dd>{classification.severity}</dd>

              <dt>Severity basis</dt>
              <dd>{classification.severity_basis}</dd>
            </>
          )}

          <dt>Road association</dt>
          <dd>Not assessed</dd>
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