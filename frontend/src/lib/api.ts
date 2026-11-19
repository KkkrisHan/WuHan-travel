import type { Place, Point, TaskCreated, TaskEvent, Trip } from "../types";

export const apiBase = import.meta.env.VITE_API_URL || "http://localhost:8000";

export type PersonalMapPlace = {
  id: number;
  trip_id: string;
  place_key: string;
  poi_id: string | null;
  name: string;
  address: string | null;
  type: string | null;
  longitude: number;
  latitude: number;
  source: string;
};

export type PlaceInfo = {
  status: "verified" | "partial" | "not_found" | "conflict" | "unavailable";
  checkedAt: string;
  stale: boolean;
  cached?: boolean;
  error?: string;
  conflict?: string;
  introduction: string;
  fields: Record<string, { value: string; evidence: string; sourceId: number; corroboratingSourceIds?: number[]; checkedAt?: string; publishedAt?: string | null }>;
  sources: {
    title: string;
    url: string;
    publishedAt: string | null;
    checkedAt: string;
    official: boolean;
    sourceType?: string;
    authority?: number;
    fetchStatus?: "fetched" | "search_content" | "failed" | "not_attempted";
    fetchError?: string | null;
  }[];
};

const tripHeaders = (trip: Pick<Trip, "id" | "secret">) => ({ "X-Trip-ID": trip.id, "X-Trip-Secret": trip.secret });

export const personalPlaceKey = (place: Place) => {
  const poiId = place.poiId || (place.placeId.startsWith("coord:") || place.placeId.startsWith("featured-") ? null : place.placeId);
  return poiId ? `poi:${poiId}` : `coord:${place.location.lng.toFixed(6)},${place.location.lat.toFixed(6)}`;
};

export const personalPlaceToPlace = (place: PersonalMapPlace): Place => ({
  placeId: place.poi_id || `coord:${place.longitude.toFixed(6)},${place.latitude.toFixed(6)}`,
  poiId: place.poi_id,
  name: place.name,
  address: place.address || "地址未知",
  category: place.type || "地点",
  location: { lng: place.longitude, lat: place.latitude },
  source: place.source,
  description: "",
});

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeoutMs = path === "/places/info" ? 75000 : path === "/itineraries/generate" ? 180000 : 20000;
  let timedOut = false;
  const timer = window.setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  const onAbort = () => controller.abort();
  options?.signal?.addEventListener("abort", onAbort, { once: true });
  if (options?.signal?.aborted) controller.abort();
  try {
    const response = await fetch(`${apiBase}/api${path}`, {
      ...options,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...options?.headers },
    });
    if (!response.ok) {
      const error = await response.json().catch(() => null);
      throw new Error(typeof error?.detail === "string" ? error.detail : `请求失败 (${response.status})`);
    }
    return await response.json() as T;
  } catch (error) {
    if (timedOut) throw new Error(`请求超时（${Math.round(timeoutMs / 1000)} 秒），请重试`);
    if (options?.signal?.aborted) throw new Error("请求已取消");
    if (error instanceof TypeError) throw new Error("无法连接后端，请确认 FastAPI 已启动");
    throw error;
  } finally {
    window.clearTimeout(timer);
    options?.signal?.removeEventListener("abort", onAbort);
  }
}

