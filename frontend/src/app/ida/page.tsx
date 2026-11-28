"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import IncidentDetails from "@/components/incidents/IncidentDetails";
import IncidentList from "@/components/incidents/IncidentList";
import CityMapLoader from "@/components/map/CityMapLoader";
import RouteComparison from "@/components/routes/RouteComparison";
import { fetchEndpointCandidates, fetchIdaRoute } from "@/lib/routes/api";
import type { MapPoint, MapSelectionRole } from "@/components/map/types";
import type { EndpointCandidate, RouteResponse } from "@/lib/routes/schema";
import {
  apiIncidentPageSchema,
  type ApiIncidentPage,
} from "@/lib/incidents/api-schema";

const REPLAY_START_MS = Date.parse("2021-09-01T04:00:00Z");
const FRAME_COUNT = 48;
const LAST_FRAME_INDEX = FRAME_COUNT - 1;
const DEFAULT_FRAME_INDEX = 22; // 2021-09-02 02:00 UTC

function getAsOfUtc(frameIndex: number) {
  return new Date(REPLAY_START_MS + frameIndex * 60 * 60 * 1000)
    .toISOString()
    .replace(".000Z", "Z");
}

function formatReplayTime(asOfUtc: string) {
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/New_York",
    timeZoneName: "short",
  }).format(new Date(asOfUtc));
}

async function fetchIncidents(
  asOfUtc: string,
  offset: number,
  signal?: AbortSignal,
  incidentId?: string,
) {
  const params = new URLSearchParams({
    scenario: "ida",
    as_of_utc: asOfUtc,
    limit: incidentId ? "1" : "20",
    offset: String(offset),
  });

  if (incidentId) {
    params.set("incident_id", incidentId);
  }

  const response = await fetch(`/api/ida/incidents?${params}`, { signal });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }

  return apiIncidentPageSchema.parse(await response.json());
}

