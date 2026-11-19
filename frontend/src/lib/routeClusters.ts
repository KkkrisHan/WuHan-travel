import type { DayPlan, Place, Point } from "../types";

export type RouteSequenceStop = {
  order: number;
  day: number;
  place: Place;
  stopId: string;
};
export type RouteCluster = {
  items: RouteSequenceStop[];
  location: Point;
  screenX: number;
  screenY: number;
};

export const ROUTE_DAY_COLORS = [
  "#fb845b", "#9d7df0", "#3eace9", "#e8ac3f",
  "#ee7fa7", "#44baa4", "#dd8b35", "#b2c955",
];

export function sequenceStops(days: DayPlan[]): RouteSequenceStop[] {
  let order = 0;
  return days.flatMap((day, dayIndex) =>
    day.stops.map((stop) => ({
      order: ++order,
      day: dayIndex + 1,
      place: stop.place,
      stopId: stop.id,
    })),
  );
}

/**
 * Cluster by rendered pixel distance, not geographical distance.
 * Every visit stays in items, including A -> B -> A return visits.
 * Recomputed on zoom/move to split nearby pins as the user zooms in.
 */
export function clusterRouteStops(
  stops: RouteSequenceStop[],
  project: (point: Point) => { x: number; y: number },
  threshold = 40,
): RouteCluster[] {
  const clusters: RouteCluster[] = [];
  for (const stop of stops) {
    const xy = project(stop.place.location);
    if (!Number.isFinite(xy.x) || !Number.isFinite(xy.y)) continue;
    const existing = clusters.find((group) =>
      Math.hypot(group.screenX - xy.x, group.screenY - xy.y) <= threshold,
    );
    if (existing) {
      const count = existing.items.length;
      existing.screenX = (existing.screenX * count + xy.x) / (count + 1);
      existing.screenY = (existing.screenY * count + xy.y) / (count + 1);
      existing.location.lng = (existing.location.lng * count + stop.place.location.lng) / (count + 1);
      existing.location.lat = (existing.location.lat * count + stop.place.location.lat) / (count + 1);
      existing.items.push(stop);
    } else {
      clusters.push({
        items: [stop],
        location: { ...stop.place.location },
        screenX: xy.x,
        screenY: xy.y,
      });
    }
  }
  return clusters;
}
