import type { Place, Point } from "../types";
import { api } from "./api";

type AMapPoi = {
  id?: string;
  name?: string;
  address?: string | string[];
  type?: string;
  location?: { lng: number; lat: number };
};
type SearchResult = { info?: string; poiList?: { pois?: AMapPoi[] } };
type GeocodeResult = { info?: string; regeocode?: { formattedAddress?: string } };
type Search = {
  getDetails: (id: string, callback: (status: string, result: SearchResult) => void) => void;
  searchNearBy: (keyword: string, center: number[], radius: number, callback: (status: string, result: SearchResult) => void) => void;
};
type Geocoder = {
  getAddress: (point: number[], callback: (status: string, result: GeocodeResult) => void) => void;
};
type Constructors = {
  PlaceSearch: new (options: Record<string, unknown>) => Search;
  Geocoder: new (options?: Record<string, unknown>) => Geocoder;
};

const withTimeout = <T>(start: (finish: (value: T) => void) => void): Promise<T> =>
  new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error("高德服务响应超时，请稍后重试")), 12000);
    try {
      start((value) => {
        window.clearTimeout(timer);
        resolve(value);
      });
    } catch (error) {
      window.clearTimeout(timer);
      reject(error);
    }
  });

const address = (value: AMapPoi["address"]) =>
  Array.isArray(value) ? value.join("") : value || "地址未知";

function fromPoi(poi: AMapPoi, source: "hotspot" | "nearby_search", fallback: Point): Place {
  return {
    placeId: poi.id || `coord:${fallback.lng.toFixed(6)},${fallback.lat.toFixed(6)}`,
    poiId: poi.id || null,
    name: poi.name || "地图位置",
    category: poi.type?.split(";")[0] || "地点",
    address: address(poi.address),
    location: poi.location ? { lng: poi.location.lng, lat: poi.location.lat } : fallback,
    source,
    description: "",
  };
}

function distance(a: Point, b: Point) {
  const radians = (degrees: number) => degrees * Math.PI / 180;
  const north = radians(b.lat - a.lat) * 6371000;
  const east = radians(b.lng - a.lng) * 6371000 * Math.cos(radians(a.lat));
  return Math.hypot(north, east);
}

export function createMapExplorer(AMap: unknown) {
  const constructors = AMap as Constructors;
  const search = new constructors.PlaceSearch({ pageSize: 20, extensions: "base" });
  const geocoder = new constructors.Geocoder();
  const coordinate = async (point: Point) => {
    let formatted = "地址未知";
    let warning = "附近没有合适的 POI，已按点击坐标创建地点。";
    try {
      const result = await withTimeout<{ status: string; value: GeocodeResult }>((finish) =>
        geocoder.getAddress([point.lng, point.lat], (status, value) => finish({ status, value })),
      );
      if (result.status === "complete" && result.value.regeocode?.formattedAddress)
        formatted = result.value.regeocode.formattedAddress;
      else warning = "附近没有合适的 POI；逆地理编码失败，仍可保存此坐标。";
    } catch {
      warning = "附近没有合适的 POI；地址查询失败，仍可保存此坐标。";
    }
    return {
      places: [{
        placeId: `coord:${point.lng.toFixed(6)},${point.lat.toFixed(6)}`,
        poiId: null,
        name: "地图位置",
        category: "坐标地点",
        address: formatted,
        location: point,
        source: "coordinate",
        description: "",
      } satisfies Place],
      note: warning,
    };
  };
  return {
    async hotspot(id: string, name: string, point: Point) {
      try {
        const detail = await api.placeDetail(id);
        return { places: [detail.place], note: "" };
      } catch { /* Fall back to the JS SDK when the Web service is unavailable. */ }
      try {
        const result = await withTimeout<{ status: string; value: SearchResult }>((finish) =>
          search.getDetails(id, (status, value) => finish({ status, value })),
        );
        const poi = result.value.poiList?.pois?.[0];
        if (result.status === "complete" && poi)
          return { places: [fromPoi(poi, "hotspot", point)], note: "" };
      } catch { /* Keep the hotspot's own name and coordinates. */ }
      return {
        places: [fromPoi({ id, name }, "hotspot", point)],
        note: "POI 详情查询失败，已显示地图热点提供的基本信息。",
      };
    },
    async nearby(point: Point) {
      try {
        const result = await withTimeout<{ status: string; value: SearchResult }>((finish) =>
          search.searchNearBy("", [point.lng, point.lat], 500, (status, value) => finish({ status, value })),
        );
        if (result.status === "complete") {
          const places = (result.value.poiList?.pois || [])
            .filter((poi) => poi.id && poi.location)
            .map((poi) => fromPoi(poi, "nearby_search", point))
            .filter((poi) => distance(point, poi.location) <= 150)
            .sort((a, b) => distance(point, a.location) - distance(point, b.location))
            .slice(0, 5);
          if (places.length) return { places, note: places.length > 1 ? "请选择点击位置附近的地点。" : "" };
          return coordinate(point);
        }
        if (result.status === "no_data" || result.value.info === "NO_DATA") return coordinate(point);
        const fallback = await coordinate(point);
        return { ...fallback, note: `周边 POI 搜索失败；${fallback.note}` };
      } catch {
        const fallback = await coordinate(point);
        return { ...fallback, note: `周边 POI 搜索失败；${fallback.note}` };
      }
    },
  };
}
