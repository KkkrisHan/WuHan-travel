import { useEffect, useMemo, useRef, useState } from "react";
import { load } from "@amap/amap-jsapi-loader";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
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
import { api, personalPlaceKey, personalPlaceToPlace, type PersonalMapPlace, type PlaceInfo } from "../lib/api";
import { createMapExplorer } from "../lib/mapExplorer";
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
};
type MapControl = {
  on: (event: string, handler: (event: MapEvent) => void) => void;
  off: (event: string, handler: (event: MapEvent) => void) => void;
  setCenter: (point: number[]) => void;
  setZoom: (zoom: number) => void;
  getZoom: () => number;
  destroy: () => void;
  add: (items: unknown[]) => void;
  addControl: (control: unknown) => void;
  remove: (items: unknown[]) => void;
  resize: () => void;
  lngLatToContainer: (point: unknown) => { x: number; y: number };
  setFitView: (targets?: unknown[], immediately?: boolean, padding?: number[]) => void;
};
type MapEvent = { id?: string; name?: string; lnglat?: { lng: number; lat: number } };
type Discovery = { places: Place[]; note: string };
type PlaceStatus = "itinerary" | "pending" | "saved" | "preview" | "normal";
const jsKey = import.meta.env.VITE_AMAP_JS_KEY as string | undefined;

