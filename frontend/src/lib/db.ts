import Dexie, { type EntityTable } from "dexie";
import type {
  Message,
  Preferences,
  Trip,
  TripData,
  TripVersion,
} from "../types";

class TravelDb extends Dexie {
  trips!: EntityTable<Trip, "id">;
  cleanup!: EntityTable<{ id: string; secret: string }, "id">;
  constructor() {
    super("wuhan-ai-concierge");
    this.version(1).stores({ trips: "id,updatedAt,name", cleanup: "id" });
  }
}

export const db = new TravelDb();
export const defaultPreferences: Preferences = {
  startDate: null,
  days: 1,
  people: 1,
  company: "",
  budget: "灵活",
  pace: "适中",
  transport: "步行与公共交通",
  interests: [],
  requirements: "",
  lodging: "",
  lodgingLocation: null,
};
const clone = <T>(value: T): T => structuredClone(value);
const tripData = (trip: Trip): TripData => ({
  name: trip.name,
  nameSource: trip.nameSource,
  preferences: clone(trip.preferences),
  savedPlaces: clone(trip.savedPlaces),
  pendingPlaces: clone(trip.pendingPlaces),
  itinerary: clone(trip.itinerary),
  generatedItineraries: clone(trip.generatedItineraries || []),
});

export async function newTrip(
  explore: boolean,
  preferences: Preferences = defaultPreferences,
): Promise<Trip> {
  const now = new Date().toISOString();
  const trip: Trip = {
    id: crypto.randomUUID(),
    secret: crypto.randomUUID() + crypto.randomUUID(),
    revision: 0,
    createdAt: now,
    updatedAt: now,
    name: explore ? "武汉探索" : "武汉之旅",
    nameSource: "default",
    preferences: clone(preferences),
    savedPlaces: [],
    pendingPlaces: [],
    itinerary: [],
    generatedItineraries: [],
    messages: [],
    history: [],
    redo: [],
  };
  if (!explore && preferences.startDate) {
    trip.name = `${preferences.startDate.slice(5).replace("-", "月")}日 · 武汉之旅`;
    trip.nameSource = "suggested";
  }
  await db.trips.add(trip);
  return trip;
}

export async function mutateTrip(
  id: string,
  summary: string,
  update: (trip: Trip) => void,
  expectedRevision?: number,
  source: "user" | "agent" = "user",
) {
  return db.transaction("rw", db.trips, async () => {
    const current = await db.trips.get(id);
    if (!current) throw new Error("旅行计划不存在");
    if (expectedRevision !== undefined && current.revision !== expectedRevision)
      throw new Error("旅行计划已更新，请重新生成方案");
    const next = clone(current);
    const version: TripVersion = {
      id: crypto.randomUUID(),
      at: new Date().toISOString(),
      summary,
      source,
      data: tripData(current),
    };
    update(next);
    next.history = [...current.history, version].slice(-30);
    next.redo = [];
    next.revision = current.revision + 1;
    next.updatedAt = new Date().toISOString();
    await db.trips.put(next);
    return next;
  });
}

export async function updateMessages(
  id: string,
  update: (messages: Message[]) => Message[],
) {
  return db.transaction("rw", db.trips, async () => {
    const current = await db.trips.get(id);
    if (!current) throw new Error("旅行计划不存在");
    current.messages = update(clone(current.messages));
    await db.trips.put(current);
    return current;
  });
}

export async function undoTrip(id: string, redo = false) {
  return db.transaction("rw", db.trips, async () => {
    const current = await db.trips.get(id);
    if (!current) throw new Error("旅行计划不存在");
    const stack = redo ? current.redo : current.history;
    const version = stack.at(-1);
    if (!version) return current;
    const inverse: TripVersion = {
      id: crypto.randomUUID(),
      at: new Date().toISOString(),
      summary: redo ? "重做前" : "撤销前",
      source: "user",
      data: tripData(current),
    };
    const next = {
      ...current,
      ...clone(version.data),
      revision: current.revision + 1,
      updatedAt: new Date().toISOString(),
      history: redo
        ? [...current.history, inverse].slice(-30)
        : current.history.slice(0, -1),
      redo: redo
        ? current.redo.slice(0, -1)
        : [...current.redo, inverse].slice(-30),
    };
    await db.trips.put(next);
    return next;
  });
}

export async function restoreVersion(id: string, versionId: string) {
  return mutateTrip(id, "恢复历史版本", (trip) => {
    const version = trip.history.find((item) => item.id === versionId);
    if (!version) throw new Error("历史版本不存在");
    Object.assign(trip, clone(version.data));
  });
}

export async function duplicateTrip(trip: Trip) {
  const copy = clone(trip);
  const now = new Date().toISOString();
  copy.id = crypto.randomUUID();
  copy.secret = crypto.randomUUID() + crypto.randomUUID();
  copy.name = `${trip.name} · 副本`;
  copy.nameSource = "manual";
  copy.revision = 0;
  copy.createdAt = now;
  copy.updatedAt = now;
  copy.history = [];
  copy.redo = [];
  copy.messages = copy.messages.map((message) => ({
    ...message,
    id: crypto.randomUUID(),
  }));
  copy.itinerary = copy.itinerary.map((day) => ({
    ...day,
    id: crypto.randomUUID(),
    stops: day.stops.map((stop) => ({ ...stop, id: crypto.randomUUID() })),
    legs: [],
    transportMinutes: null,
    warnings: [...day.warnings, "复制后请重新核对交通路线与时间。"],
  }));
  await db.trips.add(copy);
  return copy;
}

export async function deleteTrip(trip: Trip) {
  await db.transaction("rw", db.trips, db.cleanup, async () => {
    await db.cleanup.put({ id: trip.id, secret: trip.secret });
    await db.trips.delete(trip.id);
  });
}
