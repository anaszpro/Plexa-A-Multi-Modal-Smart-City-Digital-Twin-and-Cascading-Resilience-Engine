"use client";

import { useEffect, useState } from "react";
import { latLngBounds, type Map as LeafletMap } from "leaflet";
import {
  CircleMarker,
  MapContainer,
  Polyline,
  TileLayer,
  Tooltip,
  useMapEvents,
} from "react-leaflet";
import type { Route, RouteResponse } from "@/lib/routes/schema";
import type { CityMapProps, MapPoint } from "./types";
import "leaflet/dist/leaflet.css";

function markerAppearance(incident: CityMapProps["incidents"][number]) {
  const { status, hazard_type: hazardType } = incident.classification;

  if (status === "skipped") {
    return {
      color: "#64748b",
      label: "Outside current safety rules",
    };
  }

  if (status === "ok" && hazardType === "flood") {
    return {
      color: "#2459b8",
      label: "Flood-rule match",
    };
  }

  if (status === "ok" && hazardType === "road_blockage") {
    return {
      color: "#b45309",
      label: "Road-blockage-rule match",
    };
  }

  return {
    color: "#64748b",
    label: "Safety classification unavailable",
  };
}
type RouteFeatureGeometry = Route["geometry"]["features"][number]["geometry"];

function linesInGeometry(geometry: RouteFeatureGeometry) {
  return geometry.type === "LineString"
    ? [geometry.coordinates]
    : geometry.coordinates;
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
      for (const line of linesInGeometry(feature.geometry)) {
        for (const [longitude, latitude] of line) {
          positions.push([latitude, longitude]);
        }
      }
    }
  }

  map.fitBounds(latLngBounds(positions), {
    padding: [40, 40],
    maxZoom: 17,
    animate: false,
  });
}

function RouteEndpointMarker({
  point,
  label,
}: {
  point: MapPoint;
  label: "Start" | "Destination";
}) {
  const isStart = label === "Start";
  return (
    <CircleMarker
      center={[point.latitude, point.longitude]}
      radius={isStart ? 14 : 11}
      interactive={false}
      pathOptions={{
        color: "#172335",
        weight: 3,
        fill: !isStart,
        fillColor: "#ffffff",
        fillOpacity: 1,
      }}
    >
      <Tooltip permanent direction="top" offset={[0, isStart ? -16 : -13]}>
        {label}
      </Tooltip>
    </CircleMarker>
  );
}

function RouteLayers({ response }: { response: RouteResponse }) {
  const map = useMapEvents({
    resize() {
      fitRoutes(map, response);
    },
  });

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
        route.geometry.features.flatMap((feature, featureIndex) =>
          linesInGeometry(feature.geometry).map((line, lineIndex) => (
            <Polyline
              key={`${key}-${featureIndex}-${lineIndex}`}
              positions={line.map(
                ([longitude, latitude]): [number, number] => [latitude, longitude],
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
          )),
        )
      )}

      <RouteEndpointMarker point={response.origin} label="Start" />
      <RouteEndpointMarker point={response.destination} label="Destination" />
    </>
  );
}
function MapPointPicker({
  onMapPointSelect,
}: {
  onMapPointSelect: NonNullable<CityMapProps["onMapPointSelect"]>;
}) {
  const map = useMapEvents({
    click(event) {
      onMapPointSelect({
        latitude: event.latlng.lat,
        longitude: event.latlng.lng,
      });
    },
    keydown(event) {
      const keyEvent = event.originalEvent;
      if (keyEvent.key !== "Enter" || keyEvent.target !== map.getContainer()) return;
      keyEvent.preventDefault();
      const center = map.getCenter();
      onMapPointSelect({ latitude: center.lat, longitude: center.lng });
    },
  });

  useEffect(() => {
    const container = map.getContainer();
    const previousLabel = container.getAttribute("aria-label");
    container.setAttribute(
      "aria-label",
      "Choose a road point. Move the map with arrow keys, then press Enter to select the crosshair.",
    );
    container.focus();
    return () => {
      if (previousLabel === null) container.removeAttribute("aria-label");
      else container.setAttribute("aria-label", previousLabel);
    };
  }, [map]);

  return null;
}

export default function CityMap({
  incidents,
  selectedIncidentId,
  onSelect,
  routeResponse,
  draftStart,
  draftDestination,
  selectionRole,
  onMapPointSelect,
}: CityMapProps) {
  const [map, setMap] = useState<LeafletMap | null>(null);

  const locatedIncidents = incidents.filter(
    (incident) =>
      incident.location.coordinate_status === "provided" &&
      incident.location.latitude !== null &&
      incident.location.longitude !== null
  );

  const selectedIncident = locatedIncidents.find(
    (incident) => incident.incident_id === selectedIncidentId
  );

  const selectedLatitude = selectedIncident?.location.latitude ?? null;
  const selectedLongitude = selectedIncident?.location.longitude ?? null;

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
              Fit routes to map
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

        {selectionRole && onMapPointSelect && (
          <MapPointPicker onMapPointSelect={onMapPointSelect} />
        )}

        {!routeResponse?.route_found && draftStart && (
          <RouteEndpointMarker point={draftStart} label="Start" />
        )}
        {!routeResponse?.route_found && draftDestination && (
          <RouteEndpointMarker point={draftDestination} label="Destination" />
        )}
        {routeResponse && <RouteLayers response={routeResponse} />}



        {locatedIncidents.map((incident) => {
          const { latitude, longitude } = incident.location;

          if (latitude === null || longitude === null) {
            return null;
          }

          const appearance = markerAppearance(incident);
          const isSelected = incident.incident_id === selectedIncidentId;

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
                  <strong>
                    {incident.complaint_type ?? "Unknown complaint type"}
                  </strong>
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

      <div className="incident-map-legend" aria-label="Map legend">
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
          Outside current safety rules or classification unavailable
        </span>

        <span>Selected report: larger marker with dark outline.</span>
        <span>All reports are unverified.</span>
      </div>
    </>
  );
}
