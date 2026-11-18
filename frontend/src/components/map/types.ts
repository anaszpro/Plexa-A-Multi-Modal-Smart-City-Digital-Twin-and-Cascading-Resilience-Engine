import type { Incident } from "@/types/incident";
import type { ApiIncidentPage } from "@/lib/incidents/api-schema";
import type { RouteResponse } from "@/lib/routes/schema";

export interface CityMapProps {
  incidents: (Incident | ApiIncidentPage["incidents"][number])[];
  selectedIncidentId: string | null;
  onSelect: (incidentId: string) => void;
  routeResponse?: RouteResponse;
}