export const api = {
  health: () =>
    request<{ ok: boolean; amapConfigured: boolean; modelConfigured: boolean }>(
      "/health",
    ),
  featured: () => request<{ places: Place[] }>("/places/featured"),
  personalPlaces: (trip: Pick<Trip, "id" | "secret">) =>
    request<{ places: PersonalMapPlace[] }>("/places", { headers: tripHeaders(trip) }),
  addPersonalPlace: (trip: Pick<Trip, "id" | "secret">, place: Place) =>
    request<{ place: PersonalMapPlace }>("/places", {
      method: "POST",
      headers: tripHeaders(trip),
      body: JSON.stringify({
        poi_id: place.poiId || (place.placeId.startsWith("coord:") || place.placeId.startsWith("featured-") ? null : place.placeId),
        name: place.name,
        address: place.address,
        type: place.category,
        longitude: place.location.lng,
        latitude: place.location.lat,
        source: ["hotspot", "nearby_search", "coordinate"].includes(place.source)
          ? place.source
          : place.placeId.startsWith("featured-") ? "featured" : "search",
      }),
    }),
  removePersonalPlace: (trip: Pick<Trip, "id" | "secret">, id: number) =>
    request<{ ok: boolean }>(`/places/${id}`, { method: "DELETE", headers: tripHeaders(trip) }),
  search: (query: string) =>
    request<{ places: Place[]; source: string }>("/places/search", {
      method: "POST",
      body: JSON.stringify({ query }),
    }),
  placeDetail: (placeId: string) => request<{ place: Place }>(`/places/${encodeURIComponent(placeId)}/detail`),
  placeInfo: (place: Place, refresh = false, signal?: AbortSignal) => request<PlaceInfo>("/places/info", { method: "POST", body: JSON.stringify({ place, refresh }), signal }),
  generateItinerary: (input: { mustVisitPlaces: Place[]; people: number; days: number; requirements: string; startDate: string | null; transport: string; lodging: string }) =>
    request<{ title: string; markdown: string; recommendedPlaces: Place[]; routeSource: string }>("/itineraries/generate", {
      method: "POST", body: JSON.stringify(input),
    }),
  intro: (trip: Trip, place: Place) =>
    request<TaskCreated>("/places/intro", {
      method: "POST",
      body: JSON.stringify({
        tripId: trip.id,
        tripSecret: trip.secret,
        revision: trip.revision,
        place,
      }),
    }),
  chat: (trip: Trip, selectedPlace: Place | null, message: string) =>
    request<TaskCreated>("/chat", {
      method: "POST",
      body: JSON.stringify({
        snapshot: snapshot(trip, selectedPlace, message),
        message,
      }),
    }),
  plan: (trip: Trip, selectedPlace: Place | null) =>
    request<TaskCreated>("/plans/generate", {
      method: "POST",
      body: JSON.stringify({ snapshot: planningSnapshot(trip, selectedPlace) }),
    }),
  optimize: (trip: Trip, selectedPlace: Place | null) =>
    request<TaskCreated>("/plans/optimize", {
      method: "POST",
      body: JSON.stringify({ snapshot: planningSnapshot(trip, selectedPlace) }),
    }),
  task: (task: TaskCreated) =>
    request<{ status: string; result: unknown; error?: string }>(
      `/tasks/${task.taskId}`,
      { headers: { "X-Task-Token": task.accessToken } },
    ),
  heartbeat: (task: TaskCreated) =>
    request<{ status: string }>(`/tasks/${task.taskId}/heartbeat`, {
      method: "POST",
      headers: { "X-Task-Token": task.accessToken },
    }),
  cancel: (task: TaskCreated) =>
    request<{ status: string }>(`/tasks/${task.taskId}/cancel`, {
      method: "POST",
      headers: { "X-Task-Token": task.accessToken },
    }),
  cleanup: (trip: { id: string; secret: string }) =>
    request<{ ok: boolean }>(`/trips/${trip.id}/tasks`, {
      method: "DELETE",
      headers: { "X-Trip-Secret": trip.secret },
    }),
};

function snapshot(
  trip: Trip,
  selectedPlace: Place | null,
  userRequest: string,
) {
  return {
    tripId: trip.id,
    tripSecret: trip.secret,
    revision: trip.revision,
    preferences: trip.preferences,
    pendingPlaces: trip.pendingPlaces,
    savedPlaces: trip.savedPlaces,
    itinerary: trip.itinerary,
    generatedItineraries: (trip.generatedItineraries || []).slice(-5).map((document) => ({
      title: document.title,
      days: document.days,
      people: document.people,
      createdAt: document.createdAt,
      mustVisitPlaces: document.mustVisitPlaces.map((place) => place.name),
      recommendedPlaces: document.recommendedPlaces.map((place) => place.name),
      markdown: document.markdown.slice(0, 18000),
    })),
    selectedPlace,
    userRequest,
    recentMessages: trip.messages
      .slice(-10)
      .map(
        (message) =>
          `${message.role === "user" ? "用户" : "助手"}：${message.text.slice(0, 500)}`,
      ),
  };
}

