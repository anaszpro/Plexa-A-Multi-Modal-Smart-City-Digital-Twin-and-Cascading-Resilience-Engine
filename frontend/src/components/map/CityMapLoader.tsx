"use client";

import dynamic from "next/dynamic";

const CityMap = dynamic(() => import("./CityMap"), {
  ssr: false,
  loading: () => (
    <div className="map-placeholder" role="status">
      <p>Loading city map…</p>
    </div>
  ),
});

export default function CityMapLoader() {
  return <CityMap />;
}