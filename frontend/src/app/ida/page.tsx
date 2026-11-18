"use client";

import { useEffect, useState } from "react";
import IncidentDetails from "@/components/incidents/IncidentDetails";
import IncidentList from "@/components/incidents/IncidentList";
import CityMapLoader from "@/components/map/CityMapLoader";
import {
  apiIncidentPageSchema,
  type ApiIncidentPage,
} from "@/lib/incidents/api-schema";

async function fetchIncidents(offset: number, signal?: AbortSignal) {
  const params = new URLSearchParams({
    scenario: "ida",
    as_of_utc: "2021-09-02T02:00:00Z",
    limit: "20",
    offset: String(offset),
  });

  const response = await fetch(`/api/ida/incidents?${params}`, { signal });

  if (!response.ok) throw new Error(`HTTP ${response.status}`);

  return apiIncidentPageSchema.parse(await response.json());
}

export default function IdaPage() {
  const [page, setPage] = useState<ApiIncidentPage | null>(null);
  const [selectedIncidentId, setSelectedIncidentId] = useState<string | null>(
    null,
  );
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedIncident =
    page?.incidents.find(
      (incident) => incident.incident_id === selectedIncidentId,
    ) ?? null;

  useEffect(() => {
    const controller = new AbortController();

    void fetchIncidents(0, controller.signal)
      .then((firstPage) => {
        if (!controller.signal.aborted) setPage(firstPage);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setError("Could not load Ida incident reports. Refresh to try again.");
        }
      });

    return () => controller.abort();
  }, []);

  async function loadMore() {
    const nextOffset = page?.pagination.next_offset;
    if (nextOffset == null || loadingMore) return;

    setLoadingMore(true);
    setError(null);

    try {
      const nextPage = await fetchIncidents(nextOffset);
      setPage((currentPage) => ({
        ...nextPage,
        incidents: [...(currentPage?.incidents ?? []), ...nextPage.incidents],
      }));
    } catch {
      setError("Could not load more reports. Try again.");
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <main style={{ padding: "24px" }}>
      <h1>Ida incident reports</h1>
      {error && <p role="alert">{error}</p>}
      {!error && !page && <p role="status">Loading incident reports…</p>}

      {page && (
        <>
          <p role="status">
            Showing {page.incidents.length} of{" "}
            {page.pagination.total.toLocaleString("en-US")} reports.
          </p>

          <IncidentList
            incidents={page.incidents}
            selectedIncidentId={selectedIncidentId}
            displayTimezone={page.display_timezone}
            onSelect={setSelectedIncidentId}
          />

          {page.pagination.next_offset !== null && (
            <button type="button" onClick={loadMore} disabled={loadingMore}>
              {loadingMore ? "Loading…" : "Load more reports"}
            </button>
          )}

          <section className="map-panel" aria-labelledby="ida-map-heading">
            <h2 id="ida-map-heading">Ida incident map</h2>
            <CityMapLoader
              incidents={page.incidents}
              selectedIncidentId={selectedIncidentId}
              onSelect={setSelectedIncidentId}
            />
          </section>

          <section aria-labelledby="ida-report-details">
            <h2 id="ida-report-details">Report details</h2>
            <IncidentDetails incident={selectedIncident} />
          </section>
        </>
      )}
    </main>
  );
}