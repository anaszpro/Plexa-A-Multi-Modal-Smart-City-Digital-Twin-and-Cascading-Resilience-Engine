import type { Incident } from "@/types/incident";
import type { RouteResponse } from "@/lib/routes/schema";

export interface CityMapProps {
  incidents: Incident[];
  selectedIncidentId: string | null;
  onSelect: (incidentId: string) => void;
  routeResponse?: RouteResponse;
}