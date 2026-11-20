import * as AMapLoader from "@amap/amap-jsapi-loader";

const resetLoader = (AMapLoader as unknown as { default?: { reset?: () => void } }).default?.reset;
let activeKey = "";

/** Keep the loader's singleton aligned with the latest user-managed JS API key. */
export function loadAMap(key: string, plugins: string[], forceReset = false) {
  if (forceReset || (activeKey && activeKey !== key)) resetLoader?.();
  activeKey = key;
  return AMapLoader.load({ key, version: "2.0", plugins });
}
