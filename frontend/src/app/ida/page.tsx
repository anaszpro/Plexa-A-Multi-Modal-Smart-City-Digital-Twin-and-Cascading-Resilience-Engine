"use client";

import { useEffect, useState } from "react";
import { z } from "zod";

const incidentPageSchema = z.object({
  pagination: z.object({
    total: z.number().int().nonnegative(),
    next_offset: z.number().int().nonnegative().nullable(),
  }),
  incidents: z.array(
    z.object({
      incident_id: z.string(),
      complaint_type: z.string(),
      descriptor: z.string().nullable(),
    }),
  ),
});

type IncidentPage = z.infer<typeof incidentPageSchema>;

async function fetchIncidents(offset: number, signal?: AbortSignal) {
  const params = new URLSearchParams({
    scenario: "ida",
    as_of_utc: "2021-09-02T02:00:00Z",
    limit: "20",
    offset: String(offset),
  });
  const response = await fetch(`/api/ida/incidents?${params}`, { signal });

  if (!response.ok) throw new Error(`HTTP ${response.status}`);

  return incidentPageSchema.parse(await response.json());
}

export default function IdaPage() {
  const [page, setPage] = useState<IncidentPage | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
        pagination: nextPage.pagination,
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
          <ul>
            {page.incidents.map((incident) => (
              <li key={incident.incident_id}>
                <strong>{incident.complaint_type}</strong>
                {incident.descriptor ? ` — ${incident.descriptor}` : ""}
              </li>
            ))}
          </ul>
          {page.pagination.next_offset !== null && (
            <button type="button" onClick={loadMore} disabled={loadingMore}>
              {loadingMore ? "Loading…" : "Load more reports"}
            </button>
          )}
        </>
      )}
    </main>
  );
}