function planningSnapshot(trip: Trip, selectedPlace: Place | null) {
  const request = trip.messages
    .filter((message) => message.role === "user")
    .slice(-4)
    .map((message) => message.text)
    .join("\n");
  return snapshot(trip, selectedPlace, request);
}

export function subscribe(
  task: TaskCreated,
  onEvent: (event: TaskEvent) => void,
  onDisconnect: (error: Error) => void,
) {
  let closed = false;
  let last = 0;
  let disconnects = 0;
  const controller = new AbortController();
  const deadline = window.setTimeout(() => {
    if (!closed) {
      close();
      onDisconnect(new Error("AI 生成超时，请重试"));
    }
  }, 130000);
  const heartbeat = window.setInterval(() => {
    if (!closed) api.heartbeat(task).catch(() => undefined);
  }, 8000);
  const close = () => {
    closed = true;
    controller.abort();
    window.clearInterval(heartbeat);
    window.clearTimeout(deadline);
  };
  const accept = (parsed: TaskEvent) => {
    if (closed || parsed.eventId <= last) return;
    last = parsed.eventId;
    disconnects = 0;
    onEvent(parsed);
    if (
      ["task_completed", "task_failed", "task_cancelled"].includes(parsed.type)
    )
      close();
  };
  const connect = async () => {
    while (!closed) {
      try {
        const response = await fetch(
          `${apiBase}/api/tasks/${task.taskId}/events?after=${last}`,
          {
            headers: { "X-Task-Token": task.accessToken },
            signal: controller.signal,
          },
        );
        if (!response.ok || !response.body)
          throw new Error(`任务事件连接失败 (${response.status})`);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (!closed) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder
            .decode(value, { stream: true })
            .replaceAll("\r\n", "\n");
          let separator = buffer.indexOf("\n\n");
          while (separator >= 0) {
            const frame = buffer.slice(0, separator);
            buffer = buffer.slice(separator + 2);
            const line = frame
              .split("\n")
              .find((item) => item.startsWith("data: "));
            if (line) accept(JSON.parse(line.slice(6)) as TaskEvent);
            separator = buffer.indexOf("\n\n");
          }
        }
        if (closed) break;
        const status = await api.task(task);
        if (["completed", "failed", "cancelled"].includes(status.status)) {
          accept({
            taskId: task.taskId,
            eventId: last + 1,
            timestamp: new Date().toISOString(),
            type: `task_${status.status}`,
            payload:
              status.status === "completed"
                ? { result: status.result }
                : { message: status.error || "任务已取消" },
          });
          break;
        }
      } catch (error) {
        if (closed) break;
        disconnects += 1;
        if (disconnects >= 3) {
          close();
          onDisconnect(
            error instanceof Error
              ? error
              : new Error("任务连接已中断，请手动重试"),
          );
          break;
        }
        await new Promise((resolve) =>
          window.setTimeout(resolve, disconnects * 600),
        );
      }
    }
  };
  void connect();
  return close;
}

export async function staticMap(
  points: Point[],
  path: Point[] = [],
): Promise<Blob> {
  const query = points.map((point) => `${point.lng},${point.lat}`).join("|");
  const sampled = path.filter(
    (_, index) => index % Math.max(1, Math.ceil(path.length / 80)) === 0,
  );
  const route = sampled.map((point) => `${point.lng},${point.lat}`).join("|");
  const response = await fetch(
    `${apiBase}/api/maps/static?points=${encodeURIComponent(query)}&path=${encodeURIComponent(route)}`,
  );
  if (!response.ok)
    throw new Error("路线地图暂不可用，请配置高德 Web 服务密钥");
  return response.blob();
}
