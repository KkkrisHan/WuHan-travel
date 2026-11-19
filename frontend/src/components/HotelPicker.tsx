import { useEffect, useRef, useState } from "react";
import { load } from "@amap/amap-jsapi-loader";
import { api } from "../lib/api";
import type { Place, Point } from "../types";

type Props = {
  value: string;
  location: Point | null;
  onChange: (name: string, location: Point | null) => void;
};

type LiveMap = {
  setCenter: (value: number[]) => void;
  setZoom: (value: number) => void;
  on: (event: string, callback: (event: { lnglat?: Point }) => void) => void;
  off: (event: string, callback: (event: { lnglat?: Point }) => void) => void;
  add: (marker: unknown) => void;
  destroy: () => void;
};
type LiveMarker = { setPosition: (position: number[]) => void };

const jsKey = import.meta.env.VITE_AMAP_JS_KEY as string | undefined;

export function HotelPicker({ value, location, onChange }: Props) {
  const [results, setResults] = useState<Place[]>([]);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState("");
  const [preview, setPreview] = useState<Point | null>(location);
  const [mapReady, setMapReady] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<LiveMap | null>(null);
  const markerRef = useRef<LiveMarker | null>(null);
  const changeRef = useRef(onChange);
  useEffect(() => { changeRef.current = onChange; }, [onChange]);

  useEffect(() => {
    if (!value.trim() || location) return;
    if (value.trim().length < 2) return;
    let active = true;
    const timer = window.setTimeout(async () => {
      setLoading(true);
      setNotice("");
      try {
        const response = await api.search(value.trim());
        if (!active) return;
        setResults(response.places.slice(0, 8));
        if (response.places[0]) setPreview(response.places[0].location);
        else setNotice("没有找到匹配地点，请尝试完整酒店名或街道地址。");
      } catch (error) {
        if (active) setNotice(error instanceof Error ? error.message : "地点搜索失败");
      } finally {
        if (active) setLoading(false);
      }
    }, 600);
    return () => { active = false; window.clearTimeout(timer); };
  }, [value, location]);

  useEffect(() => {
    if (!jsKey || !container.current) return;
    let active = true;
    const win = window as typeof window & { _AMapSecurityConfig?: { serviceHost?: string; securityJsCode?: string } };
    if (import.meta.env.VITE_AMAP_SERVICE_HOST) {
      win._AMapSecurityConfig = { serviceHost: import.meta.env.VITE_AMAP_SERVICE_HOST };
    } else if (import.meta.env.VITE_AMAP_SECURITY_CODE) {
      win._AMapSecurityConfig = { securityJsCode: import.meta.env.VITE_AMAP_SECURITY_CODE };
    }
    let callback: ((event: { lnglat?: Point }) => void) | null = null;
    load({ key: jsKey, version: "2.0", plugins: ["AMap.Scale"] }).then((AMap) => {
      if (!active || !container.current) return;
      const map = new AMap.Map(container.current, {
        center: [114.3055, 30.5928], zoom: 12, mapStyle: "amap://styles/normal",
      }) as LiveMap;
      const marker = new AMap.Marker({ position: [114.3055, 30.5928] }) as LiveMarker;
      map.add(marker);
      mapRef.current = map;
      markerRef.current = marker;
      setMapReady(true);
      callback = (event) => {
        if (!event.lnglat) return;
        const point = { lng: Number(event.lnglat.lng), lat: Number(event.lnglat.lat) };
        if (!Number.isFinite(point.lng) || !Number.isFinite(point.lat)) return;
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
      if (active) setNotice("地图暂不可用，仍可使用文字搜索选择酒店。");
    });
    return () => {
      active = false;
      if (callback) mapRef.current?.off("click", callback);
      mapRef.current?.destroy();
      mapRef.current = null;
      markerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const point = location || preview;
    if (!point || !mapRef.current || !markerRef.current) return;
    mapRef.current.setCenter([point.lng, point.lat]);
    mapRef.current.setZoom(15);
    markerRef.current.setPosition([point.lng, point.lat]);
  }, [location, preview, mapReady]);

  const choose = (place: Place) => {
    setPreview(place.location);
    onChange(place.name + (place.address && place.address !== "地址未知" ? " · " + place.address : ""), place.location);
    setNotice("已定位并选中住宿地点，生成行程将从这里计算往返路线。");
    setResults([]);
  };

  return (
    <div className="hotel-picker">
      <label htmlFor="hotel-search">
        酒店或每日出发地址 <span>选填</span>
      </label>
      <input
        id="hotel-search"
        value={value}
        onChange={(event) => { setResults([]); setLoading(false); onChange(event.target.value, null); }}
        placeholder="例如：武汉万达瑞华酒店、汉口火车站附近…"
        maxLength={120}
        autoComplete="off"
      />
      <p className="hotel-picker-hint">输入酒店名后，地图自动定位到搜索结果；点击候选项确认，也可以直接点击地图选择位置。</p>
      <div className="hotel-picker-map">
        {jsKey ? <div ref={container} className="hotel-picker-canvas" aria-label="住宿位置交互地图" /> : (
          <div className="hotel-map-fallback">高德地图 JS Key 未配置，可通过文字搜索选择地址。</div>
        )}
      </div>
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
      {notice && <p className="hotel-picker-hint" role="status">{notice}</p>}
      {value && (
        <button type="button" className="hotel-picker-clear" onClick={() => { onChange("", null); setPreview(null); setResults([]); setNotice(""); }}>
          清除住宿位置
        </button>
      )}
    </div>
  );
}
