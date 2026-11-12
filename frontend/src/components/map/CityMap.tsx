"use client";

import { useEffect, useState } from "react";
import { latLngBounds, type Map as LeafletMap } from "leaflet";
import {
  CircleMarker,
  MapContainer,
  Polyline,
  TileLayer,
  Tooltip,
  useMap,
} from "react-leaflet";
import type { CityMapProps } from "./types";
import type { Incident } from "@/types/incident";
import type { RouteResponse } from "@/lib/routes/schema";
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

function fitRoutes(map: LeafletMap, response: RouteResponse) {
  if (!response.route_found) {
    return;
  }

  const positions: [number, number][] = [
    [response.origin.latitude, response.origin.longitude],
    [response.destination.latitude, response.destination.longitude],
  ];

  for (const route of [
    response.routes.shortest,
    response.routes.recommended,
  ]) {
    for (const feature of route.geometry.features) {
      for (const [longitude, latitude] of feature.geometry.coordinates) {
        positions.push([latitude, longitude]);
      }
    }
  }

  map.fitBounds(latLngBounds(positions), {
    padding: [40, 40],
    maxZoom: 17,
    animate: false,
  });
}

function RouteLayers({ response }: { response: RouteResponse }) {
  const map = useMap();

  useEffect(() => {
    fitRoutes(map, response);
  }, [map, response]);

  if (!response.route_found) {
    return null;
  }

  const alternatives = [
    {
      key: "shortest",
      label: "Shortest route",
      color: "#475569",
      dashArray: "8 6",
      route: response.routes.shortest,
    },
    {
      key: "recommended",
      label: "Recommended route",
      color: "#2459b8",
      dashArray: undefined,
      route: response.routes.recommended,
    },
  ];

  return (
    <>
      {alternatives.map(({ key, label, color, dashArray, route }) =>
        route.geometry.features.map((feature, index) => (
          <Polyline
            key={`${key}-${index}`}
            positions={feature.geometry.coordinates.map(
              ([longitude, latitude]): [number, number] => [
                latitude,
                longitude,
              ]
            )}
            pathOptions={{
              color,
              weight: 5,
              opacity: 0.9,
              dashArray,
            }}
          >
            <Tooltip sticky>
              {label} · {feature.properties.street}
            </Tooltip>
          </Polyline>
        ))
      )}

      <CircleMarker
        center={[response.origin.latitude, response.origin.longitude]}
        radius={16}
        pathOptions={{
          color: "#172335",
          weight: 3,
          fill: false,
        }}
      >
        <Tooltip permanent direction="top" offset={[0, -18]}>
          Origin
        </Tooltip>
      </CircleMarker>

      <CircleMarker
        center={[
          response.destination.latitude,
          response.destination.longitude,
        ]}
        radius={12}
        pathOptions={{
          color: "#172335",
          weight: 3,
          fillColor: "#ffffff",
          fillOpacity: 1,
        }}
      >
        <Tooltip permanent direction="top" offset={[0, -14]}>
          Destination
        </Tooltip>
      </CircleMarker>
    </>
  );
}

export default function CityMap({
  incidents,
  selectedIncidentId,
  onSelect,
  routeResponse,
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
        <span>
          {locatedIncidents.length} report{" "}
          {locatedIncidents.length === 1 ? "location" : "locations"}
        </span>

        <div className="map-actions">
          {routeResponse?.route_found && (
            <button
              type="button"
              disabled={map === null}
              onClick={() => {
                if (map !== null) {
                  fitRoutes(map, routeResponse);
                }
              }}
            >
              Show routes
            </button>
          )}

          <button
            type="button"
            disabled={!canZoom}
            onClick={zoomToSelectedReport}
          >
            Zoom to report
          </button>
        </div>
      </div>

      {routeResponse && !routeResponse.route_found && (
        <p className="map-route-status" role="status">
          No route found. No route lines are displayed.
        </p>
      )}

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

        {routeResponse && <RouteLayers response={routeResponse} />}

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

      <div
        className="incident-map-legend"
        aria-label="Map legend"
      >
        {routeResponse?.route_found && (
          <>
            <span>
              <i
                className="route-map-swatch route-map-swatch--shortest"
                aria-hidden="true"
              />
              Shortest route
            </span>

            <span>
              <i
                className="route-map-swatch route-map-swatch--recommended"
                aria-hidden="true"
              />
              Recommended route
            </span>
          </>
        )}

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