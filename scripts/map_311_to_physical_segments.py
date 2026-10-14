"""Resolve report candidates to physical segments without guessing ties."""

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main(root=ROOT, output=None):
    folder = root / "data/processed"
    source = folder / "street_311_mapping_v2"
    index_path = folder / "physical_street_index_v1.parquet"
    output = output or folder / "physical_311_mapping_v1"

    if output.exists():
        raise ValueError(f"Output already exists: {output}")

    manifest = json.loads((source / "manifest.json").read_text())
    index = pd.read_parquet(index_path)
    audit = pd.read_parquet(source / "report_audit.parquet")
    edges = pd.read_parquet(source / "nearest_edge_candidates.parquet")
    keys = ["u", "v", "key"]

    network_hash = sha256(root / "data/raw/nyc_streets.parquet")
    if network_hash != manifest["street_network_sha256"]:
        raise ValueError("Street network differs from the report mapping.")

    if index.empty or not index.street_network_sha256.eq(network_hash).all():
        raise ValueError("Street index belongs to a different network.")

    if index[keys + ["street_segment_id"]].isna().any().any():
        raise ValueError("Street index contains missing identifiers.")

    if index.duplicated(keys).any():
        raise ValueError("Duplicate street edge keys.")

    if audit.unique_key.isna().any() or audit.unique_key.duplicated().any():
        raise ValueError("Audit report IDs must be nonnull and unique.")

    if len(audit) != manifest["candidate_reports_with_resolved_time"]:
        raise ValueError("Audit count differs from the source manifest.")

    if edges.duplicated(["unique_key", *keys]).any():
        raise ValueError("Duplicate report/edge candidates.")

    if not edges.unique_key.isin(audit.unique_key).all():
        raise ValueError("Candidate report is absent from the audit.")

    counts = edges.groupby("unique_key").size()
    observed = audit.unique_key.map(counts).fillna(0).astype(int)

    if not observed.eq(audit.nearest_edge_count).all():
        raise ValueError("Candidate counts differ from the audit.")

    allowed_statuses = [
        "matched_unique",
        "ambiguous_nearest",
        "missing_or_invalid_coordinates",
        "outside_match_distance",
    ]
    if not audit.match_status.isin(allowed_statuses).all():
        raise ValueError("Unknown input match status.")

    has_candidates = audit.match_status.isin(
        ["matched_unique", "ambiguous_nearest"]
    )
    if not has_candidates.eq(observed.gt(0)).all():
        raise ValueError("Input statuses disagree with candidate availability.")

    joined = edges.merge(
        index[keys + ["street_segment_id"]],
        on=keys,
        how="left",
        validate="many_to_one",
    )
    if joined.street_segment_id.isna().any():
        raise ValueError("A candidate edge is missing from the street index.")

    # Multiple directed edges may identify the same physical segment.
    # Keep every distinct candidate segment for each report.
    candidates = joined.groupby(
        ["unique_key", "street_segment_id"],
        as_index=False,
    ).agg(
        directed_candidate_count=("key", "size"),
        nearest_distance_ft=("dist_to_street_ft", "min"),
    )

    segment_counts = candidates.groupby("unique_key").size()

    audit = audit.rename(columns={"match_status": "edge_match_status"})
    audit["physical_segment_count"] = (
        audit.unique_key.map(segment_counts).fillna(0).astype(int)
    )
    audit["match_status"] = audit.edge_match_status

    audit.loc[
        audit.physical_segment_count.eq(1), "match_status"
    ] = "matched_unique_segment"

    audit.loc[
        audit.physical_segment_count.gt(1), "match_status"
    ] = "ambiguous_segment"

    resolved = candidates.loc[
        candidates.unique_key.map(segment_counts).eq(1),
        ["unique_key", "street_segment_id"],
    ]
    matched = audit.merge(
        resolved,
        on="unique_key",
        validate="one_to_one",
    )

    if len(matched) != int(audit.physical_segment_count.eq(1).sum()):
        raise ValueError("Resolved report count is inconsistent.")

    input_paths = [
        index_path,
        source / "manifest.json",
        source / "report_audit.parquet",
        source / "nearest_edge_candidates.parquet",
    ]

    result = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_mapping_manifest": manifest,
        "input_sha256": {
            str(path.relative_to(root)): sha256(path)
            for path in input_paths
        },
        "script_sha256": sha256(Path(__file__)),
        "status_counts": {
            str(status): int(count)
            for status, count in audit.match_status.value_counts().items()
        },
        "uniquely_matched_reports": len(matched),
        "candidate_segment_rows": len(candidates),
        "note": (
            "Report locations only; no negative labels or forecast targets. "
            "Unresolved-time exclusions are retained in the source manifest."
        ),
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=".physical_311_", dir=output.parent)
    )

    try:
        outputs = [
            ("report_audit", audit),
            ("candidate_segments", candidates),
            ("matched_reports", matched),
        ]

        for name, frame in outputs:
            path = temporary / f"{name}.parquet"
            frame.to_parquet(path, index=False)
            pd.testing.assert_frame_equal(
                frame.reset_index(drop=True),
                pd.read_parquet(path),
            )

        (temporary / "manifest.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
        os.rename(temporary, output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)

    print(json.dumps(result["status_counts"], indent=2))
    print(f"Saved: {output}")


if __name__ == "__main__":
    main()
