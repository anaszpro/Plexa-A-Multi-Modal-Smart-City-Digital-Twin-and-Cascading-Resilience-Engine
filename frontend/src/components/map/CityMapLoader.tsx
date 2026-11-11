"use client";

import dynamic from "next/dynamic";
import type { CityMapProps } from "./types";

const CityMap = dynamic<CityMapProps>(() => import("./CityMap"), {
  ssr: false,
  loading: () => (
    <div className="map-placeholder" role="status">
      <p>Loading city map…</p>
    </div>
  ),
});

export default function CityMapLoader(props: CityMapProps) {
  return <CityMap {...props} />;
}