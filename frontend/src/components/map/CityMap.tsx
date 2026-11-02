"use client";

import { MapContainer, TileLayer } from "react-leaflet";
import "leaflet/dist/leaflet.css";

export default function CityMap() {
  return (
    <MapContainer
      center={[40.7128, -74.006]}
      zoom={11}
      minZoom={9}
      maxZoom={19}
      scrollWheelZoom={false}
      className="city-map"
    >
      <TileLayer
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        maxZoom={19}
      />
    </MapContainer>
  );
}