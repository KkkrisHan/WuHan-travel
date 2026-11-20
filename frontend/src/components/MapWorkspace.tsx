import { useEffect, useMemo, useRef, useState } from "react";
import { loadAMap } from "../lib/amapLoader";
import { MarkdownContent } from "./MarkdownContent";
import {
  IconArrowUpRight,
  IconCheck,
  IconCompass,
  IconMapPin,
  IconMinus,
  IconPlus,
  IconSearch,
  IconSparkles,
  IconX,
} from "@tabler/icons-react";
import { api, apiBase, personalPlaceKey, personalPlaceToPlace, type PersonalMapPlace, type PlaceInfo } from "../lib/api";
import { createMapExplorer } from "../lib/mapExplorer";
import { getWuhanBoundary, getWuhanBounds, isInsideWuhan } from "../lib/wuhanBoundary";
import type { DayPlan, Place, Trip } from "../types";
import { clusterRouteStops, ROUTE_DAY_COLORS, sequenceStops, type RouteSequenceStop } from "../lib/routeClusters";

type Props = {
  trip: Trip;
  featured: Place[];
  selected: Place | null;
  onSelect: (place: Place | null) => void;
  onAddPending: (place: Place) => void;
  onIntro: (place: Place) => void;
  routeDays: DayPlan[];
  routeFocus: boolean;
  routeLabel: string;
  onMapError: (message: string) => void;
  amapJsKey: string;
  amapConfigRevision: string;
  onRequireAmapWebService: (action: () => void) => void;
};
type MapControl = {
  on: (event: string, handler: (event: MapEvent) => void) => void;
  off: (event: string, handler: (event: MapEvent) => void) => void;
  setCenter: (point: number[]) => void;
  setZoom: (zoom: number, immediately?: boolean) => void;
  getZoom: () => number;
  destroy: () => void;
  add: (items: unknown[]) => void;
  addControl: (control: unknown) => void;
  remove: (items: unknown[]) => void;
  resize: () => void;
  lngLatToContainer: (point: unknown) => { x: number; y: number };
  setFitView: (targets?: unknown[], immediately?: boolean, padding?: number[]) => void;
  setZooms: (zooms: [number, number]) => void;
  setMask: (mask: number[][][][]) => void;
  setLimitBounds: (bounds: unknown) => void;
};
type MapEvent = { id?: string; name?: string; lnglat?: { lng: number; lat: number } };
type Discovery = { places: Place[]; note: string };
type PlaceStatus = "itinerary" | "pending" | "saved" | "preview" | "normal";
function MapCanvas({
  places,
  selected,
  onSelect,
  routeDays,
  routeFocus,
  statusById,
  onError,
  amapJsKey,
  amapConfigRevision,
  onDiscoverStart,
  onDiscoverResult,
}: {
  places: Place[];
  selected: Place | null;
  onSelect: (place: Place) => void;
  routeDays: DayPlan[];
  routeFocus: boolean;
  statusById: Record<string, PlaceStatus>;
  onError: (message: string) => void;
  amapJsKey: string;
  amapConfigRevision: string;
  onDiscoverStart: () => void;
  onDiscoverResult: (result: Discovery) => void;
}) {
  const element = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  const [mapStatus, setMapStatus] = useState("正在加载武汉地图…");
  const outsideMaskPath = useRef<SVGPathElement | null>(null);
  const mapRef = useRef<MapControl | null>(null);
  const amapRef = useRef<Awaited<ReturnType<typeof loadAMap>> | null>(null);
  const overlays = useRef<unknown[]>([]);
  const routeOverlays = useRef<unknown[]>([]);
  const [drawerItems, setDrawerItems] = useState<RouteSequenceStop[]>([]);
  const selectRef = useRef(onSelect);
  const startRef = useRef(onDiscoverStart);
  const resultRef = useRef(onDiscoverResult);
  const requestId = useRef(0);
  const previousMapRevision = useRef("");
  const clickTimer = useRef<number | null>(null);
  const suppressUntil = useRef(0);
  useEffect(() => {
    selectRef.current = onSelect;
    startRef.current = onDiscoverStart;
    resultRef.current = onDiscoverResult;
  }, [onSelect, onDiscoverStart, onDiscoverResult]);
  useEffect(() => {
    if (!amapJsKey || !element.current) return;
    let active = true;
    let detach: (() => void) | null = null;
    setMapStatus("正在加载武汉地图…");
    const win = window as typeof window & {
      _AMapSecurityConfig?: { securityJsCode?: string; serviceHost?: string };
    };
    win._AMapSecurityConfig = { serviceHost: apiBase + "/_AMapService" };
    const forceLoaderReset = !!previousMapRevision.current && previousMapRevision.current !== amapConfigRevision;
    previousMapRevision.current = amapConfigRevision;
    loadAMap(amapJsKey, ["AMap.Scale", "AMap.PlaceSearch", "AMap.Geocoder"], forceLoaderReset)
      .then(async (AMap) => {
        if (!active || !element.current) return;
        setMapStatus("正在加载武汉地图…");
        const boundary = getWuhanBoundary();
        if (!active || !element.current) return;
        const bounds = getWuhanBounds();
        if (!bounds) throw new Error("武汉市行政边界数据无效，地点选择已暂停。");
        // AMap's mask accepts a list of independent polygon paths. Keep each shell
        // separate; the live viewport mask below also cuts any interior rings.
        const paths = boundary.polygons.map((polygon) => [polygon[0]]);
        amapRef.current = AMap;
        mapRef.current = new AMap.Map(element.current, {
          center: [114.3055, 30.5928],
          zoom: 8,
          zooms: [5, 18],
          viewMode: "3D",
          pitch: 0,
          rotation: 0,
          rotateEnable: false,
          pitchEnable: false,
          mask: paths,
          mapStyle: "amap://styles/normal",
          showLabel: true,
          isHotspot: true,
          showIndoorMap: false,
          showBuildingBlock: false,
          backgroundColor: "#0b1213",
        }) as MapControl;
        mapRef.current.setMask(paths);
        const southWest = new AMap.LngLat(bounds.west, bounds.south);
        const northEast = new AMap.LngLat(bounds.east, bounds.north);
        mapRef.current.setLimitBounds(new AMap.Bounds(southWest, northEast));
        const maskOutlines = boundary.rings.map((ring) => new AMap.Polygon({
          path: ring,
          strokeColor: "#c4ed88",
          strokeWeight: 1.25,
          strokeOpacity: 0.9,
          fillOpacity: 0,
          zIndex: 180,
        }));
        mapRef.current.add(maskOutlines);
        mapRef.current.addControl(new AMap.Scale());
        const explorer = createMapExplorer(AMap);
        const point = (event: MapEvent) => event.lnglat &&
          Number.isFinite(event.lnglat.lng) && Number.isFinite(event.lnglat.lat)
            ? { lng: event.lnglat.lng, lat: event.lnglat.lat } : null;
        const discover = (work: () => Promise<Discovery>) => {
          const current = ++requestId.current;
          startRef.current();
          work().then((result) => {
            if (active && current === requestId.current) resultRef.current(result);
          }).catch(() => {
            if (active && current === requestId.current)
              resultRef.current({ places: [], note: "地点查询失败，请检查网络、高德配额或安全配置后重试。" });
          });
        };
        const onHotspot = (event: MapEvent) => {
          if (clickTimer.current !== null) window.clearTimeout(clickTimer.current);
          clickTimer.current = null;
          suppressUntil.current = Date.now() + 350;
          const location = point(event);
          if (event.id && location && isInsideWuhan(location)) discover(() => explorer.hotspot(event.id!, event.name || "地图地点", location));
        };
        const onClick = (event: MapEvent) => {
          const location = point(event);
          if (!location || !isInsideWuhan(location) || Date.now() < suppressUntil.current) return;
          if (clickTimer.current !== null) window.clearTimeout(clickTimer.current);
          clickTimer.current = window.setTimeout(() => {
            clickTimer.current = null;
            if (Date.now() >= suppressUntil.current) discover(() => explorer.nearby(location));
          }, 160);
        };
        const onGesture = () => {
          suppressUntil.current = Date.now() + 300;
          if (clickTimer.current !== null) window.clearTimeout(clickTimer.current);
          clickTimer.current = null;
        };
        mapRef.current.on("hotspotclick", onHotspot);
        mapRef.current.on("click", onClick);
        for (const event of ["dragstart", "dragend", "zoomstart", "zoomend"]) mapRef.current.on(event, onGesture);
        let lastFitZoom: number | null = null;
        const updateViewLimits = () => {
          const rect = element.current?.getBoundingClientRect();
          if (!rect || rect.width < 100 || rect.height < 100) return null;
          const mercatorY = (lat: number) => Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
          const worldWidth = (bounds.east - bounds.west) / 360 * 256;
          const worldHeight = (mercatorY(bounds.north) - mercatorY(bounds.south)) / (2 * Math.PI) * 256;
          const fit = Math.log2(Math.min((rect.width - 96) / worldWidth, (rect.height - 96) / worldHeight));
          const minZoom = Math.max(4, Math.min(14, Math.round((fit - 0.25) * 10) / 10));
          const atPreviousFit = lastFitZoom !== null && Math.abs((mapRef.current?.getZoom() || 0) - lastFitZoom) < 0.08;
          mapRef.current?.setZooms([minZoom, 18]);
          if (mapRef.current && (mapRef.current.getZoom() < minZoom || atPreviousFit))
            mapRef.current.setZoom(Math.max(minZoom, Math.min(18, fit)), true);
          lastFitZoom = fit;
          return fit;
        };
        const fitZoom = updateViewLimits();
        const centerLng = (bounds.west + bounds.east) / 2;
        const centerLat = (bounds.south + bounds.north) / 2;
        mapRef.current.setCenter([centerLng, centerLat]);
        if (fitZoom !== null) mapRef.current.setZoom(fitZoom, true);
        const resizeLimits = () => updateViewLimits();
        mapRef.current.on("resize", resizeLimits);
        const map = mapRef.current;
        let outsideMaskFrame: number | null = null;
        const drawOutsideMask = () => {
          const host = element.current;
          if (!host || !outsideMaskPath.current || !map) return;
          const width = host.clientWidth;
          const height = host.clientHeight;
          const rings = boundary.rings.map((ring) => ring.map(([lng, lat]) => {
            const pixel = map.lngLatToContainer([lng, lat]);
            return `${pixel.x.toFixed(2)} ${pixel.y.toFixed(2)}`;
          }).join(" L"));
          const paths = rings.map((points) => `M${points} Z`).join(" ");
          outsideMaskPath.current.setAttribute("d", `M0 0 H${width} V${height} H0 Z ${paths}`);
        };
        const updateOutsideMask = () => {
          if (outsideMaskFrame !== null) return;
          outsideMaskFrame = window.requestAnimationFrame(() => {
            outsideMaskFrame = null;
            drawOutsideMask();
          });
        };
        drawOutsideMask();
        for (const event of ["mapmove", "zoomchange", "resize"]) map.on(event, updateOutsideMask);
        detach = () => {
          map?.off("hotspotclick", onHotspot);
          map?.off("click", onClick);
          for (const event of ["dragstart", "dragend", "zoomstart", "zoomend"]) map?.off(event, onGesture);
          map?.off("resize", resizeLimits);
          for (const event of ["mapmove", "zoomchange", "resize"]) map?.off(event, updateOutsideMask);
          if (outsideMaskFrame !== null) window.cancelAnimationFrame(outsideMaskFrame);
        };
        setReady(true);
      })
      .catch((error) => {
        if (active) {
          const message = error instanceof Error ? error.message : "高德地图加载失败，请检查 JS Key、域名白名单与安全密钥";
          setMapStatus(message);
          onError(message);
        }
      });
    return () => {
      active = false;
      const pendingClick = clickTimer.current;
      if (pendingClick !== null) window.clearTimeout(pendingClick);
      detach?.();
      mapRef.current?.destroy();
      mapRef.current = null;
      setReady(false);
    };
  }, [amapJsKey, amapConfigRevision, onError]);
  useEffect(() => {
    const map = mapRef.current,
      AMap = amapRef.current;
    if (!map || !AMap) return;
    if (overlays.current.length) map.remove(overlays.current);
    const next: unknown[] = [];
    const routeIds = routeFocus ? new Set(routeDays.flatMap((day) => day.stops.map((stop) => stop.place.placeId))) : new Set<string>();
    places.forEach((place) => {
      if (routeIds.has(place.placeId)) return;
      const inRoute = routeDays.some((day) =>
        day.stops.some((stop) => stop.place.placeId === place.placeId),
      );
      const marker = new AMap.Marker({
        position: [place.location.lng, place.location.lat],
        title: place.name,
        content: `<div class="map-pin ${statusById[place.placeId] || "normal"} ${inRoute ? "in-route" : ""} ${selected?.placeId === place.placeId ? "is-selected" : ""}"><span></span></div>`,
        offset: new AMap.Pixel(-12, -12),
      });
      marker.on("click", () => {
        requestId.current++;
        suppressUntil.current = Date.now() + 350;
        if (clickTimer.current !== null) window.clearTimeout(clickTimer.current);
        clickTimer.current = null;
        selectRef.current(place);
      });
      next.push(marker);
    });
    if (!routeFocus) routeDays.forEach((day) => {
      day.legs.forEach((leg) => {
        const points = leg.polyline.map((point) => [point.lng, point.lat]);
        if (points.length > 1)
          next.push(
            new AMap.Polyline({
              path: points,
              strokeColor: "#12aeb6",
              strokeWeight: 5,
              strokeOpacity: 0.85,
              lineJoin: "round",
              zIndex: 60,
            }),
          );
      });
    });
    map.add(next);
    overlays.current = next;
  }, [places, routeDays, routeFocus, selected, statusById, ready]);

  useEffect(() => {
    const map = mapRef.current;
    const AMap = amapRef.current;
    if (!ready || !map || !AMap) return;
    const stops = sequenceStops(routeDays);
    let fitMarkers: unknown[] = [];

    const drawRoute = () => {
      if (routeOverlays.current.length) map.remove(routeOverlays.current);
      routeOverlays.current = [];
      fitMarkers = [];
      if (!routeFocus || !stops.length) return;

      const overlaysForRoute: unknown[] = [];
      for (const [dayIndex, day] of routeDays.entries()) {
        const color = ROUTE_DAY_COLORS[dayIndex % ROUTE_DAY_COLORS.length];
        for (let index = 0; index < day.stops.length - 1; index++) {
          const current = day.stops[index];
          const next = day.stops[index + 1];
          const leg = day.legs.find((item) => item.fromStopId === current.id && item.toStopId === next.id);
          // Use the real road polyline when available; otherwise draw a clearly labelled schematic.
          const path = leg?.polyline && leg.polyline.length > 1
            ? leg.polyline.map((point) => [point.lng, point.lat])
            : [[current.place.location.lng, current.place.location.lat], [next.place.location.lng, next.place.location.lat]];
          if (path.every((point) => point[0] === path[0][0] && point[1] === path[0][1])) continue;
          overlaysForRoute.push(new AMap.Polyline({
            path, strokeColor: color, strokeWeight: 5, strokeOpacity: 0.87,
            showDir: true, dirColor: "#ffffff", lineJoin: "round", zIndex: 105,
            extData: { schematic: !leg?.polyline || leg.polyline.length < 2 },
          }));
        }
      }
      const groups = clusterRouteStops(
        stops,
        (point) => {
          const pixel = map.lngLatToContainer(new AMap.LngLat(point.lng, point.lat));
          return { x: pixel.x, y: pixel.y };
        },
        41,
      );
      for (const group of groups) {
        const orders = group.items.map((item) => item.order);
        const label = group.items.length > 1
          ? (orders.length <= 3 ? orders.join("·") : `${orders.slice(0, 2).join("·")} +${orders.length - 2}`)
          : String(orders[0]);
        const cluster = group.items.length > 1;
        const color = ROUTE_DAY_COLORS[(group.items[0].day - 1) % ROUTE_DAY_COLORS.length];
        const content = document.createElement("div");
        content.className = `route-sequence-pin ${cluster ? "cluster" : ""}`;
        content.style.setProperty("--route-color", color);
        const number = document.createElement("span");
        number.textContent = label;
        content.appendChild(number);
        if (cluster) {
          const arrow = document.createElement("b");
          arrow.textContent = "▾";
          content.appendChild(arrow);
        }
        const activate = () => {
          requestId.current++;
          suppressUntil.current = Date.now() + 500;
          if (clickTimer.current !== null) window.clearTimeout(clickTimer.current);
          clickTimer.current = null;
          if (cluster) setDrawerItems(group.items);
          else selectRef.current(group.items[0].place);
        };
        content.addEventListener("click", (event) => {
          event.stopPropagation();
          activate();
        });
        const marker = new AMap.Marker({
          position: [group.location.lng, group.location.lat],
          title: cluster ? `合并 ${group.items.length} 次行程停靠，点击展开顺序` : `第 ${orders[0]} 站：${group.items[0].place.name}`,
          content,
          offset: new AMap.Pixel(cluster ? -33 : -17, -18),
          zIndex: 160,
        });
        marker.on("click", activate);
        fitMarkers.push(marker);
        overlaysForRoute.push(marker);
      }
      map.add(overlaysForRoute);
      routeOverlays.current = overlaysForRoute;
    };

    drawRoute();
    if (routeFocus && fitMarkers.length) {
      map.setFitView(fitMarkers, false, [86, 75, 85, 75]);
    }
    const update = () => drawRoute();
    map.on("zoomend", update);
    map.on("moveend", update);
    return () => {
      map.off("zoomend", update);
      map.off("moveend", update);
      if (routeOverlays.current.length) map.remove(routeOverlays.current);
      routeOverlays.current = [];
    };
  }, [ready, routeFocus, routeDays]);

  useEffect(() => {
    if (selected && isInsideWuhan(selected.location) && mapRef.current) {
      mapRef.current.setCenter([selected.location.lng, selected.location.lat]);
      mapRef.current.setZoom(Math.max(mapRef.current.getZoom(), 13));
    }
  }, [selected]);
  useEffect(() => {
    if (!mapRef.current || !element.current) return;
    const observer = new ResizeObserver(() => mapRef.current?.resize());
    observer.observe(element.current);
    return () => observer.disconnect();
  }, [amapJsKey]);
  useEffect(() => {
    const zoom = (event: Event) => {
      const detail = (event as CustomEvent<number>).detail;
      if (mapRef.current)
        mapRef.current.setZoom(mapRef.current.getZoom() + detail);
    };
    document.addEventListener("map-zoom", zoom);
    return () => document.removeEventListener("map-zoom", zoom);
  }, []);
  if (!amapJsKey) {
    const positioned: { x: number; y: number }[] = [];
    const pins = places.map((place) => {
      let x = Math.max(
        8,
        Math.min(92, 50 + (place.location.lng - 114.3055) * 245),
      );
      let y = Math.max(
        10,
        Math.min(88, 45 - (place.location.lat - 30.56) * 300),
      );
      while (
        positioned.some(
          (point) => Math.abs(point.x - x) < 6 && Math.abs(point.y - y) < 6,
        )
      ) {
        x = Math.min(92, x + 7);
        y = Math.min(88, y + 5);
      }
      positioned.push({ x, y });
      return (
        <button
          className={`fallback-pin ${statusById[place.placeId] || "normal"} ${selected?.placeId === place.placeId ? "active" : ""}`}
          style={{ left: `${x}%`, top: `${y}%` }}
          title={place.name}
          aria-label={`查看${place.name}`}
          key={place.placeId}
          onClick={() => onSelect(place)}
        >
          <span />
        </button>
      );
    });
    return (
      <div
        className="map-fallback"
        role="img"
        aria-label="武汉地点示意视图，需要高德 JS Key 才能显示真实地图"
      >
        <div className="fallback-grid" />
        <div className="fallback-river" />
        <div className="fallback-water-label">长 江</div>
        <div className="fallback-district district-a">汉口</div>
        <div className="fallback-district district-b">武昌</div>
        <div className="fallback-district district-c">汉阳</div>
        {pins}
        <div className="fallback-notice">
          <IconCompass size={16} /> 地点示意视图 · 配置高德 JS Key
          后显示真实地图
        </div>
      </div>
    );
  }
  return (
    <>
      <div ref={element} className="amap-canvas" aria-label="武汉高德地图" />
      <svg className="wuhan-outside-mask" aria-hidden="true" preserveAspectRatio="none">
        <path ref={outsideMaskPath} fill="#0b1213" fillRule="evenodd" />
      </svg>
      {!ready && <div className="map-region-loading" role="status">{mapStatus}</div>}
      {routeFocus && drawerItems.length > 1 && (
        <div className="route-cluster-drawer" role="group" aria-label="合并的行程地点">
          <div className="route-cluster-drawer-head"><strong>合并站点 · {drawerItems.length} 次停靠</strong><button type="button" onClick={() => setDrawerItems([])} aria-label="关闭站点抽屉"><IconX size={16}/></button></div>
          <p>当前缩放下的多个标记重叠。以下保留每一次经过的顺序，放大地图可拆分标记。</p>
          <div className="route-cluster-items">{drawerItems.map((item) => <button type="button" key={`${item.order}-${item.stopId}`} onClick={() => { selectRef.current(item.place); setDrawerItems([]); }}><span>{item.order}</span><div><strong>{item.place.name}</strong><small>第 {item.day} 天 · 第 {item.order} 站</small></div></button>)}</div>
        </div>
      )}
    </>
  );
}

