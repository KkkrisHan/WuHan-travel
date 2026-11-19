export type Point = { lng: number; lat: number };
export type Place = {
  placeId: string;
  poiId?: string | null;
  name: string;
  category: string;
  address: string;
  location: Point;
  source: string;
  description: string;
  officialUrl?: string | null;
};
export type Preferences = {
  startDate: string | null;
  days: number;
  people: number;
  company: string;
  budget: string;
  pace: string;
  transport: string;
  interests: string[];
  requirements: string;
  lodging: string;
  lodgingLocation?: Point | null;
};
export type Stop = {
  id: string;
  place: Place;
  arrival: string;
  departure: string;
  minutes: number;
  warning?: string | null;
};
export type Leg = {
  fromStopId: string;
  toStopId: string;
  mode: string;
  minutes: number | null;
  minutesMin?: number | null;
  minutesMax?: number | null;
  distanceMeters: number | null;
  walkingMinutes?: number | null;
  transfers?: number | null;
  source: string;
  polyline: Point[];
};
export type DayPlan = {
  id: string;
  date: string;
  stops: Stop[];
  legs: Leg[];
  transportMinutes: number | null;
  sightseeingMinutes: number;
  meals: string[];
  budgetNote: string;
  warnings: string[];
};
export type Candidate = {
  id: string;
  title: string;
  subtitle: string;
  rationale: string;
  days: DayPlan[];
  warnings: string[];
};
export type Message = {
  id: string;
  role: "user" | "assistant";
  kind: "chat" | "intro";
  text: string;
  status: "running" | "completed" | "failed";
  place?: Place;
  error?: string;
  createdAt: string;
};
export type TripData = {
  name: string;
  nameSource: "default" | "suggested" | "manual";
  preferences: Preferences;
  savedPlaces: Place[];
  pendingPlaces: Place[];
  itinerary: DayPlan[];
  generatedItineraries?: GeneratedItinerary[];
};
export type GeneratedItinerary = {
  id: string;
  title: string;
  markdown: string;
  people: number;
  days: number;
  requirements: string;
  mustVisitPlaces: Place[];
  recommendedPlaces: Place[];
  createdAt: string;
  dayPlans?: DayPlan[];
  strategy?: { priorities: string[]; dayThemes: string[]; conflicts: string[]; reviewIssues: string[] };
  metrics?: { transportMinutes: number[]; walkingMinutes: number[]; requiredCoverage: number };
};
export type TripVersion = {
  id: string;
  at: string;
  summary: string;
  source: "user" | "agent";
  data: TripData;
};
export type Trip = TripData & {
  id: string;
  secret: string;
  revision: number;
  createdAt: string;
  updatedAt: string;
  messages: Message[];
  history: TripVersion[];
  redo: TripVersion[];
};
export type TaskCreated = {
  taskId: string;
  accessToken: string;
  baseRevision: number;
};
export type TaskEvent = {
  taskId: string;
  eventId: number;
  timestamp: string;
  type: string;
  payload: Record<string, unknown>;
};
