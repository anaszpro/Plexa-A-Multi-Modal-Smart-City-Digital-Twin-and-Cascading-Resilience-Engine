"use client";

import type { RouteResponse } from "@/lib/routes/schema";

interface RouteComparisonProps {
  response: RouteResponse;
  onSelectIncident: (incidentId: string) => void;
}

function formatNumber(value: number) {
  return value.toLocaleString("en-US", {
    maximumFractionDigits: 2,
  });
}

export default function RouteComparison({
  response,
  onSelectIncident,
}: RouteComparisonProps) {
  if (!response.route_found) {
    return (
      <section
        className="route-comparison"
        aria-labelledby="routes-heading"
      >
        <h2 id="routes-heading">Route comparison</h2>
        <p role="status">
          No route found between the selected endpoints.
        </p>
      </section>
    );
  }

  const alternatives = [
    {
      key: "shortest",
      label: "Shortest route",
      route: response.routes.shortest,
    },
    {
      key: "recommended",
      label: "Recommended route",
      route: response.routes.recommended,
    },
  ];

  return (
    <section
      className="route-comparison"
      aria-labelledby="routes-heading"
    >
      <h2 id="routes-heading">Route comparison</h2>

      <p>
        {response.changed_from_shortest
          ? "The engine recommends a different route with a lower routing cost."
          : "The engine recommends the shortest route."}
      </p>

      <div className="route-alternatives">
        {alternatives.map(({ key, label, route }) => (
          <article
            key={key}
            className={`route-card route-card--${key}`}
            aria-labelledby={`route-${key}-heading`}
          >
            <h3 id={`route-${key}-heading`}>{label}</h3>

            <dl>
              <dt>Driving distance</dt>
              <dd>{formatNumber(route.distance_m)} m</dd>

              <dt>Routing cost</dt>
              <dd>
                {formatNumber(route.routing_cost_m)} equivalent metres
              </dd>

              <dt>Traffic estimate coverage</dt>
              <dd>
                {formatNumber(route.traffic_coverage.percent)}% of
                route distance
              </dd>
            </dl>

            <details className="route-penalties">
              <summary>Routing penalties</summary>

              <dl>
                <dt>Estimated traffic</dt>
                <dd>
                  {formatNumber(route.penalties_m.traffic)} m
                </dd>

                <dt>Missing traffic estimates</dt>
                <dd>
                  {formatNumber(route.penalties_m.missing_traffic)} m
                </dd>

                <dt>Road warnings</dt>
                <dd>
                  {formatNumber(route.penalties_m.road_warning)} m
                </dd>
              </dl>
            </details>

            {route.safety_warnings.length > 0 ? (
              <div className="route-warnings">
                <h4>Linked report warnings</h4>

                <ul>
                  {route.safety_warnings.map((warning) => (
                    <li key={warning.incident_id}>
                      <button
                        type="button"
                        onClick={() =>
                          onSelectIncident(warning.incident_id)
                        }
                      >
                        View report {warning.incident_id}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p>No linked Safety report warnings.</p>
            )}
          </article>
        ))}
      </div>

      <p className="field-help">
        Routing cost combines distance and penalties. It is not travel
        time. No linked warnings does not establish that a route is safe.
      </p>
    </section>
  );
}