export function MapWorkspace({
  trip,
  featured,
  selected,
  onSelect,
  onAddPending,
  onIntro,
  routeDays,
  routeFocus,
  routeLabel,
  onMapError,
  amapJsKey,
  amapConfigRevision,
  onRequireAmapWebService,
}: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Place[]>([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const [searchNote, setSearchNote] = useState("");
  const [personalPlaces, setPersonalPlaces] = useState<PersonalMapPlace[]>([]);
  const [personalLoading, setPersonalLoading] = useState(true);
  const [personalError, setPersonalError] = useState("");
  const [personalBusy, setPersonalBusy] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [discoveryNote, setDiscoveryNote] = useState("");
  const [placeInfo, setPlaceInfo] = useState<PlaceInfo | null>(null);
  const infoRequestId = useRef(0);
  const infoController = useRef<AbortController | null>(null);
  const [candidates, setCandidates] = useState<Place[]>([]);
  const discoveryCancelled = useRef(false);
  const personalTrip = useMemo(() => ({ id: trip.id, secret: trip.secret }), [trip.id, trip.secret]);
  const safeRouteDays = useMemo(() => routeDays.map((day) => {
    const stops = day.stops.filter((stop) => isInsideWuhan(stop.place.location));
    const stopIds = new Set(stops.map((stop) => stop.id));
    return {
      ...day,
      stops,
      legs: day.legs.filter((leg) => stopIds.has(leg.fromStopId) && stopIds.has(leg.toStopId) &&
        leg.polyline.every((point) => isInsideWuhan(point))),
    };
  }), [routeDays]);
  useEffect(() => {
    const id = ++infoRequestId.current;
    infoController.current?.abort();
    if (!selected) {
      queueMicrotask(() => { if (id === infoRequestId.current) setPlaceInfo(null); });
      return;
    }
    const controller = new AbortController();
    infoController.current = controller;
    queueMicrotask(() => { if (id === infoRequestId.current) setPlaceInfo(null); });
    api.placeKnowledge(selected, controller.signal).then(info => { if (id === infoRequestId.current) setPlaceInfo(info?.knowledgeBase ? info : null); })
      .catch(() => { if (id === infoRequestId.current && !controller.signal.aborted) setPlaceInfo(null); });
    return () => { infoController.current?.abort(); infoRequestId.current += 1; };
  }, [selected]);
  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      setPersonalLoading(true);
      setPersonalError("");
      setPersonalPlaces([]);
    });
    api.personalPlaces(personalTrip).then((result) => {
      if (active) setPersonalPlaces(result.places);
    }).catch((error) => {
      if (active) setPersonalError(error instanceof Error ? error.message : "个人地图加载失败");
    }).finally(() => {
      if (active) setPersonalLoading(false);
    });
    return () => { active = false; };
  }, [personalTrip]);
  const select = (place: Place | null) => {
    if (place && !isInsideWuhan(place.location)) {
      onMapError("该地点不在武汉市行政区域内，不能作为武汉旅行地点。");
      return;
    }
    discoveryCancelled.current = true;
    setDiscovering(false);
    setCandidates([]);
    setDiscoveryNote("");
    onSelect(place);
  };
  const onDiscoverResult = (result: Discovery) => {
    if (discoveryCancelled.current) return;
    setDiscovering(false);
    setDiscoveryNote(result.note);
    setCandidates(result.places.length > 1 ? result.places : []);
    if (result.places.length === 1) onSelect(result.places[0]);
  };
  const togglePersonal = async (place: Place) => {
    if (!isInsideWuhan(place.location)) {
      setPersonalError("该地点不在武汉市行政区域内，不能加入个人收藏。");
      return;
    }
    if (personalBusy || personalLoading || personalError) return;
    setPersonalBusy(true);
    try {
      const saved = personalPlaces.find((item) => item.place_key === personalPlaceKey(place));
      if (saved) {
        await api.removePersonalPlace(trip, saved.id);
        setPersonalPlaces((items) => items.filter((item) => item.id !== saved.id));
      } else {
        const result = await api.addPersonalPlace(trip, place);
        setPersonalPlaces((items) => [...items.filter((item) => item.id !== result.place.id), result.place]);
      }
    } catch (error) {
      setPersonalError(error instanceof Error ? error.message : "标记操作失败");
    } finally {
      setPersonalBusy(false);
    }
  };
  const reloadPersonal = async () => {
    setPersonalError("");
    setPersonalLoading(true);
    try {
      const result = await api.personalPlaces(trip);
      setPersonalPlaces(result.places);
    } catch (error) {
      setPersonalError(error instanceof Error ? error.message : "个人地图加载失败");
    } finally {
      setPersonalLoading(false);
    }
  };
  const markerInput = JSON.stringify({
    featured,
    results,
    saved: trip.savedPlaces,
    personal: personalPlaces.map((item) => {
      const place = personalPlaceToPlace(item);
      if (item.source === "featured") {
        const match = featured.find((candidate) => personalPlaceKey(candidate) === item.place_key);
        if (match) place.placeId = match.placeId;
      }
      return place;
    }),
    pending: trip.pendingPlaces,
    itinerary: trip.itinerary.flatMap((day) =>
      day.stops.map((stop) => stop.place),
    ),
    route: safeRouteDays.flatMap((day) => day.stops.map((stop) => stop.place)),
  });
  const { places, statusById } = useMemo(() => {
    const data = JSON.parse(markerInput) as {
      featured: Place[];
      results: Place[];
      saved: Place[];
      personal: Place[];
      pending: Place[];
      itinerary: Place[];
      route: Place[];
    };
    const all = [
      ...data.featured,
      ...data.results,
      ...data.saved,
      ...data.personal,
      ...data.pending,
      ...data.itinerary,
      ...data.route,
    ];
    const status: Record<string, PlaceStatus> = {};
    data.saved.forEach((place) => (status[place.placeId] = "saved"));
    data.personal.forEach((place) => (status[place.placeId] = "saved"));
    data.pending.forEach((place) => (status[place.placeId] = "pending"));
    data.itinerary.forEach((place) => (status[place.placeId] = "itinerary"));
    data.route.forEach((place) => {
      if (!status[place.placeId]) status[place.placeId] = "preview";
    });
    return {
      places: [...new Map(all.filter((place) => isInsideWuhan(place.location)).map((place) => [place.placeId, place])).values()],
      statusById: status,
    };
  }, [markerInput]);
  const runSearch = async (searchQuery: string) => {
    setSearching(true);
    setSearchOpen(true);
    try {
      const data = await api.search(searchQuery);
      const localPlaces = data.places.filter((place) => isInsideWuhan(place.location));
      setResults(localPlaces);
      setSearchNote(data.source);
      if (localPlaces[0]) select(localPlaces[0]);
      else setSearchNote("未找到武汉市范围内的相关地点。");
    } catch (error) {
      setSearchNote(error instanceof Error ? error.message : "搜索失败");
    } finally {
      setSearching(false);
    }
  };
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const searchQuery = query.trim();
    if (!searchQuery) return;
    onRequireAmapWebService(() => { void runSearch(searchQuery); });
  };
  const isPending =
    selected &&
    trip.pendingPlaces.some((place) => place.placeId === selected.placeId);
  const personalSaved = selected && personalPlaces.find((item) => item.place_key === personalPlaceKey(selected));
  return (
    <section className="map-workspace" aria-label="地图与地点搜索">
      <MapCanvas
        places={places}
        amapJsKey={amapJsKey}
        amapConfigRevision={amapConfigRevision}
        selected={selected}
        onSelect={select}
        routeDays={safeRouteDays}
        routeFocus={routeFocus}
        statusById={statusById}
        onError={onMapError}
        onDiscoverStart={() => {
          discoveryCancelled.current = false;
          setDiscovering(true);
          setDiscoveryNote("");
          setCandidates([]);
          onSelect(null);
        }}
        onDiscoverResult={onDiscoverResult}
      />
      <div className="map-top">
        <form className="map-search" onSubmit={submit}>
          <IconSearch size={19} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onFocus={() => results.length && setSearchOpen(true)}
            placeholder="搜索武汉的地点、美食、街区"
            aria-label="搜索武汉地点"
          />
          <button type="submit" disabled={searching}>
            {searching ? "搜索中" : "搜索"}
          </button>
        </form>
        {searchOpen && (
          <div className="search-results">
            <div className="search-results-head">
              <span>搜索结果 {results.length}</span>
              <button
                onClick={() => setSearchOpen(false)}
                aria-label="关闭搜索结果"
              >
                <IconX size={17} />
              </button>
            </div>
            {results.length ? (
              results.map((place) => (
                <button
                  key={place.placeId}
                  className="search-result"
                  onClick={() => {
                    select(place);
                    setSearchOpen(false);
                  }}
                >
                  <IconMapPin size={17} />
                  <span>
                    <strong>{place.name}</strong>
                    <small>
                      {place.category} · {place.address}
                    </small>
                  </span>
                  <IconArrowUpRight size={16} />
                </button>
              ))
            ) : (
              <p className="search-empty">
                {searching
                  ? "正在查找地点…"
                  : "未找到武汉市范围内的相关地点。"}
              </p>
            )}
            <div className="search-source">{searchNote}</div>
          </div>
        )}
      </div>
      <div className="map-route-chip">
        <span className="route-indicator" />
        {routeLabel}
        {routeFocus && <small className="route-map-note">箭头表示游览顺序；无导航路径时以直线示意</small>}
      </div>
      <div className="map-legend" aria-label="地图地点图例">
        <span>
          <i className="legend-dot itinerary" />
          行程
        </span>
        <span>
          <i className="legend-dot pending" />
          待安排
        </span>
        <span>
          <i className="legend-dot saved" />
          收藏
        </span>
      </div>
      {(discovering || candidates.length > 0 || (discoveryNote && !selected)) && (
        <div className="discovery-panel" role="status">
          <div className="search-results-head">
            <strong>{discovering ? "正在识别地点…" : candidates.length ? "选择附近地点" : "地点查询"}</strong>
            <button aria-label="关闭地点查询" onClick={() => { discoveryCancelled.current = true; setDiscovering(false); setCandidates([]); setDiscoveryNote(""); }}><IconX size={17} /></button>
          </div>
          {discoveryNote && <p>{discoveryNote}</p>}
          {candidates.map((place) => (
            <button className="discovery-choice" key={place.placeId} onClick={() => select(place)}>
              <strong>{place.name}</strong><small>{place.category} · {place.address}</small>
            </button>
          ))}
          {candidates.length > 0 && <small>点击位置没有精确匹配时，可再次点击地图选择坐标。</small>}
        </div>
      )}
      <div className="map-bottom">
        <div className="map-attribution">
          {amapJsKey ? "© 高德地图 · 行政边界 © OpenStreetMap contributors (ODbL 1.0)" : "示意视图 · 坐标仅供参考"}
        </div>
        <div className="map-zoom">
          <button
            aria-label="放大地图"
            onClick={() => {
              document.dispatchEvent(
                new CustomEvent("map-zoom", { detail: 1 }),
              );
            }}
          >
            <IconPlus size={17} />
          </button>
          <button
            aria-label="缩小地图"
            onClick={() => {
              document.dispatchEvent(
                new CustomEvent("map-zoom", { detail: -1 }),
              );
            }}
          >
            <IconMinus size={17} />
          </button>
        </div>
      </div>
      {selected && (
        <div className="place-card">
          <div className="place-card-top">
            <div>
              <span className="place-category">{selected.category}</span>
              <h3>{selected.name}</h3>
            </div>
            <button
              className="icon-button"
              aria-label="关闭地点卡"
              onClick={() => select(null)}
            >
              <IconX size={19} />
            </button>
          </div>
          <p>{selected.description || "可加入行程，继续了解这个地点。"}</p>
          <div className="place-address">
            <IconMapPin size={15} />
            {selected.address}
          </div>
          <div className="place-coordinates">GCJ-02 · {selected.location.lng.toFixed(6)}, {selected.location.lat.toFixed(6)}</div>
          <div className="place-save-state">
            个人地图：{personalLoading ? "加载中…" : personalError ? "状态读取失败" : personalSaved ? "已标记" : "未标记"}
          </div>
          {discoveryNote && <p role="status">{discoveryNote}</p>}
          {personalError && <div className="place-error" role="alert">{personalError} <button onClick={reloadPersonal}>重试</button></div>}
          {placeInfo?.knowledgeBase && placeInfo.fields.openingHours && <div className="fact-status place-info">
            <div className="place-info-heading"><strong>本地知识库 · 开放时间</strong></div>
            <small>最后核查：{new Date(placeInfo.fields.openingHours.checkedAt || placeInfo.checkedAt).toLocaleString("zh-CN")}</small>
            <dl><div><dt>开放时间</dt><dd>{placeInfo.fields.openingHours.value} {placeInfo.sources[placeInfo.fields.openingHours.sourceId - 1]?.url && <a href={placeInfo.sources[placeInfo.fields.openingHours.sourceId - 1].url} target="_blank" rel="noopener noreferrer">[核实来源]</a>}</dd></div></dl>
            {placeInfo.introduction && <div className="place-info-intro"><h4>地点介绍与游览建议</h4><MarkdownContent>{placeInfo.introduction}</MarkdownContent></div>}
            {placeInfo.sources.length > 0 && <div className="place-info-sources"><h4>信息来源</h4>{placeInfo.sources.map((source,index) => <p key={source.url}><a href={source.url} target="_blank" rel="noopener noreferrer">[{index+1}] {source.title}</a> {source.official ? "官方" : "第三方"} · {source.publishedAt ? `发布 ${source.publishedAt.slice(0,10)}` : "发布时间未标明"}</p>)}</div>}
          </div>}
          <div className="place-actions">
            <button
              className="button secondary"
              disabled={personalLoading || personalBusy || !!personalError}
              onClick={() => togglePersonal(selected)}
            >
              {personalBusy ? "处理中…" : personalSaved ? "取消标记" : "标记"}
            </button>
            <button
              className="button primary"
              disabled={!!isPending}
              onClick={() => onAddPending(selected)}
            >
              {isPending ? <IconCheck size={17} /> : <IconPlus size={17} />}
              {isPending ? "已加入必去景点" : "加入必去景点"}
            </button>
            <button className="button ghost" onClick={() => onIntro(selected)}>
              <IconSparkles size={17} />
              AI 介绍
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
