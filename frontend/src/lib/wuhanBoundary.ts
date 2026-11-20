import boundaryData from "../data/wuhanBoundary.json" with { type: "json" };
import type { Point } from "../types";

export type WuhanRing = [number, number][];
export type WuhanBoundary = {
  source: string;
  sourceUrl: string;
  license: string;
  divisionCode: "420100";
  name: string;
  coordinateSystem: "GCJ-02";
  originalCoordinateSystem: "WGS84";
  dataDate: string;
  structure: "MultiPolygon with polygon interior rings";
  polygons: WuhanRing[][];
  rings: WuhanRing[];
};

const metadata = boundaryData.metadata;
const coordinates = boundaryData.features[0].geometry.coordinates as number[][][][];
const geometry: WuhanRing[][] = coordinates.map((polygon) =>
  polygon.map((ring) => ring.map(([lng, lat]) => [lng, lat] as [number, number])),
);
const boundary: WuhanBoundary = {
  source: metadata.source,
  sourceUrl: metadata.sourceUrl,
  license: metadata.license,
  divisionCode: metadata.divisionCode as "420100",
  name: metadata.name,
  coordinateSystem: "GCJ-02",
  originalCoordinateSystem: "WGS84",
  dataDate: metadata.dataDate,
  structure: "MultiPolygon with polygon interior rings",
  polygons: geometry,
  rings: geometry.flat(),
};

/** Static licensed boundary data. This resolves locally and never makes a network request. */
export function loadWuhanBoundary(): Promise<WuhanBoundary> {
  return Promise.resolve(boundary);
}

export function getWuhanBoundary(): WuhanBoundary { return boundary; }

function pointOnSegment(point: Point, a: [number, number], b: [number, number]) {
  const cross = (point.lat - a[1]) * (b[0] - a[0]) - (point.lng - a[0]) * (b[1] - a[1]);
  const scale = Math.max(1, Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1]));
  if (Math.abs(cross) > 1e-10 * scale) return false;
  return point.lng >= Math.min(a[0], b[0]) - 1e-10 && point.lng <= Math.max(a[0], b[0]) + 1e-10 &&
    point.lat >= Math.min(a[1], b[1]) - 1e-10 && point.lat <= Math.max(a[1], b[1]) + 1e-10;
}

function inRing(point: Point, ring: WuhanRing): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[j], b = ring[i];
    if (pointOnSegment(point, a, b)) return true;
    const crosses = (a[1] > point.lat) !== (b[1] > point.lat) &&
      point.lng < ((b[0] - a[0]) * (point.lat - a[1])) / (b[1] - a[1]) + a[0];
    if (crosses) inside = !inside;
  }
  return inside;
}

/** Missing/invalid coordinates and malformed geometry always fail closed. */
export function isInsideWuhan(point: Point | null | undefined): boolean {
  if (!point || !Number.isFinite(point.lng) || !Number.isFinite(point.lat) ||
      point.lng < -180 || point.lng > 180 || point.lat < -90 || point.lat > 90) return false;
  for (const polygon of boundary.polygons) {
    if (!polygon.length || !inRing(point, polygon[0])) continue;
    // GeoJSON defines the first ring as the shell and subsequent rings as holes.
    // Points on any boundary are included by product policy.
    let inHole = false;
    for (const hole of polygon.slice(1)) {
      if (hole.some((vertex, i) => pointOnSegment(point, vertex, hole[(i + 1) % hole.length]))) return true;
      if (inRing(point, hole)) { inHole = true; break; }
    }
    if (!inHole) return true;
  }
  return false;
}

export function getWuhanBounds() {
  const points = boundary.rings.flat();
  if (!points.length) return null;
  return {
    west: Math.min(...points.map(([lng]) => lng)),
    east: Math.max(...points.map(([lng]) => lng)),
    south: Math.min(...points.map(([, lat]) => lat)),
    north: Math.max(...points.map(([, lat]) => lat)),
  };
}
