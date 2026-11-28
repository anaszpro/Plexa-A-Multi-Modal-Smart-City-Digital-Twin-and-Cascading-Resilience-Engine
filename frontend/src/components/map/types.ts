import type { Incident } from "@/types/incident";
import type { ApiIncidentPage } from "@/lib/incidents/api-schema";
import type { RouteResponse } from "@/lib/routes/schema";

export type MapSelectionRole = "origin" | "destination";

export interface MapPoint {
  latitude: number;
  longitude: number;
}

export interface CityMapProps {
  incidents: (Incident | ApiIncidentPage["incidents"][number])[];
  selectedIncidentId: string | null;
  onSelect: (incidentId: string) => void;
  routeResponse?: RouteResponse;
  draftStart?: MapPoint | null;
  draftDestination?: MapPoint | null;
  selectionRole?: MapSelectionRole | null;
  onMapPointSelect?: (point: MapPoint) => void;
}
