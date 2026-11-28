import {
  endpointCandidatesResponseSchema,
  routeRequestSchema,
  routeResponseSchema,
} from "./schema";

export async function fetchEndpointCandidates(
  latitude: number,
  longitude: number,
  role: "origin" | "destination",
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({
    latitude: String(latitude),
    longitude: String(longitude),
    role,
    limit: "5",
  });

  const response = await fetch(
    `/api/ida/endpoint-candidates?${params}`,
    { signal },
  );

  if (!response.ok) {
    throw new Error(`Endpoint lookup failed (HTTP ${response.status}).`);
  }

  return endpointCandidatesResponseSchema.parse(await response.json());
}

interface IdaRouteInput {
  as_of_utc: string;
  origin: {
    node_id: string;
    node_level: string;
  };
  destination: {
    node_id: string;
    node_level: string;
  };
}

export async function fetchIdaRoute(
  request: IdaRouteInput,
  signal?: AbortSignal,
) {
  const body = routeRequestSchema.parse({
    scenario: "ida",
    ...request,
  });

  const response = await fetch("/api/ida/routes", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    throw new Error(`Route comparison failed (HTTP ${response.status}).`);
  }

  return routeResponseSchema.parse(await response.json());
}