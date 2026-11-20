import { useEffect, useRef, useState } from "react";
import { loadAMap } from "../lib/amapLoader";
import { api, apiBase } from "../lib/api";
import { getWuhanBoundary, getWuhanBounds, isInsideWuhan } from "../lib/wuhanBoundary";
import type { Place, Point } from "../types";

type Props = {
  amapJsKey: string;
  amapConfigRevision: string;
  webServiceConfigured: boolean;
  value: string;
  location: Point | null;
  onChange: (name: string, location: Point | null) => void;
  onRequireAmapWebService: (action: () => void) => void;
};

type LiveMap = {
  setCenter: (value: number[]) => void;
  setZoom: (value: number, immediately?: boolean) => void;
  getZoom: () => number;
  on: (event: string, callback: (event: { lnglat?: Point }) => void) => void;
  off: (event: string, callback: (event: { lnglat?: Point }) => void) => void;
  add: (marker: unknown) => void;
  destroy: () => void;
  setMask: (mask: number[][][][]) => void;
  setLimitBounds: (bounds: unknown) => void;
  setZooms: (zooms: [number, number]) => void;
  lngLatToContainer: (point: unknown) => { x: number; y: number };
  resize: () => void;
};
type LiveMarker = { setPosition: (position: number[]) => void };

