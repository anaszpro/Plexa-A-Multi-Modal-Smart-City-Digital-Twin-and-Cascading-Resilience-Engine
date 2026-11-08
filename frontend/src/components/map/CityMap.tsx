"use client";

import { useState } from "react";
import type { Map as LeafletMap } from "leaflet";
import {
  CircleMarker,
  MapContainer,
  TileLayer,
  Tooltip,
} from "react-leaflet";
import type { CityMapProps } from "./types";
import type { Incident } from "@/types/incident";
import "leaflet/dist/leaflet.css";

function markerAppearance(incident: Incident) {
  if (incident.classification.status === "skipped") {
    return {
      color: "#64748b",
      label: "Outside current safety rules",
    };
  }

  if (incident.classification.hazard_type === "flood") {
    return {
      color: "#2459b8",
      label: "Flood-rule match",
    };
  }

  return {
    color: "#b45309",
    label: "Road-blockage-rule match",
  };
}

export default function CityMap({
  incidents,
  selectedIncidentId,
  onSelect,
}: CityMapProps) {
  const [map, setMap] = useState<LeafletMap | null>(null);

  const locatedIncidents = incidents.filter(
    (incident) =>
      incident.location.coordinate_status === "provided" &&
      incident.location.latitude !== null &&
      incident.location.longitude !== null
  );

  const selectedIncident = incidents.find(
    (incident) => incident.incident_id === selectedIncidentId
  );

  const selectedLatitude =
    selectedIncident?.location.latitude ?? null;
  const selectedLongitude =
    selectedIncident?.location.longitude ?? null;

  const canZoom =
    map !== null &&
    selectedLatitude !== null &&
    selectedLongitude !== null;

  function zoomToSelectedReport() {
    if (
      map === null ||
      selectedLatitude === null ||
      selectedLongitude === null
    ) {
      return;
    }

    map.setView(
      [selectedLatitude, selectedLongitude],
      Math.max(map.getZoom(), 15)
    );
  }

  return (
    <>
      <div className="incident-map-toolbar">
        <span>{locatedIncidents.length} sample report locations</span>

        <button
          type="button"
          disabled={!canZoom}
          onClick={zoomToSelectedReport}
        >
          Zoom to report
        </button>
      </div>

      <MapContainer
        ref={setMap}
        center={[40.7128, -74.006]}
        zoom={11}
        minZoom={9}
        maxZoom={19}
        scrollWheelZoom={false}
        className="city-map"
      >
        <TileLayer
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          maxZoom={19}
        />

        {locatedIncidents.map((incident) => {
          const { latitude, longitude } = incident.location;

          if (latitude === null || longitude === null) {
            return null;
          }

          const appearance = markerAppearance(incident);
          const isSelected =
            incident.incident_id === selectedIncidentId;

          return (
            <CircleMarker
              key={incident.incident_id}
              center={[latitude, longitude]}
              radius={isSelected ? 11 : 7}
              pathOptions={{
                color: isSelected ? "#111827" : appearance.color,
                fillColor: appearance.color,
                fillOpacity: isSelected ? 1 : 0.8,
                weight: isSelected ? 4 : 2,
              }}
              eventHandlers={{
                click: () => onSelect(incident.incident_id),
              }}
            >
              <Tooltip direction="top">
                <div>
                  <strong>{incident.complaint_type}</strong>
                  <br />
                  {incident.incident_id}
                  <br />
                  {appearance.label} · Unverified
                </div>
              </Tooltip>
            </CircleMarker>
          );
        })}
      </MapContainer>

      <div className="incident-map-legend" aria-label="Report marker legend">
        <span>
          <i className="incident-map-swatch flood" aria-hidden="true" />
          Flood-rule match
        </span>

        <span>
          <i className="incident-map-swatch blockage" aria-hidden="true" />
          Road-blockage-rule match
        </span>

        <span>
          <i className="incident-map-swatch skipped" aria-hidden="true" />
          Outside current safety rules
        </span>

        <span>Selected report: larger marker with dark outline.</span>
        <span>All reports are unverified.</span>
      </div>
    </>
  );
}