function MapCanvas({
  places,
  selected,
  onSelect,
  routeDays,
  routeFocus,
  statusById,
  onError,
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
  onDiscoverStart: () => void;
  onDiscoverResult: (result: Discovery) => void;
}) {
  const element = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  const mapRef = useRef<MapControl | null>(null);
  const amapRef = useRef<Awaited<ReturnType<typeof load>> | null>(null);
  const overlays = useRef<unknown[]>([]);
  const routeOverlays = useRef<unknown[]>([]);
  const [drawerItems, setDrawerItems] = useState<RouteSequenceStop[]>([]);
  const selectRef = useRef(onSelect);
  const startRef = useRef(onDiscoverStart);
  const resultRef = useRef(onDiscoverResult);
  const requestId = useRef(0);
  const clickTimer = useRef<number | null>(null);
  const suppressUntil = useRef(0);
  useEffect(() => {
    selectRef.current = onSelect;
    startRef.current = onDiscoverStart;
    resultRef.current = onDiscoverResult;
  }, [onSelect, onDiscoverStart, onDiscoverResult]);
  useEffect(() => {
    if (!jsKey || !element.current) return;
    let active = true;
    let detach: (() => void) | null = null;
    const win = window as typeof window & {
      _AMapSecurityConfig?: { securityJsCode?: string; serviceHost?: string };
    };
    if (import.meta.env.VITE_AMAP_SERVICE_HOST) {
      win._AMapSecurityConfig = {
        serviceHost: import.meta.env.VITE_AMAP_SERVICE_HOST,
      };
    } else if (import.meta.env.VITE_AMAP_SECURITY_CODE) {
      win._AMapSecurityConfig = {
        securityJsCode: import.meta.env.VITE_AMAP_SECURITY_CODE,
      };
    }
    load({ key: jsKey, version: "2.0", plugins: ["AMap.Scale", "AMap.PlaceSearch", "AMap.Geocoder"] })
      .then((AMap) => {
        if (!active || !element.current) return;
        amapRef.current = AMap;
        mapRef.current = new AMap.Map(element.current, {
          center: [114.3055, 30.5928],
          zoom: 11.6,
          mapStyle: "amap://styles/normal",
          showLabel: true,
          isHotspot: true,
        }) as MapControl;
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
          if (event.id && location) discover(() => explorer.hotspot(event.id!, event.name || "地图地点", location));
        };
        const onClick = (event: MapEvent) => {
          const location = point(event);
          if (!location || Date.now() < suppressUntil.current) return;
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
        const map = mapRef.current;
        detach = () => {
          map?.off("hotspotclick", onHotspot);
          map?.off("click", onClick);
          for (const event of ["dragstart", "dragend", "zoomstart", "zoomend"]) map?.off(event, onGesture);
        };
        setReady(true);
      })
      .catch(() => {
        if (active) onError("高德地图加载失败，请检查 JS Key、域名白名单与安全密钥");
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
  }, [onError]);
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
    if (selected && mapRef.current) {
      mapRef.current.setCenter([selected.location.lng, selected.location.lat]);
      mapRef.current.setZoom(Math.max(mapRef.current.getZoom(), 13));
    }
  }, [selected]);
  useEffect(() => {
    if (!mapRef.current || !element.current) return;
    const observer = new ResizeObserver(() => mapRef.current?.resize());
    observer.observe(element.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const zoom = (event: Event) => {
      const detail = (event as CustomEvent<number>).detail;
      if (mapRef.current)
        mapRef.current.setZoom(mapRef.current.getZoom() + detail);
    };
    document.addEventListener("map-zoom", zoom);
    return () => document.removeEventListener("map-zoom", zoom);
  }, []);
  if (!jsKey) {
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
  const [infoLoading, setInfoLoading] = useState(false);
  const infoRequestId = useRef(0);
  const infoController = useRef<AbortController | null>(null);
  const [candidates, setCandidates] = useState<Place[]>([]);
  const discoveryCancelled = useRef(false);
  const personalTrip = useMemo(() => ({ id: trip.id, secret: trip.secret }), [trip.id, trip.secret]);
  useEffect(() => {
    const id = ++infoRequestId.current;
    infoController.current?.abort();
    if (!selected) {
      queueMicrotask(() => { if (id === infoRequestId.current) { setPlaceInfo(null); setInfoLoading(false); } });
      return;
    }
    const controller = new AbortController();
    infoController.current = controller;
    queueMicrotask(() => { if (id === infoRequestId.current) { setPlaceInfo(null); setInfoLoading(true); } });
    api.placeInfo(selected, false, controller.signal).then(info => { if (id === infoRequestId.current) setPlaceInfo(info); })
      .catch(error => { if (id === infoRequestId.current && !controller.signal.aborted) setPlaceInfo({ status: "unavailable", checkedAt: new Date().toISOString(), stale: false, introduction: "", fields: {}, sources: [], error: error instanceof Error ? error.message : "联网资料请求失败" }); })
      .finally(() => { if (id === infoRequestId.current) setInfoLoading(false); });
    return () => { infoController.current?.abort(); infoRequestId.current += 1; };
  }, [selected]);
  const refreshInfo = () => {
    if (!selected || infoLoading) return;
    const id = ++infoRequestId.current;
    infoController.current?.abort();
    const controller = new AbortController();
    infoController.current = controller;
    setInfoLoading(true);
    api.placeInfo(selected, true, controller.signal).then(info => { if (id === infoRequestId.current) setPlaceInfo(info); })
      .catch(error => { if (id === infoRequestId.current && !controller.signal.aborted) setPlaceInfo(current => current ? { ...current, stale: true, error: error instanceof Error ? error.message : "联网资料请求失败" } : null); })
      .finally(() => { if (id === infoRequestId.current) setInfoLoading(false); });
  };
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
    route: routeDays.flatMap((day) => day.stops.map((stop) => stop.place)),
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
      places: [...new Map(all.map((place) => [place.placeId, place])).values()],
      statusById: status,
    };
  }, [markerInput]);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    setSearchOpen(true);
    try {
      const data = await api.search(query.trim());
      setResults(data.places);
      setSearchNote(data.source);
      if (data.places[0]) select(data.places[0]);
    } catch (error) {
      setSearchNote(error instanceof Error ? error.message : "搜索失败");
    } finally {
      setSearching(false);
    }
  };
  const isPending =
    selected &&
    trip.pendingPlaces.some((place) => place.placeId === selected.placeId);
  const personalSaved = selected && personalPlaces.find((item) => item.place_key === personalPlaceKey(selected));
  return (
    <section className="map-workspace" aria-label="地图与地点搜索">
      <MapCanvas
        places={places}
        selected={selected}
        onSelect={select}
        routeDays={routeDays}
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
                  : "没有找到地点，请试试其他关键词。"}
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
          {jsKey ? "© 高德地图 · 数据仅供行程参考" : "示意视图 · 坐标仅供参考"}
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
          <div className="fact-status place-info">
            <div className="place-info-heading">联网资料：<strong>{infoLoading && !placeInfo ? "正在查询…" : !placeInfo ? "尚未查询" : placeInfo.error?.includes("超时") ? "查询超时" : placeInfo.stale ? "部分资料可用（旧数据）" : ({ verified: "已核实部分公开信息", partial: "部分核实", not_found: "查询后未找到可靠信息", conflict: "来源冲突", unavailable: "请求失败" } as const)[placeInfo.status]}</strong><button type="button" onClick={refreshInfo} disabled={infoLoading}>刷新</button></div>
            {placeInfo && <><small>最后核查：{new Date(placeInfo.checkedAt).toLocaleString("zh-CN")}{placeInfo.stale ? " · 旧数据，搜索失败" : ""}；当日开放状态以景区公告为准。</small>
              {placeInfo.error && <p role="alert">{placeInfo.error}</p>}
              {placeInfo.conflict && <p>来源冲突：{placeInfo.conflict}</p>}
              <dl>{([ ["todayNotice", "当日公告"], ["openingHours", "常规开放时间"], ["adultPrice", "成人票价"], ["discounts", "优惠票"], ["reservation", "预约要求"], ["reservationUrl", "预约入口"], ["phone", "联系电话"], ["address", "官方地址"], ["visitorNotes", "游客须知"] ] as const).map(([key,label]) => <div key={key}><dt>{label}</dt><dd>{placeInfo.fields[key] ? <>{key === "reservationUrl" && /^https?:\/\//.test(placeInfo.fields[key].value) ? <a href={placeInfo.fields[key].value} target="_blank" rel="noopener noreferrer">打开入口</a> : placeInfo.fields[key].value} <a href={placeInfo.sources[placeInfo.fields[key].sourceId - 1]?.url} target="_blank" rel="noopener noreferrer">[来源{placeInfo.fields[key].sourceId}]</a></> : "未核实"}</dd></div>)}</dl>
              {placeInfo.introduction && <div className="place-info-intro"><h4>地点介绍与游览建议</h4><ReactMarkdown remarkPlugins={[remarkGfm]}>{placeInfo.introduction}</ReactMarkdown></div>}
              {placeInfo.sources.length > 0 && <div className="place-info-sources"><h4>信息来源</h4>{placeInfo.sources.map((source,index) => <p key={source.url}><a href={source.url} target="_blank" rel="noopener noreferrer">[{index+1}] {source.title}</a> {source.official ? "官方" : "第三方"} · {source.publishedAt ? `发布 ${source.publishedAt.slice(0,10)}` : "发布时间未标明"}</p>)}</div>}
            </>}
          </div>
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