export function HotelPicker({ amapJsKey, amapConfigRevision, webServiceConfigured, value, location, onChange, onRequireAmapWebService }: Props) {
  const [results, setResults] = useState<Place[]>([]);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState("");
  const [preview, setPreview] = useState<Point | null>(location);
  const [mapReady, setMapReady] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<LiveMap | null>(null);
  const markerRef = useRef<LiveMarker | null>(null);
  const outsideMaskPath = useRef<SVGPathElement | null>(null);
  const previousRevision = useRef("");
  const changeRef = useRef(onChange);
  const requireWebServiceRef = useRef(onRequireAmapWebService);
  const latestQuery = useRef(value.trim());
  const pendingSearchQuery = useRef("");
  const activeSearchQuery = useRef("");
  useEffect(() => { changeRef.current = onChange; }, [onChange]);
  useEffect(() => { requireWebServiceRef.current = onRequireAmapWebService; }, [onRequireAmapWebService]);

  useEffect(() => {
    const query = value.trim();
    if (!query || location || query.length < 2) {
      pendingSearchQuery.current = "";
      activeSearchQuery.current = "";
      return;
    }
    const search = async () => {
      if (latestQuery.current !== query) return;
      if (activeSearchQuery.current === query) return;
      activeSearchQuery.current = query;
      setLoading(true);
      setNotice("");
      try {
        const response = await api.search(query);
        if (latestQuery.current !== query) return;
        const places = response.places.filter((place) => isInsideWuhan(place.location));
        setResults(places.slice(0, 8));
        if (places[0]) setPreview(places[0].location);
        else setNotice("未找到武汉市范围内的相关住宿地点。");
      } catch (error) {
        if (latestQuery.current === query) {
          activeSearchQuery.current = "";
          setNotice(error instanceof Error ? error.message : "地点搜索失败");
        }
      } finally {
        if (latestQuery.current === query) setLoading(false);
      }
    };
    let timer: number | null = null;
    if (!webServiceConfigured) {
      if (pendingSearchQuery.current !== query) {
        pendingSearchQuery.current = query;
        requireWebServiceRef.current(() => {
          if (latestQuery.current !== query) return;
          pendingSearchQuery.current = "";
          void search();
        });
      }
    } else {
      pendingSearchQuery.current = "";
      timer = window.setTimeout(() => void search(), 600);
    }
    return () => { if (timer !== null) window.clearTimeout(timer); };
  }, [value, location, webServiceConfigured]);

  useEffect(() => {
    if (!amapJsKey || !container.current) return;
    let active = true;
    const win = window as typeof window & { _AMapSecurityConfig?: { serviceHost?: string } };
    win._AMapSecurityConfig = { serviceHost: apiBase + "/_AMapService" };
    let callback: ((event: { lnglat?: Point }) => void) | null = null;
    let updateOutsideMask: (() => void) | null = null;
    let resizeObserver: ResizeObserver | null = null;
    let outsideMaskFrame: number | null = null;
    const forceLoaderReset = !!previousRevision.current && previousRevision.current !== amapConfigRevision;
    previousRevision.current = amapConfigRevision;
    loadAMap(amapJsKey, ["AMap.Scale"], forceLoaderReset).then(async (AMap) => {
      if (!active || !container.current) return;
      const boundary = getWuhanBoundary();
      if (!active || !container.current) return;
      const bounds = getWuhanBounds();
      if (!bounds) throw new Error("武汉市行政边界数据无效，住宿位置选择已暂停。");
      const paths = boundary.polygons.map((polygon) => [polygon[0]]);
      const map = new AMap.Map(container.current, {
        center: [114.3055, 30.5928], zoom: 9, zooms: [5, 18],
        viewMode: "3D", pitch: 0, rotation: 0, rotateEnable: false, pitchEnable: false,
        mask: paths, mapStyle: "amap://styles/normal", backgroundColor: "#0b1213",
      }) as LiveMap;
      map.setMask(paths);
      map.setLimitBounds(new AMap.Bounds(new AMap.LngLat(bounds.west, bounds.south), new AMap.LngLat(bounds.east, bounds.north)));
      const mercatorY = (lat: number) => Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
      const worldWidth = (bounds.east - bounds.west) / 360 * 256;
      const worldHeight = (mercatorY(bounds.north) - mercatorY(bounds.south)) / (2 * Math.PI) * 256;
      let lastFitZoom: number | null = null;
      const updateViewLimits = () => {
        const host = container.current;
        if (!host || host.clientWidth < 100 || host.clientHeight < 100) return null;
        const fitZoom = Math.log2(Math.min((host.clientWidth - 48) / worldWidth, (host.clientHeight - 48) / worldHeight));
        const minZoom = Math.max(4, Math.min(14, Math.round((fitZoom - 0.25) * 10) / 10));
        const atPreviousFit = lastFitZoom !== null && Math.abs(map.getZoom() - lastFitZoom) < 0.08;
        map.setZooms([minZoom, 18]);
        if (map.getZoom() < minZoom || atPreviousFit) map.setZoom(Math.max(minZoom, Math.min(18, fitZoom)), true);
        lastFitZoom = fitZoom;
        return fitZoom;
      };
      const fitZoom = updateViewLimits();
      map.setCenter([(bounds.west + bounds.east) / 2, (bounds.south + bounds.north) / 2]);
      const drawOutsideMask = () => {
        if (!container.current || !outsideMaskPath.current) return;
        const width = container.current.clientWidth;
        const height = container.current.clientHeight;
        const rings = boundary.rings.map((ring) => ring.map(([lng, lat]) => {
          const pixel = map.lngLatToContainer([lng, lat]);
          return `${pixel.x.toFixed(2)} ${pixel.y.toFixed(2)}`;
        }).join(" L"));
        outsideMaskPath.current.setAttribute("d", `M0 0 H${width} V${height} H0 Z ${rings.map((points) => `M${points} Z`).join(" ")}`);
      };
      updateOutsideMask = () => {
        if (outsideMaskFrame !== null) return;
        outsideMaskFrame = window.requestAnimationFrame(() => {
          outsideMaskFrame = null;
          drawOutsideMask();
        });
      };
      updateOutsideMask();
      for (const event of ["mapmove", "zoomchange", "resize"]) map.on(event, updateOutsideMask);
      if (fitZoom !== null) map.setZoom(fitZoom, true);
      resizeObserver = new ResizeObserver(() => {
        map.resize();
        updateViewLimits();
        updateOutsideMask?.();
      });
      resizeObserver.observe(container.current);
      // AMap's built-in marker references an obsolete HTTP icon that can return 503.
      // Use our own HTML/CSS pin so the hotel picker never requests that image.
      const pin = document.createElement("div");
      pin.className = "hotel-map-pin";
      pin.textContent = "⌖";
      const marker = new AMap.Marker({
        position: [114.3055, 30.5928],
        content: pin,
        offset: new AMap.Pixel(-15, -30),
      }) as LiveMarker;
      map.add(marker);
      mapRef.current = map;
      markerRef.current = marker;
      setMapReady(true);
      callback = (event) => {
        if (!event.lnglat) return;
        const point = { lng: Number(event.lnglat.lng), lat: Number(event.lnglat.lat) };
        if (!Number.isFinite(point.lng) || !Number.isFinite(point.lat)) return;
        if (!isInsideWuhan(point)) {
          setNotice("住宿位置必须位于武汉市行政区域内。");
          return;
        }
        setPreview(point);
        setNotice("正在查询地图上选中的地址…");
        api.reverseGeocode(point).then((result) => {
          if (!active) return;
          changeRef.current(result.address, point);
          setNotice("已使用地图上选中的位置作为住宿出发点。");
        }).catch(() => {
          if (!active) return;
          changeRef.current(`地图选定位置（${point.lng.toFixed(6)}, ${point.lat.toFixed(6)}）`, point);
          setNotice("已记录选定坐标，地址名称未能核实。");
        });
      };
      map.on("click", callback);
    }).catch(() => {
      if (active) setNotice("武汉行政边界或地图暂不可用，住宿地点选择已暂停，请刷新页面重试。");
    });
    return () => {
      active = false;
      if (callback) mapRef.current?.off("click", callback);
      for (const event of ["mapmove", "zoomchange", "resize"]) if (updateOutsideMask) mapRef.current?.off(event, updateOutsideMask);
      if (outsideMaskFrame !== null) window.cancelAnimationFrame(outsideMaskFrame);
      resizeObserver?.disconnect();
      mapRef.current?.destroy();
      mapRef.current = null;
      markerRef.current = null;
    };
  }, [amapJsKey, amapConfigRevision]);

  useEffect(() => {
    const point = location || preview;
    if (!point || !mapRef.current || !markerRef.current) return;
    mapRef.current.setCenter([point.lng, point.lat]);
    mapRef.current.setZoom(15);
    markerRef.current.setPosition([point.lng, point.lat]);
  }, [location, preview, mapReady]);

  const choose = (place: Place) => {
    if (!isInsideWuhan(place.location)) {
      setNotice("住宿位置必须位于武汉市行政区域内。");
      return;
    }
    setPreview(place.location);
    onChange(place.name + (place.address && place.address !== "地址未知" ? " · " + place.address : ""), place.location);
    setNotice("已定位并选中住宿地点，生成行程将从这里计算往返路线。");
    setResults([]);
  };
  const webServiceNotice = !webServiceConfigured && value.trim().length >= 2 && !location
    ? "搜索酒店需要连接高德 Web Service。正在打开服务配置…"
    : "";

  return (
    <div className="hotel-picker">
      <label htmlFor="hotel-search">
        酒店或每日出发地址 <span>选填</span>
      </label>
      <input
        id="hotel-search"
        value={value}
        onChange={(event) => { const next = event.target.value; latestQuery.current = next.trim(); setResults([]); setLoading(false); setNotice(""); onChange(next, null); }}
        placeholder="例如：武汉万达瑞华酒店、汉口火车站附近…"
        maxLength={120}
        autoComplete="off"
      />
      <p className="hotel-picker-hint">输入酒店名后，地图自动定位到搜索结果；点击候选项确认，也可以直接点击地图选择位置。</p>
      <div className="hotel-picker-map">
        {amapJsKey ? <div ref={container} className="hotel-picker-canvas" aria-label="住宿位置交互地图" /> : (
          <div className="hotel-map-fallback">高德地图 JS Key 未配置，可通过文字搜索选择地址。</div>
        )}
        <svg className="hotel-wuhan-outside-mask" aria-hidden="true" preserveAspectRatio="none">
          <path ref={outsideMaskPath} fill="#192b32" fillRule="evenodd" />
        </svg>
      </div>
      {amapJsKey && <small className="hotel-picker-hint">武汉行政边界 © OpenStreetMap contributors · ODbL 1.0</small>}
      {!location && loading && <p className="hotel-picker-hint">正在查找酒店或地址…</p>}
      {!location && results.length > 0 && (
        <div className="hotel-picker-results" aria-label="酒店搜索结果">
          {results.map((place) => (
            <button key={place.placeId} type="button" onClick={() => choose(place)}>
              <strong>{place.name}</strong>
              <span>{place.address}</span>
            </button>
          ))}
        </div>
      )}
      {location && <p className="hotel-picker-selected">已选择：{value}（{location.lng.toFixed(5)}, {location.lat.toFixed(5)}）</p>}
      {(notice || webServiceNotice) && <p className="hotel-picker-hint" role="status">{notice || webServiceNotice}</p>}
      {value && (
        <button type="button" className="hotel-picker-clear" onClick={() => { latestQuery.current = ""; onChange("", null); setPreview(null); setResults([]); setNotice(""); }}>
          清除住宿位置
        </button>
      )}
    </div>
  );
}
