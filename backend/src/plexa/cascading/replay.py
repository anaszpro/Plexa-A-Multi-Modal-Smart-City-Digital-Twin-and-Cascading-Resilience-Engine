"""Load frozen replay forecasts and align them to the prepared LION graph."""

import hashlib
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds


SCENARIO_WINDOWS = {
    "2024": {
        "label": "2024 simulated live",
        "start_utc": pd.Timestamp("2024-01-01T05:00:00Z"),
        "end_utc_exclusive": pd.Timestamp("2025-01-01T05:00:00Z"),
    },
    "ida": {
        "label": "Hurricane Ida replay",
        "start_utc": pd.Timestamp("2021-09-01T04:00:00Z"),
        "end_utc_exclusive": pd.Timestamp("2021-09-03T04:00:00Z"),
    },
}


def make_clock(scenario, as_of):
    """Create an hourly replay clock and its next-hour target interval."""
    if scenario not in SCENARIO_WINDOWS:
        raise ValueError(
            f"Unsupported scenario {scenario!r}. "
            "Currently supported: '2024' and 'ida'."
        )

    timestamp = pd.Timestamp(as_of)

    if pd.isna(timestamp):
        raise ValueError("A valid timestamp is required.")

    if timestamp.tzinfo is None:
        raise ValueError(
            "Include a timezone offset or Z; naive timestamps are rejected."
        )

    timestamp = timestamp.tz_convert("UTC")

    if timestamp != timestamp.floor("h"):
        raise ValueError("The clock must be an exact UTC hour start.")

    target_end = timestamp + pd.Timedelta(hours=1)
    scenario_window = SCENARIO_WINDOWS[scenario]

    if (
        timestamp < scenario_window["start_utc"]
        or target_end > scenario_window["end_utc_exclusive"]
    ):
        raise ValueError("The prediction interval is outside this scenario.")

    return {
        "clock_policy_id": "plexa-hourly-clock-v0.1.0",
        "scenario": scenario,
        "as_of_utc": timestamp,
        "target_start_utc": timestamp,
        "target_end_utc": target_end,
        "horizon_hours": 1,
    }