export default function IdaPage() {
  const [frameIndex, setFrameIndex] = useState(DEFAULT_FRAME_INDEX);
  const [playing, setPlaying] = useState(false);
  const [page, setPage] = useState<ApiIncidentPage | null>(null);
  const [selectedIncidentId, setSelectedIncidentId] = useState<string | null>(
    null,
  );
  const [fetchedIncident, setFetchedIncident] =
    useState<ApiIncidentPage["incidents"][number] | null>(null);
  const [incidentLookupLoading, setIncidentLookupLoading] = useState(false);
  const [incidentLookupError, setIncidentLookupError] = useState<string | null>(
    null,
  );

  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectionRole, setSelectionRole] = useState<MapSelectionRole | null>(
    null,
  );
  const [originCandidates, setOriginCandidates] = useState<EndpointCandidate[]>(
    [],
  );
  const [destinationCandidates, setDestinationCandidates] = useState<
    EndpointCandidate[]
  >([]);
  const [origin, setOrigin] = useState<EndpointCandidate | null>(null);
  const [destination, setDestination] = useState<EndpointCandidate | null>(null);
  const [candidateLoading, setCandidateLoading] = useState(false);
  const [candidateError, setCandidateError] = useState<string | null>(null);
  const [routeResponse, setRouteResponse] = useState<RouteResponse | null>(null);
  const [routeLoading, setRouteLoading] = useState(false);
  const [routeError, setRouteError] = useState<string | null>(null);
  const requestControllerRef = useRef<AbortController | null>(null);
  const candidateControllerRef = useRef<AbortController | null>(null);
  const routeControllerRef = useRef<AbortController | null>(null);
  const lookupControllerRef = useRef<AbortController | null>(null);

  const asOfUtc = getAsOfUtc(frameIndex);
  const activePage =
    page && Date.parse(page.as_of_utc) === Date.parse(asOfUtc) ? page : null;

  const selectedIncident =
    activePage?.incidents.find(
      (incident) => incident.incident_id === selectedIncidentId,
    ) ??
    (fetchedIncident?.incident_id === selectedIncidentId
      ? fetchedIncident
      : null);
  const clearIncidentLookup = useCallback(() => {
    lookupControllerRef.current?.abort();
    lookupControllerRef.current = null;
    setFetchedIncident(null);
    setIncidentLookupLoading(false);
    setIncidentLookupError(null);
  }, []);

  function handleIncidentSelect(incidentId: string) {
    clearIncidentLookup();
    setSelectedIncidentId(incidentId);
  }

  async function handleWarningIncidentSelect(incidentId: string) {
    const alreadyLoaded = activePage?.incidents.some(
      (incident) => incident.incident_id === incidentId,
    );

    clearIncidentLookup();
    setSelectedIncidentId(incidentId);

    if (alreadyLoaded) return;

    const controller = new AbortController();
    lookupControllerRef.current = controller;
    setIncidentLookupLoading(true);

    try {
      const result = await fetchIncidents(
        asOfUtc,
        0,
        controller.signal,
        incidentId,
      );

      if (controller.signal.aborted) return;

      const incident = result.incidents.find(
        (item) => item.incident_id === incidentId,
      );

      if (incident) {
        setFetchedIncident(incident);
      } else {
        setIncidentLookupError(
          "That report could not be found for this replay hour.",
        );
      }
    } catch (requestError) {
      if (!controller.signal.aborted) {
        setIncidentLookupError(
          requestError instanceof Error
            ? requestError.message
            : "Could not load the selected report.",
        );
      }
    } finally {
      if (lookupControllerRef.current === controller) {
        lookupControllerRef.current = null;
        setIncidentLookupLoading(false);
      }
    }
  }

  const clearRouteResult = useCallback(() => {
    routeControllerRef.current?.abort();
    routeControllerRef.current = null;
    setRouteResponse(null);
    setRouteError(null);
    setRouteLoading(false);
  }, []);

  function beginMapSelection(role: MapSelectionRole) {
    candidateControllerRef.current?.abort();
    candidateControllerRef.current = null;
    setCandidateLoading(false);
    setCandidateError(null);
    setSelectionRole(role);
  }

  async function handleMapPointSelect(point: MapPoint) {
    const role = selectionRole;
    if (!role) return;

    candidateControllerRef.current?.abort();

    const controller = new AbortController();
    candidateControllerRef.current = controller;

    setSelectionRole(null);
    setCandidateLoading(true);
    setCandidateError(null);
    clearRouteResult();

    if (role === "origin") {
      setOrigin(null);
      setOriginCandidates([]);
    } else {
      setDestination(null);
      setDestinationCandidates([]);
    }

    try {
      const result = await fetchEndpointCandidates(
        point.latitude,
        point.longitude,
        role,
        controller.signal,
      );

      if (controller.signal.aborted) return;

      if (result.candidates.length === 0) {
        setCandidateError(`No road nodes found near the selected ${role}. Click another map location.`);
        return;
      }

      if (role === "origin") {
        setOriginCandidates(result.candidates);
      } else {
        setDestinationCandidates(result.candidates);
      }
    } catch (requestError) {
      if (!controller.signal.aborted) {
        setCandidateError(
          requestError instanceof Error
            ? requestError.message
            : "Could not find nearby road nodes.",
        );
      }
    } finally {
      if (!controller.signal.aborted) {
        setCandidateLoading(false);
        candidateControllerRef.current = null;
      }
    }
  }

  async function compareRoutes() {
    if (!origin || !destination) return;

    routeControllerRef.current?.abort();

    const controller = new AbortController();
    routeControllerRef.current = controller;

    setRouteResponse(null);
    setRouteError(null);
    setRouteLoading(true);

    try {
      const result = await fetchIdaRoute(
        {
          as_of_utc: asOfUtc,
          origin: {
            node_id: origin.node_id,
            node_level: origin.node_level,
          },
          destination: {
            node_id: destination.node_id,
            node_level: destination.node_level,
          },
        },
        controller.signal,
      );

      if (!controller.signal.aborted) {
        setRouteResponse(result);
      }
    } catch (requestError) {
      if (!controller.signal.aborted) {
        setRouteError(
          requestError instanceof Error
            ? requestError.message
            : "Route comparison failed.",
        );
      }
    } finally {
      if (!controller.signal.aborted) {
        setRouteLoading(false);
        routeControllerRef.current = null;
      }
    }
  }
  const resetRoutePlanner = useCallback(() => {
    candidateControllerRef.current?.abort();
    candidateControllerRef.current = null;
    setSelectionRole(null);
    setOriginCandidates([]);
    setDestinationCandidates([]);
    setOrigin(null);
    setDestination(null);
    setCandidateLoading(false);
    setCandidateError(null);
    clearRouteResult();
  }, [clearRouteResult]);
  function changeFrame(nextIndex: number) {
    const boundedIndex = Math.max(
      0,
      Math.min(LAST_FRAME_INDEX, nextIndex),
    );

    requestControllerRef.current?.abort();
    clearIncidentLookup();
    resetRoutePlanner();
    setPlaying(false);
    setPage(null);
    setSelectedIncidentId(null);
    setLoadingMore(false);
    setError(null);
    setFrameIndex(boundedIndex);
  }

  useEffect(() => {
    const controller = new AbortController();

    requestControllerRef.current?.abort();
    requestControllerRef.current = controller;


    void fetchIncidents(asOfUtc, 0, controller.signal)
      .then((firstPage) => {
        if (!controller.signal.aborted) {
          setPage(firstPage);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setPlaying(false);
          setError("Could not load Ida incident reports. Refresh to try again.");
        }
      });

    return () => {
      controller.abort();

      if (requestControllerRef.current === controller) {
        requestControllerRef.current = null;
      }
    };
  }, [asOfUtc]);

  useEffect(() => {
    if (!playing || !activePage || frameIndex >= LAST_FRAME_INDEX) return;

    const timer = window.setTimeout(() => {
      const nextIndex = Math.min(LAST_FRAME_INDEX, frameIndex + 1);

      requestControllerRef.current?.abort();
      clearIncidentLookup();
      resetRoutePlanner();
      setPage(null);
      setSelectedIncidentId(null);
      setLoadingMore(false);
      setError(null);
      setFrameIndex(nextIndex);

      if (nextIndex === LAST_FRAME_INDEX) {
        setPlaying(false);
      }
    }, 1500);

    return () => window.clearTimeout(timer);
  }, [activePage, clearIncidentLookup, frameIndex, playing, resetRoutePlanner]);

  async function loadMore() {
    const nextOffset = activePage?.pagination.next_offset;
    const controller = requestControllerRef.current;

    if (
      nextOffset == null ||
      loadingMore ||
      !controller ||
      controller.signal.aborted
    ) {
      return;
    }

    setLoadingMore(true);
    setError(null);

    try {
      const nextPage = await fetchIncidents(
        asOfUtc,
        nextOffset,
        controller.signal,
      );

      if (controller.signal.aborted) return;

      setPage((currentPage) => {
        if (
          !currentPage ||
          Date.parse(currentPage.as_of_utc) !== Date.parse(asOfUtc)
        ) {
          return currentPage;
        }

        return {
          ...nextPage,
          incidents: [...currentPage.incidents, ...nextPage.incidents],
        };
      });
    } catch {
      if (!controller.signal.aborted) {
        setError("Could not load more reports. Try again.");
      }
    } finally {
      if (!controller.signal.aborted) {
        setLoadingMore(false);
      }
    }
  }

  return (
    <main className="ida-dashboard">
      <h1>Ida incident reports</h1>

      <section className="timeline-panel ida-replay-panel" aria-labelledby="ida-timeline-heading">
        <h2 id="ida-timeline-heading">Ida replay timeline</h2>
        <p>{formatReplayTime(asOfUtc)} · America/New_York</p>

        <div>
          <button
            type="button"
            onClick={() => changeFrame(frameIndex - 1)}
            disabled={frameIndex === 0}
          >
            Previous hour
          </button>{" "}
          <button
            type="button"
            onClick={() => {
              setPlaying((currentlyPlaying) => !currentlyPlaying);
            }}
            disabled={frameIndex === LAST_FRAME_INDEX}
          >
            {playing ? "Pause" : "Play"}
          </button>{" "}
          <button
            type="button"
            onClick={() => changeFrame(frameIndex + 1)}
            disabled={frameIndex === LAST_FRAME_INDEX}
          >
            Next hour
          </button>
        </div>

        <label htmlFor="ida-replay-hour">Replay hour</label>
        <input
          id="ida-replay-hour"
          type="range"
          min={0}
          max={LAST_FRAME_INDEX}
          step={1}
          value={frameIndex}
          onChange={(event) => changeFrame(Number(event.currentTarget.value))}
          aria-valuetext={`Hour ${frameIndex + 1} of ${FRAME_COUNT}: ${formatReplayTime(asOfUtc)}`}
        />

        <p>
          Hour {frameIndex + 1} of {FRAME_COUNT}
        </p>
      </section>
      <section className="ida-route-planner" aria-labelledby="route-planner-heading">
        <h2 id="route-planner-heading">Route planner</h2>

        <button type="button" onClick={() => beginMapSelection("origin")}>
          Choose origin on map
        </button>{" "}
        <button type="button" onClick={() => beginMapSelection("destination")}>
          Choose destination on map
        </button>

        {selectionRole && (
          <p role="status">Click the map to set the {selectionRole}.</p>
        )}
        {candidateLoading && <p role="status">Finding nearby road nodes…</p>}
        {candidateError && <p role="alert">{candidateError}</p>}

        <label htmlFor="origin-candidate">Origin road node</label>
        <select
          id="origin-candidate"
          value={origin ? `${origin.node_id}:${origin.node_level}` : ""}
          onChange={(event) => {
            const key = event.currentTarget.value;
            setOrigin(
              originCandidates.find(
                (candidate) => `${candidate.node_id}:${candidate.node_level}` === key,
              ) ?? null,
            );
            clearRouteResult();
          }}
        >
          <option value="">Choose an origin</option>
          {originCandidates.map((candidate) => (
            <option
              key={`${candidate.node_id}:${candidate.node_level}`}
              value={`${candidate.node_id}:${candidate.node_level}`}
            >
              {candidate.node_id} ({candidate.node_level}) —{" "}
              {candidate.distance_from_pin_m.toFixed(1)} m from click
            </option>
          ))}
        </select>

        <label htmlFor="destination-candidate">Destination road node</label>
        <select
          id="destination-candidate"
          value={destination ? `${destination.node_id}:${destination.node_level}` : ""}
          onChange={(event) => {
            const key = event.currentTarget.value;
            setDestination(
              destinationCandidates.find(
                (candidate) => `${candidate.node_id}:${candidate.node_level}` === key,
              ) ?? null,
            );
            clearRouteResult();
          }}
        >
          <option value="">Choose a destination</option>
          {destinationCandidates.map((candidate) => (
            <option
              key={`${candidate.node_id}:${candidate.node_level}`}
              value={`${candidate.node_id}:${candidate.node_level}`}
            >
              {candidate.node_id} ({candidate.node_level}) —{" "}
              {candidate.distance_from_pin_m.toFixed(1)} m from click
            </option>
          ))}
        </select>

        <button
          type="button"
          onClick={compareRoutes}
          disabled={!origin || !destination || candidateLoading || routeLoading}
        >
          {routeLoading ? "Comparing routes…" : "Compare routes"}
        </button>

        {routeError && <p role="alert">{routeError}</p>}
      </section>

      {error && <p role="alert">{error}</p>}
      {!error && !activePage && (
        <p role="status">Loading incident reports for this hour…</p>
      )}

      {activePage && (
        <div className="ida-workspace">
          <div className="ida-map-column">
            <section className="map-panel" aria-labelledby="ida-map-heading">
              <h2 id="ida-map-heading">Ida incident map</h2>
              <CityMapLoader
                incidents={activePage.incidents}
                selectedIncidentId={selectedIncidentId}
                onSelect={handleIncidentSelect}
                selectionRole={selectionRole}
                onMapPointSelect={handleMapPointSelect}
                routeResponse={routeResponse ?? undefined}
              />
            </section>

            {routeResponse && (
              <RouteComparison
                response={routeResponse}
                onSelectIncident={handleWarningIncidentSelect}
              />
            )}
          </div>

          <section className="ida-incidents-panel" aria-labelledby="ida-incidents-heading">
            <h2 id="ida-incidents-heading">Incident reports</h2>
            <p role="status">
              Showing {activePage.incidents.length} of{" "}
              {activePage.pagination.total.toLocaleString("en-US")} reports.
            </p>

            <IncidentList
              incidents={activePage.incidents}
              selectedIncidentId={selectedIncidentId}
              displayTimezone={activePage.display_timezone}
              onSelect={handleIncidentSelect}
            />

            {activePage.pagination.next_offset !== null && (
              <button type="button" onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? "Loading…" : "Load more reports"}
              </button>
            )}
          </section>

          <section className="ida-details-panel" aria-labelledby="ida-report-details">
            <h2 id="ida-report-details">Report details</h2>
            {incidentLookupLoading && (
              <p role="status">Loading the report linked to this warning…</p>
            )}
            {incidentLookupError && <p role="alert">{incidentLookupError}</p>}
            <IncidentDetails incident={selectedIncident} />
          </section>
        </div>
      )}
    </main>
  );
}