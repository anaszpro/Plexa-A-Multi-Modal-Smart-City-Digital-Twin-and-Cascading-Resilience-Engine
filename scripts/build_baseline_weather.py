"""Build complete, lagged time-window features; missing hours stay missing.

Each output contains cell/hour rows. It deliberately does not expand across streets.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from climate_artifacts import ROOT, new_artifact, sha256, write_json
from fetch_historical_radar import validate_frame


def rolling_features(values, windows):
    """Sum exact consecutive hours, requiring every cell-hour in the window."""
    frame = pd.DataFrame(values)
    return {f'rain_{hours}h_mm': frame.rolling(hours, min_periods=hours).sum().to_numpy()
            for hours in windows}


def build(start, end, radar_dir, reference_path, output, config):
    if start.tz is None or end.tz is None or start >= end:
        raise ValueError('Start/end must be increasing timezone-aware hours.')
    if start != start.floor('h') or end != end.floor('h'):
        raise ValueError('Start/end must align with UTC hours.')
    lag = config['rainfall_availability_lag_hours']
    windows = config['rainfall_windows_hours']
    if lag < 3 or not windows or min(windows) < 1:
        raise ValueError('Invalid lag/windows; historical Pass 2 needs an availability delay.')
    reference = pd.read_parquet(reference_path)
    lats, lons = np.sort(reference.latitude.unique()), np.sort(reference.longitude.unique()) + 360
    grid_id = reference.grid_id.iloc[0]
    validate_frame(reference, reference.ts_utc.iloc[0], lats, lons, grid_id)
    times = pd.date_range(start - pd.Timedelta(hours=lag + max(windows) - 1),
                          end - pd.Timedelta(hours=lag), freq='h', inclusive='left')
    values = np.full((len(times), len(reference)), np.nan)
    missing, invalid, fingerprints = [], [], {}
    for i, timestamp in enumerate(times):
        path = radar_dir / f'radar_{timestamp:%Y%m%d_%H%M%S}.parquet'
        if not path.exists():
            missing.append(timestamp.isoformat())
            continue
        try:
            frame = pd.read_parquet(path)
            validate_frame(frame, timestamp, lats, lons, grid_id)
            values[i] = frame.rain_mm.to_numpy()
            fingerprints[path.name] = sha256(path)
        except (ValueError, OSError) as error:
            invalid.append({'file': path.name, 'error': str(error)})
    features = rolling_features(values, windows)
    predictions = times + pd.Timedelta(hours=lag)
    selected = (predictions >= start) & (predictions < end)
    out = pd.DataFrame({
        'prediction_time_utc': np.repeat(predictions[selected], len(reference)),
        'cell_id': np.tile(reference.cell_id.to_numpy(), int(selected.sum())),
        'grid_id': grid_id,
    })
    for name, array in features.items():
        out[name] = array[selected].ravel()
    out['weather_features_valid'] = out[list(features)].notna().all(axis=1)
    summary = {
        'created_utc': datetime.now(timezone.utc), 'start_utc': start, 'end_utc_exclusive': end,
        'source_grid_id': grid_id, 'rows': len(out),
        'valid_rows': int(out.weather_features_valid.sum()),
        'missing_source_hours': missing, 'invalid_source_files': invalid,
        'source_sha256': fingerprints, 'reference_sha256': sha256(reference_path),
        'preparation_config': config, 'script_sha256': sha256(Path(__file__)),
        'availability': 'Observation end plus configured lag; actual historic publication time is unverified.',
    }
    with new_artifact(output) as destination:
        out.to_parquet(destination / 'weather_features.parquet', index=False)
        write_json(destination / 'manifest.json', summary)
    print(f'{start} -> {end}: {summary["valid_rows"]}/{len(out)} valid cell-hours; '
          f'{len(missing)} absent and {len(invalid)} invalid source hours', flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--radar-dir', type=Path, default=ROOT / 'data/raw/radar_parquet_v2')
    parser.add_argument('--reference', type=Path, default=ROOT / 'data/processed/radar_download_validation/radar_20210902_000000.parquet')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads((ROOT / 'config/climate_preparation.json').read_text())
    build(pd.Timestamp(args.start), pd.Timestamp(args.end), args.radar_dir, args.reference, args.output, config)


if __name__ == '__main__':
    main()
