"""Pack released Safety reports into the HTTP incident response."""

from math import isfinite

import pandas as pd

from .engine import DISPLAY_TIMEZONE
from .replay import utc_z


def _text(value):
    return None if pd.isna(value) else str(value).strip() or None


def _number(value):
    if pd.isna(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _boolean(value):
    if pd.isna(value):
        return None
    if isinstance(value, str):
        return {"true": True, "false": False}.get(value.strip().lower())
    return bool(value)


def _timestamp(value):
    timestamp = pd.to_datetime(value, format="ISO8601", utc=True, errors="coerce")
    return None if pd.isna(timestamp) else timestamp.isoformat().replace("+00:00", "Z")


def _pack_incident(row, matched_ids):
    incident_id = str(row["incident_id"])
    latitude = _number(row.get("location.latitude"))
    longitude = _number(row.get("location.longitude"))
    located = (
        latitude is not None and longitude is not None
        and -90 <= latitude <= 90 and -180 <= longitude <= 180
    )
    value = _number(row.get("result.score.value", row.get("rule_score_value")))
    score = None
    if value is not None:
        score = {
            "value": value,
            "kind": _text(row.get("result.score.kind", row.get("rule_score_kind"))),
            "calibrated": _boolean(
                row.get("result.score.calibrated", row.get("rule_score_calibrated"))
            ),
        }
    warnings = row.get("safety_warnings")

    return {
        "incident_id": incident_id,
        "complaint_type": _text(row.get("input.complaint_type")),
        "descriptor": _text(row.get("input.descriptor")),
        "location": {
            "latitude": latitude if located else None,
            "longitude": longitude if located else None,
            "location_text": (
                _text(row.get("location.location_text", row.get("incident_address")))
                or _text(row.get("street_name"))
            ),
            "borough": _text(row.get("location.borough", row.get("borough"))),
            "coordinate_status": "provided" if located else "missing",
        },
        "time": {
            "created_raw": _text(row.get("time.raw", row.get("created_date"))),
            "created_utc_assumed": _timestamp(
                row.get("created_utc_assumed", row.get("normalized_created_utc_assumed"))
            ),
            "availability_utc_assumed": _timestamp(row["availability_utc_assumed"]),
            "resolution": _text(row.get("derived_time_status", row.get("normalized_time_status"))),
            "native_utc": _timestamp(row.get("time.utc")),
            "native_resolution": _text(row.get("time.resolution")),
        },
        "source": {
            "dataset_id": _text(row.get("source.dataset_id", row.get("source_dataset_api_id"))),
            "record_id": _text(row.get("source.record_id", row.get("unique_key"))),
            "snapshot_id": _text(row.get("source.snapshot_id", row.get("source_snapshot_id"))),
            "kind": "historical",
            "native_request_id": _text(row.get("request_id")),
        },
        "classification": {
            "status": str(row["safety_processing_status"]),
            "reason_code": _text(row.get("safety_reason_code")),
            "rule_id": _text(row.get("result.rule_id")),
            "hazard_type": _text(row.get("result.hazard_type")),
            "severity": _text(row.get("result.severity", row.get("severity"))),
            "severity_basis": _text(row.get("result.severity_basis", row.get("severity_basis"))),
            "score": score,
            "warnings": [str(warning) for warning in warnings] if isinstance(warnings, list) else [],
        },
        "verification_status": "unverified",
        "lifecycle": {"state": "active", "cleared_at_utc": None},
        "road_association_status": "matched" if incident_id in matched_ids else "not_available",
    }


def pack_incident_page(clock, reports, warning_links, *, limit, offset, incident_id=None):
    if incident_id is not None:
        reports = reports.loc[reports["incident_id"].eq(incident_id)]
    ordered = reports.sort_values(
        ["availability_utc_assumed", "incident_id"], ascending=[False, True]
    )
    page = ordered.iloc[offset:offset + limit]
    matched_ids = set(warning_links["incident_id"])
    end = offset + len(page)
    return {
        "schema_id": "plexa.incidents.response.v0.1.0",
        "scenario_id": clock["scenario"],
        "as_of_utc": utc_z(clock["as_of_utc"]),
        "display_timezone": DISPLAY_TIMEZONE,
        "pagination": {
            "offset": offset,
            "limit": limit,
            "total": len(ordered),
            "next_offset": end if end < len(ordered) else None,
        },
        "incidents": [
            _pack_incident(row, matched_ids) for row in page.to_dict("records")
        ],
    }