def utc_z(timestamp):
    """Format an aware timestamp for module interfaces requiring UTC Z."""
    return (
        pd.Timestamp(timestamp)
        .tz_convert("UTC")
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def load_ida_climate_hour(clock, bundle_directory, common_contexts):
    if clock["scenario"] != "ida":
        raise ValueError("This loader currently supports the Ida bundle only.")

    source_path = bundle_directory / "actual_predictions.parquet"
    target_time = utc_z(clock["target_start_utc"])

    native_records = (
        ds.dataset(str(source_path), format="parquet")
        .to_table(
            filter=ds.field("prediction_time_utc") == target_time
        )
        .to_pylist()
    )

    if not native_records:
        raise ValueError(f"No stored climate predictions for {target_time}.")

    rows = []
    for record in native_records:
        if record["status"] != "ok" or record["result"] is None:
            raise ValueError(
                "Unexpected non-success result in the verified Ida cache: "
                f"{record['request_id']} / {record['reason_code']}"
            )

        row = {
            "climate_cache_prediction_time_utc": record["prediction_time_utc"],
            "climate_request_id": record["request_id"],
            "climate_request_sha256": record["request_sha256"],
            "climate_status": record["status"],
            "climate_reason_code": record["reason_code"],
            "climate_warnings": record["warnings"],
        }

        # Retain every native result field, using a climate prefix.
        row.update({
            f"climate_{key}": value
            for key, value in record["result"].items()
        })
        rows.append(row)

    hour_table = pd.DataFrame(rows)

    for column in [
        "climate_cache_prediction_time_utc",
        "climate_prediction_time_utc",
        "climate_target_start_utc",
        "climate_target_end_utc",
        "climate_latest_rainfall_end_utc",
        "climate_history_cutoff_utc",
    ]:
        hour_table[column] = pd.to_datetime(
            hour_table[column], utc=True, errors="raise"
        )

    # Validate this adapter's hour selection and joins.
    for column in [
        "climate_cache_prediction_time_utc",
        "climate_prediction_time_utc",
        "climate_target_start_utc",
    ]:
        if not hour_table[column].eq(clock["target_start_utc"]).all():
            raise ValueError(f"Unexpected time in {column}.")

    if not hour_table["climate_target_end_utc"].eq(
        clock["target_end_utc"]
    ).all():
        raise ValueError("Climate target interval does not match the clock.")

    if not hour_table["climate_prediction_horizon_hours"].eq(1).all():
        raise ValueError("Unexpected climate horizon.")

    if not hour_table["climate_street_segment_id"].is_unique:
        raise ValueError("Duplicate climate roads in the selected hour.")

    expected_roads = set(common_contexts["climate_street_segment_id"])
    actual_roads = set(hour_table["climate_street_segment_id"])

    if actual_roads != expected_roads:
        raise ValueError(
            "Climate road scope mismatch: "
            f"{len(expected_roads - actual_roads)} missing; "
            f"{len(actual_roads - expected_roads)} unexpected."
        )

    return hour_table, native_records


def load_mobility_hour(clock, locations, common_contexts, alignment_metadata):
    cache_directories = {
        "2024": locations["Mobility 2024 cache"],
        "ida": locations["Mobility Ida cache"],
    }
    cache_directory = Path(cache_directories[clock["scenario"]]).resolve()
    manifest_path = cache_directory / "cache_manifest.json"

    with manifest_path.open("r", encoding="utf-8-sig") as handle:
        manifest = json.load(handle)

    # Bind this loader to the accepted release and common scope.
    expected_metadata = {
        "complete": True,
        "package_id": "mobility-b3-time-demo-v0.2.0",
        "package_manifest_sha256": (
            "135448a1cc24ddee11aa8ed0dd64bdd79"
            "a71ea0dd5cb879d29c95d61db1c5a28"
        ),
        "serving_context_id": alignment_metadata["serving_context_id"],
        "subset_id": alignment_metadata["subset_id"],
        "scope": "joint",
        "interval_hours": 1,
        "target": "typical_hourly_vehicle_volume",
        "unit": "vehicles_per_hour",
        "value_kind": "model_estimate",
    }

    for key, expected_value in expected_metadata.items():
        if manifest.get(key) != expected_value:
            raise ValueError(f"Unexpected cache metadata for {key!r}.")

    def declared_utc(value):
        timestamp = pd.Timestamp(value)
        if pd.isna(timestamp) or timestamp.tzinfo is None:
            raise ValueError("Cache time must have an explicit timezone.")
        return timestamp.tz_convert("UTC")

    target_start = clock["target_start_utc"]
    target_end = clock["target_end_utc"]

    if not (
        declared_utc(manifest["start_utc"]) <= target_start
        and target_end <= declared_utc(manifest["end_utc_exclusive"])
    ):
        raise ValueError("Requested interval is outside the traffic cache.")

    selected_parts = [
        part for part in manifest["parts"]
        if (
            declared_utc(part["start_utc"]) <= target_start
            < declared_utc(part["end_utc_exclusive"])
        )
    ]

    if not selected_parts:
        raise ValueError("No declared cache part contains the requested hour.")

    selected_paths = []
    for part in selected_parts:
        part_path = (cache_directory / part["file"]).resolve()
        if not part_path.is_relative_to(cache_directory):
            raise ValueError("Declared part points outside the cache directory.")
        if not part_path.is_file():
            raise FileNotFoundError(part_path)
        selected_paths.append(str(part_path))

    dataset = ds.dataset(selected_paths, format="parquet")

    columns = [
        "segment_id", "direction", "road_type",
        "hour_start_utc", "interval_end_utc",
        "collection_source", "p10", "p50", "p90",
    ]
    missing_columns = set(columns) - set(dataset.schema.names)
    if missing_columns:
        raise ValueError(f"Missing cache columns: {sorted(missing_columns)}")

    hour_type = dataset.schema.field("hour_start_utc").type
    if not pa.types.is_timestamp(hour_type) or hour_type.tz is None:
        raise ValueError("Traffic hour column must be timezone-aware.")

    target_scalar = pa.scalar(
        target_start.to_pydatetime(), type=hour_type
    )

    hour_table = dataset.to_table(
        columns=columns,
        filter=ds.field("hour_start_utc") == target_scalar,
    ).to_pandas()

    if hour_table.empty:
        raise ValueError("The selected cache parts contain no requested-hour rows.")

    keys = ["segment_id", "direction", "road_type"]
    for column in keys:
        hour_table[column] = hour_table[column].astype("string")

    for column in ["hour_start_utc", "interval_end_utc"]:
        hour_table[column] = pd.to_datetime(
            hour_table[column], utc=True, errors="raise"
        )

    if not hour_table["hour_start_utc"].eq(target_start).all():
        raise ValueError("Traffic start time does not match the clock.")
    if not hour_table["interval_end_utc"].eq(target_end).all():
        raise ValueError("Traffic end time does not match the clock.")

    # Check the identities selected by this hourly loader.
    key_comparison = common_contexts[keys].merge(
        hour_table[keys],
        on=keys,
        how="outer",
        validate="one_to_one",
        indicator=True,
    )
    if not key_comparison["_merge"].eq("both").all():
        raise ValueError("Traffic contexts do not match the approved scope.")

    hour_table = hour_table.rename(columns={
        "hour_start_utc": "mobility_target_start_utc",
        "interval_end_utc": "mobility_target_end_utc",
        "collection_source": "mobility_collection_source",
        "p10": "mobility_p10",
        "p50": "mobility_p50",
        "p90": "mobility_p90",
    })

    source_metadata = {
        "manifest_path": str(manifest_path),
        "cache_manifest": manifest,
        "selected_parts": selected_parts,
    }

    return (
        hour_table.sort_values(keys).reset_index(drop=True),
        source_metadata,
    )


def attach_predictions_to_lion(snapshot, traversals, direction_bridge):
    context_keys = ["segment_id", "direction", "road_type"]
    edge_keys = [
        "segment_id", "candidate_lion_id",
        "from_node_id", "from_node_level", "to_node_id", "to_node_level",
    ]
    traffic_fields = ["mobility_p10", "mobility_p50", "mobility_p90"]
    climate_fields = [
        "climate_reported_flooding_score",
        "climate_model_alert",
        "climate_latest_rainfall_end_utc",
    ]

    edges = traversals[edge_keys + ["candidate_source", "source_fids"]].copy()

    # Climate results belong to the physical road.
    road_climate = snapshot[["segment_id"] + climate_fields].drop_duplicates()
    edges = edges.merge(
        road_climate, on="segment_id", how="left", validate="many_to_one"
    )

    # Traffic estimates belong to a specific road and direction.
    links = direction_bridge.loc[
        direction_bridge["direction_review"].eq("one_proposed_match"),
        edge_keys + ["direction", "road_type"],
    ].copy()
    link_counts = links.groupby(edge_keys).size().rename("mobility_context_count")
    single_links = links.loc[~links.duplicated(edge_keys, keep=False)]

    edge_traffic = single_links.merge(
        snapshot[context_keys + traffic_fields],
        on=context_keys,
        how="left",
        validate="one_to_one",
    )
    edges = edges.join(link_counts, on=edge_keys).merge(
        edge_traffic, on=edge_keys, how="left", validate="one_to_one"
    )

    edges["mobility_context_count"] = edges["mobility_context_count"].fillna(0).astype(int)
    edges["mobility_edge_status"] = np.select(
        [
            edges["mobility_context_count"].eq(0),
            edges["mobility_context_count"].gt(1),
            edges["mobility_p50"].isna(),
        ],
        [
            "no_directional_match",
            "multiple_contexts_need_review",
            "matched_context_missing_estimate",
        ],
        default="estimate_attached",
    )
    return edges


def run_climate_with_request(python_path, inputs_path, model_path, target):
    with TemporaryDirectory(prefix="plexa-climate-test-") as temp:
        request_path = Path(temp) / "request.json"
        result_path = Path(temp) / "result.json"

        prepared = subprocess.run(
            [
                str(python_path), "-B", str(model_path / "prepare_request.py"),
                "--inputs", str(inputs_path), "--bundle", str(model_path),
                "--time", utc_z(target), "--request", str(request_path),
            ],
            cwd=temp, capture_output=True, text=True,
        )
        if prepared.returncode or not request_path.is_file():
            raise RuntimeError(prepared.stderr or prepared.stdout)

        predicted = subprocess.run(
            [
                str(python_path), "-B", str(model_path / "predict.py"),
                "--bundle", str(model_path), "--input", str(request_path),
                "--output", str(result_path),
            ],
            cwd=temp, capture_output=True, text=True,
        )
        if not result_path.is_file():
            raise RuntimeError(
                f"predict.py exited with code {predicted.returncode} "
                f"without a result file.\n{predicted.stderr or predicted.stdout}"
            )

        request_bytes = request_path.read_bytes()
        request = json.loads(request_bytes)
        result = json.loads(result_path.read_bytes())

    if result["request_sha256"] != hashlib.sha256(request_bytes).hexdigest():
        raise ValueError("The climate result refers to a different request.")

    requests = pd.json_normalize(request["records"])
    results = pd.json_normalize(result["records"])
    rows = requests[["request_id", "street_segment_id", "prediction_time_utc"]].merge(
        results,
        on="request_id",
        how="outer",
        indicator=True,
        validate="one_to_one",
    )
    if not rows["_merge"].eq("both").all():
        raise ValueError("The climate request and result contain different records.")

    return rows.drop(columns="_merge"), predicted.returncode


def normalize_2024_climate_hour(clock, native, exit_code, common_contexts, declared_gap_hours):
    if clock["scenario"] != "2024":
        raise ValueError("Use a 2024 replay clock for this climate loader.")

    cutoff = clock["as_of_utc"]
    road_key = "climate_street_segment_id"
    approved_roads = set(common_contexts[road_key])
    road_ids = native["street_segment_id"]

    if road_ids.duplicated().any() or set(road_ids) != approved_roads:
        raise ValueError("Expected one climate response for every approved road.")
    if not pd.to_datetime(native["prediction_time_utc"], utc=True).eq(cutoff).all():
        raise ValueError("Climate request times do not match the replay cutoff.")

    status = native["status"].astype("string")
    if status.isna().any() or exit_code != int(status.eq("error").any()):
        raise ValueError("The native exit code and record statuses disagree.")

    valid = status.eq("ok")
    declared_gap = (
        status.eq("error")
        & native["reason_code"].eq("MISSING_REQUIRED_FEATURE")
        & (cutoff in declared_gap_hours)
    )

    fields = [
        "street_segment_id", "prediction_time_utc", "target_start_utc",
        "target_end_utc", "latest_rainfall_end_utc", "reported_flooding_score",
        "score_calibrated", "model_alert", "model_alert_threshold",
        "availability_policy_id",
    ]
    result = native.reindex(columns=[f"result.{field}" for field in fields]).copy()
    result.columns = fields

    if not result.loc[valid, "street_segment_id"].eq(road_ids.loc[valid]).all():
        raise ValueError("Climate result road IDs do not match the requests.")

    for field, expected in [
        ("prediction_time_utc", cutoff),
        ("target_start_utc", clock["target_start_utc"]),
        ("target_end_utc", clock["target_end_utc"]),
    ]:
        times = pd.to_datetime(result.loc[valid, field], utc=True)
        if not times.eq(expected).all():
            raise ValueError(f"Climate {field} does not match the replay clock.")

    rainfall_end = pd.to_datetime(result["latest_rainfall_end_utc"], utc=True)
    if not rainfall_end.loc[valid].le(cutoff - pd.Timedelta(hours=3)).all():
        raise ValueError("Climate rainfall evidence exceeds the availability cutoff.")

    scores = pd.to_numeric(result["reported_flooding_score"], errors="coerce")
    alerts = result["model_alert"]
    if scores.loc[valid].isna().any() or not alerts.loc[valid].isin([True, False]).all():
        raise ValueError("A valid climate result is missing its score or alert flag.")

    return pd.DataFrame({
        road_key: road_ids,
        "climate_status": status.mask(declared_gap, "unavailable"),
        "climate_native_status": status,
        "climate_native_reason_code": native["reason_code"],
        "climate_reported_flooding_score": scores.where(valid),
        "climate_model_alert": alerts.where(valid).astype("boolean"),
        "climate_latest_rainfall_end_utc": rainfall_end.where(valid),
        "climate_score_calibrated": result["score_calibrated"].where(valid),
        "climate_model_alert_threshold": result["model_alert_threshold"].where(valid),
        "climate_availability_policy_id": result["availability_policy_id"].where(valid),
    })


def load_2024_climate_hour(clock, python_path, inputs_path, model_path, common_contexts):
    """Read the declared gaps and run the frozen model in its own environment."""
    if clock["scenario"] != "2024":
        raise ValueError("Use a 2024 replay clock for this climate loader.")

    python_path = Path(python_path).resolve()
    inputs_path = Path(inputs_path).resolve()
    model_path = Path(model_path).resolve()
    ledger = pd.read_csv(inputs_path / "unavailable_prediction_hours.csv")
    time_field = next(
        (field for field in ("prediction_time_utc", "target_start_utc") if field in ledger),
        None,
    )
    if time_field is None:
        raise ValueError("The climate gap ledger has no prediction time field.")
    gaps = set(pd.to_datetime(ledger[time_field], format="ISO8601", utc=True).dropna())
    native, exit_code = run_climate_with_request(
        python_path, inputs_path, model_path, clock["target_start_utc"]
    )
    return normalize_2024_climate_hour(clock, native, exit_code, common_contexts, gaps)


def release_reports_at(clock, report_index, warning_index):
    reports = report_index.loc[report_index["availability_utc_assumed"].le(clock["as_of_utc"])]
    warning_links = warning_index.loc[warning_index["incident_id"].isin(reports["incident_id"])]
    return reports, warning_links


def assemble_replay_hour(
    clock, *, common_contexts, mobility, climate, traversals, direction_bridge,
    base_arcs, report_index, warning_index,
):
    """Join the selected forecast hour and release its reports by availability."""
    context_keys = ["segment_id", "direction", "road_type"]
    road_key = "climate_street_segment_id"
    traffic_fields = ["mobility_p10", "mobility_p50", "mobility_p90"]
    snapshot = (
        common_contexts[context_keys + [road_key]]
        .merge(mobility[context_keys + traffic_fields], on=context_keys, validate="one_to_one")
        .merge(climate, on=road_key, validate="many_to_one")
    )
    if len(snapshot) != len(common_contexts):
        raise ValueError("The selected hour is missing approved road-direction contexts.")

    edges = attach_predictions_to_lion(snapshot, traversals, direction_bridge)
    links = edges.explode("source_fids").rename(columns={
        "source_fids": "source_fid",
        "candidate_lion_id": "lion_segment_id",
        "segment_id": "mobility_segment_id",
    })
    links["source_fid"] = links["source_fid"].astype("int64")
    link_keys = [
        "source_fid", "lion_segment_id",
        "from_node_id", "from_node_level", "to_node_id", "to_node_level",
    ]
    graph_fields = link_keys + [
        "length_m", "travel_sense", "from_node_index", "to_node_index",
    ]
    arcs = base_arcs[graph_fields].merge(
        links, on=link_keys, how="left", indicator="forecast_link", validate="many_to_one"
    )
    linked_keys = arcs.loc[arcs["forecast_link"].eq("both"), link_keys].drop_duplicates()
    if len(linked_keys) != len(links):
        raise ValueError("Some forecast links are missing from the supplied graph.")
    arcs["mobility_edge_status"] = arcs["mobility_edge_status"].fillna("no_model_link")

    reports, warning_links = release_reports_at(clock, report_index, warning_index)
    return {
        "clock": clock,
        "climate": climate,
        "snapshot": snapshot,
        "reports": reports,
        "warning_links": warning_links,
        "arcs": arcs,
    }
