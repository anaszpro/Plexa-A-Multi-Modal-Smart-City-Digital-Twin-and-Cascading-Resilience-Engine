"""Build one row per physical segment with metric radar and DEM sampling.

SRTM terrain samples are proxies, not surveyed road elevations or water depths.
The supplied network is a current OSM drive-network snapshot, not historic roads.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import shapely
from pyproj import Transformer
from scipy.spatial import cKDTree

from climate_artifacts import ROOT, new_artifact, sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--radar-reference', type=Path, default=ROOT / 'data/processed/radar_download_validation/radar_20210902_000000.parquet')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/processed/baseline_street_features_v1')
    args = parser.parse_args()
    network_path = ROOT / 'data/raw/nyc_streets.parquet'
    index_path = ROOT / 'data/processed/physical_street_index_v1.parquet'
    dem_path = ROOT / 'data/raw/nyc_dem.tif'
    borough_path = ROOT / 'data/raw/nyc_boroughs.geojson'
    streets = gpd.read_parquet(network_path)
    index = pd.read_parquet(index_path)
    if not index.street_network_sha256.eq(sha256(network_path)).all():
        raise ValueError('Street network fingerprint differs from index.')
    keys = ['u', 'v', 'key']
    streets = streets.merge(index[keys + ['street_segment_id']], on=keys, validate='one_to_one')
    if len(streets) != len(index) or streets.street_segment_id.isna().any():
        raise ValueError('Street index does not cover the network.')
    segments = streets.sort_values(keys).drop_duplicates('street_segment_id').to_crs('EPSG:32618')
    segments = segments.reset_index(drop=True)
    geom = segments.geometry.array
    length = shapely.length(geom)
    if not np.isfinite(length).all() or (length <= 0).any():
        raise ValueError('Nonpositive segment length.')
    midpoints = gpd.GeoSeries(shapely.line_interpolate_point(geom, 0.5, normalized=True), crs=segments.crs)
    radar = pd.read_parquet(args.radar_reference)
    if radar.cell_id.duplicated().any() or radar.grid_id.nunique() != 1:
        raise ValueError('Reference must be a single complete radar grid.')
    from fetch_historical_radar import validate_frame
    lats = np.sort(radar.latitude.unique())
    lons = np.sort(radar.longitude.unique()) + 360
    validate_frame(radar, radar.ts_utc.iloc[0], lats, lons, radar.grid_id.iloc[0])
    grid = gpd.GeoSeries(gpd.points_from_xy(radar.longitude, radar.latitude), crs=4326).to_crs(32618)
    distances, nearest = cKDTree(np.column_stack([grid.x, grid.y])).query(
        np.column_stack([midpoints.x, midpoints.y]), k=2)
    geographic = midpoints.to_crs(4326)
    dx, dy = np.diff(np.sort(radar.longitude.unique())).min(), np.diff(lats).min()
    inside = (geographic.x.between(radar.longitude.min() - dx/2, radar.longitude.max() + dx/2)
              & geographic.y.between(lats.min() - dy/2, lats.max() + dy/2)).to_numpy()
    ties = np.isclose(distances[:, 0], distances[:, 1], atol=1e-6, rtol=0)

    # Sample endpoints and intermediate positions no more than 20 metres apart.
    counts = np.maximum(2, np.ceil(length / 20).astype(int) + 1)
    owners = np.repeat(np.arange(len(segments)), counts)
    fractions = np.concatenate([np.linspace(0, 1, n) for n in counts])
    points = shapely.line_interpolate_point(np.asarray(geom)[owners], fractions, normalized=True)
    with rasterio.open(dem_path) as dem:
        transform = Transformer.from_crs(segments.crs, dem.crs, always_xy=True)
        x, y = transform.transform(shapely.get_x(points), shapely.get_y(points))
        samples = np.ma.concatenate(list(dem.sample(zip(x, y), masked=True))).astype(float)
        values = samples.filled(np.nan)
    values[~np.isfinite(values)] = np.nan
    sampled = pd.DataFrame({'owner': owners, 'elevation': values})
    stats = sampled.groupby('owner').elevation.agg(['mean', 'min', 'max', 'count'])
    offsets = np.r_[0, np.cumsum(counts)]
    grade = np.abs(values[offsets[1:] - 1] - values[offsets[:-1]]) / length
    frame = pd.DataFrame({
        'street_segment_id': segments.street_segment_id,
        'length_m': length,
        'cell_id': radar.cell_id.to_numpy()[nearest[:, 0]],
        'grid_id': radar.grid_id.iloc[0],
        'radar_distance_m': distances[:, 0],
        'radar_covered': inside & ~ties & (distances[:, 0] <= 1000),
        'elevation_mean_m': stats['mean'].to_numpy(),
        'terrain_endpoint_grade_abs': grade,
        'terrain_relief_m': (stats['max'] - stats['min']).to_numpy(),
        'dem_valid_fraction': stats['count'].to_numpy() / counts,
    })
    frame['static_features_valid'] = (frame.radar_covered & frame.dem_valid_fraction.eq(1)
                                       & np.isfinite(frame.terrain_endpoint_grade_abs))
    # Borough is descriptive coverage metadata, not proof of network completeness.
    boroughs = gpd.read_file(borough_path).to_crs(segments.crs)
    points_frame = gpd.GeoDataFrame({'row': np.arange(len(frame))}, geometry=midpoints, crs=segments.crs)
    membership = gpd.sjoin(points_frame, boroughs[['boroname', 'geometry']], predicate='intersects')
    names = membership.groupby('row').boroname.agg(lambda x: '|'.join(sorted(set(x))))
    frame['borough_at_midpoint'] = pd.Series(np.arange(len(frame))).map(names).fillna('outside_borough_polygons')
    summary = {
        'created_utc': datetime.now(timezone.utc), 'segments': len(frame),
        'static_features_valid': int(frame.static_features_valid.sum()),
        'radar_not_covered': int((~frame.radar_covered).sum()),
        'incomplete_dem': int(frame.dem_valid_fraction.lt(1).sum()),
        'borough_midpoint_counts': frame.borough_at_midpoint.value_counts().to_dict(),
        'sample_spacing_max_m': 20, 'metric_crs': 'EPSG:32618',
        'caveats': ['SRTM terrain is not surveyed street elevation or drainage capacity.',
                    'Current OSM drive-network geometry is used retrospectively; complete historic street coverage is not established.',
                    'One midpoint radar cell represents each segment; rainfall is not measured separately on each street.'],
        'input_sha256': {str(p.relative_to(ROOT)): sha256(p) for p in
                         [network_path, index_path, dem_path, borough_path, args.radar_reference]},
        'script_sha256': sha256(Path(__file__)),
    }
    with new_artifact(args.output) as out:
        frame.to_parquet(out / 'street_features.parquet', index=False)
        segments[['street_segment_id', 'geometry']].to_crs(4326).to_parquet(out / 'street_segments.parquet', index=False)
        write_json(out / 'manifest.json', summary)
    print(json.dumps(summary, indent=2, default=str))


if __name__ == '__main__':
    main()
