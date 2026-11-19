import { staticMap } from "./api";
import type { DayPlan, Trip } from "../types";

const mapUrl = (day: DayPlan) =>
  `https://uri.amap.com/marker?position=${day.stops[0]?.place.location.lng ?? 114.3055},${day.stops[0]?.place.location.lat ?? 30.5928}&name=${encodeURIComponent(day.stops[0]?.place.name || "武汉")}`;
const htmlEscape = (value: string) =>
  value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
const blobDataUrl = (blob: Blob) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });

export async function exportTrip(
  trip: Trip,
  indexes: number[],
  format: "md" | "pdf",
) {
  const days = indexes.map((index) => trip.itinerary[index]).filter(Boolean);
  if (!days.length) throw new Error("请选择至少一天");
  const popup = format === "pdf" ? window.open("", "_blank") : null;
  if (format === "pdf" && !popup)
    throw new Error("浏览器阻止了打印窗口，请允许弹出窗口后重试");
  if (popup)
    popup.document.write(
      '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>准备导出</title><body style="font-family:sans-serif;padding:2rem">正在准备地图与打印页面…</body></html>',
    );
  let maps: string[];
  try {
    maps = await Promise.all(
      days.map(async (day) =>
        blobDataUrl(
          await staticMap(
            day.stops.length
              ? day.stops.map((stop) => stop.place.location)
              : [{ lng: 114.3055, lat: 30.5928 }],
            day.legs.flatMap((leg) => leg.polyline),
          ),
        ),
      ),
    );
  } catch (error) {
    popup?.close();
    throw error;
  }
  if (format === "md") {
    const lines = [
      `# ${trip.name}`,
      "",
      `出行：${trip.preferences.days} 天 · ${trip.preferences.people} 人 · ${trip.preferences.pace}节奏`,
      "",
      "地图与 POI 来源：高德地图。开放、预约、票价以官方信息为准。",
      "",
    ];
    days.forEach((day, index) => {
      lines.push(
        `## ${day.date}`,
        "",
        `![${day.date} 地图](${maps[index]})`,
        "",
        `[在高德地图打开首站](${mapUrl(day)})`,
        "",
        `游览：${day.sightseeingMinutes} 分钟；交通：${day.transportMinutes === null ? "待核实" : `${day.transportMinutes} 分钟`}`,
        "",
      );
      day.stops.forEach((stop, stopIndex) => {
        lines.push(
          `${stopIndex + 1}. **${stop.place.name}** ${stop.arrival}–${stop.departure} · ${stop.minutes} 分钟`,
          `   - ${stop.place.address}（${stop.place.source}）`,
          `   - 开放与预约：${stop.warning || "尚未核实"}`,
        );
        if (day.legs[stopIndex])
          lines.push(
            `   - 前往下一站：${day.legs[stopIndex].mode} ${day.legs[stopIndex].minutes === null ? "耗时未知" : `${day.legs[stopIndex].minutes} 分钟`}（${day.legs[stopIndex].source}）`,
          );
      });
      lines.push(
        "",
        `用餐与休息：${day.meals.join("；") || "自行安排"}`,
        `预算：${day.budgetNote}`,
        ...day.warnings.map((warning) => `> 提醒：${warning}`),
        "",
      );
    });
    const url = URL.createObjectURL(
      new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${trip.name.replace(/[\\/:*?"<>|]/g, "_")}.md`;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    return;
  }
  const sections = days
    .map(
      (day, index) =>
        `<section class="page"><h2>${htmlEscape(day.date)}</h2><img class="map" src="${maps[index]}" alt="${htmlEscape(day.date)}地图" /><p class="source">© 高德地图 · <a href="${mapUrl(day)}">在高德地图查看首站</a> · ${day.legs.some((leg) => leg.polyline.length > 1) ? "路线来自高德地图路线规划" : "仅展示地点标记，路线尚未核实"}</p><p>游览 ${day.sightseeingMinutes} 分钟 · 交通 ${day.transportMinutes === null ? "待核实" : `${day.transportMinutes} 分钟`}</p><ol>${day.stops.map((stop) => `<li><strong>${htmlEscape(stop.place.name)}</strong>　${htmlEscape(stop.arrival)}–${htmlEscape(stop.departure)}<br /><small>${htmlEscape(stop.place.address)} · ${htmlEscape(stop.warning || "开放与预约待核实")}</small></li>`).join("")}</ol><p>用餐与休息：${htmlEscape(day.meals.join("；") || "自行安排")}</p><p>预算：${htmlEscape(day.budgetNote)}</p>${day.warnings.map((warning) => `<p class="warning">提醒：${htmlEscape(warning)}</p>`).join("")}</section>`,
    )
    .join("");
  if (!popup) return;
  popup.document.write(
    `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>${htmlEscape(trip.name)}</title><style>body{font-family:"Microsoft YaHei",Arial,sans-serif;color:#1a2429;margin:0}h1{padding:24px 32px;margin:0;background:#10232b;color:white;font-size:26px}h2{font-size:22px}p{line-height:1.6}.page{padding:24px 32px;break-after:page}.page:last-child{break-after:auto}.map{width:100%;max-height:340px;object-fit:contain}.source,small{color:#627077;font-size:12px}li{padding:10px 0}.warning{color:#a15b13}@page{size:A4;margin:12mm}</style></head><body><h1>${htmlEscape(trip.name)}</h1>${sections}<script>window.onload=()=>setTimeout(()=>window.print(),500)</script></body></html>`,
  );
  popup.document.close();
}
