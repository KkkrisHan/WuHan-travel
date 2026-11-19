import type { DayPlan, GeneratedItinerary, Place, Stop } from "../types";

// Recover map-ready stops from documents created before the unified itinerary format.
// The saved Markdown remains the authoritative itinerary text.
export function recoverItinerary(document: GeneratedItinerary): DayPlan[] {
  if (document.dayPlans?.length) return structuredClone(document.dayPlans);
  const places = new Map<string, Place>(
    [...document.mustVisitPlaces, ...document.recommendedPlaces].map((place) => [place.name.trim(), place]),
  );
  const sections = document.markdown.split(/(?=^## Day\s+\d+\s*·)/m).slice(1);
  const days: DayPlan[] = [];
  for (const [index, section] of sections.entries()) {
    const date = section.split("\n", 1)[0].match(/^## Day\s+\d+\s*·\s*(.+)$/)?.[1] || `第${index + 1}天（日期未定）`;
    const stops: Stop[] = [];
    for (const line of section.split("\n")) {
      const match = line.match(/^\|\s*(?:上午|下午|晚间)\s*\|\s*(?:【必去】|【AI推荐】)(.+?)（[^）]+）\s*\|\s*(\d{2}:\d{2})[–-](\d{2}:\d{2})/);
      if (!match) continue;
      const place = places.get(match[1].trim());
      if (!place) continue;
      const minute = (time: string) => Number(time.slice(0, 2)) * 60 + Number(time.slice(3));
      stops.push({
        id: crypto.randomUUID(), place,
        arrival: match[2], departure: match[3],
        minutes: Math.max(1, minute(match[3]) - minute(match[2])),
      });
    }
    if (stops.length) {
      days.push({
        id: crypto.randomUUID(), date, stops, legs: [], transportMinutes: null,
        sightseeingMinutes: stops.reduce((sum, stop) => sum + stop.minutes, 0),
        meals: ["午餐与休息"], budgetNote: "以最新官方报价和实际交通费用为准",
        warnings: ["从旧版已生成行程恢复；地图交通路径与耗时请重新核对。"],
      });
    }
  }
  return days;
}
