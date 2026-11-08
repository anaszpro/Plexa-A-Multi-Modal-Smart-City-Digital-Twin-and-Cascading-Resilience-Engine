import type { Incident } from "@/types/incident";

export interface CityMapProps {
  incidents: Incident[];
  selectedIncidentId: string | null;
  onSelect: (incidentId: string) => void;
}