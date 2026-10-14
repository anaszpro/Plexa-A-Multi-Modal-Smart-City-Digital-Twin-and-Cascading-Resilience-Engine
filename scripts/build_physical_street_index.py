"""Give identical reverse-direction street edges a shared segment ID."""

import hashlib
import os
import tempfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/raw/nyc_streets.parquet"
OUTPUT = ROOT / "data/processed/physical_street_index_v1.parquet"


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    if OUTPUT.exists():
        raise SystemExit(
            f"Output already exists: {OUTPUT}\n"
            "Stopped to avoid replacing an existing street index."
        )

    streets = gpd.read_parquet(SOURCE)
    required = {"u", "v", "key", "geometry"}
    missing = required.difference(streets.columns)
    if missing:
        raise ValueError(f"Missing street columns: {sorted(missing)}")

    if streets.empty or streets.crs is None:
        raise ValueError("Street network must be nonempty and have a CRS.")

    keys = ["u", "v", "key"]
    if streets[keys].isna().any().any():
        raise ValueError("Street edge keys contain missing values.")
    if streets.duplicated(keys).any():
        raise ValueError("Street edge keys are not unique.")

    geometry = streets.geometry
    if geometry.isna().any() or geometry.is_empty.any():
        raise ValueError("Street network contains missing or empty geometry.")
    if not geometry.is_valid.all():
        raise ValueError("Street network contains invalid geometry.")
    if not geometry.geom_type.eq("LineString").all():
        raise ValueError("Expected LineString street geometries.")

    # Normalization gives a line and its reversal the same representation.
    # Endpoint IDs prevent different network locations from being merged.
    normalized = shapely.to_wkb(
        shapely.normalize(geometry.array),
        byte_order=1,
    )

    segment_ids = []
    for u, v, shape_bytes in zip(streets.u, streets.v, normalized):
        endpoints = f"{min(int(u), int(v))}:{max(int(u), int(v))}:"
        digest = hashlib.sha256(endpoints.encode() + shape_bytes).hexdigest()
        segment_ids.append(digest)

    index = streets[keys].copy()
    index["street_segment_id"] = segment_ids
    index["street_network_sha256"] = file_sha256(SOURCE)
    index["street_network_crs"] = streets.crs.to_string()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".physical_street_index_",
        suffix=".parquet",
        dir=OUTPUT.parent,
    )
    os.close(descriptor)

    try:
        index.to_parquet(temporary, index=False)
        restored = pd.read_parquet(temporary)
        pd.testing.assert_frame_equal(index.reset_index(drop=True), restored)
        os.replace(temporary, OUTPUT)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    print(f"Directed edges: {len(index):,}")
    print(f"Physical segments: {index.street_segment_id.nunique():,}")
    print("Every directed edge retains its original u, v, key.")
    print(f"Saved: {OUTPUT}")


if __name__ == "__main__":
    main()
