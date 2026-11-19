import type { DayPlan, Place, Point } from "../types";
import { apiBase } from "./api";

export type ItineraryInput = {
  mustVisitPlaces: Place[];
  people: number;
  days: number;
  requirements: string;
  startDate: string | null;
  transport: string;
  company: string;
  pace: string;
  budget: string;
  interests: string[];
  lodging: string;
  lodgingLocation?: Point | null;
};

export type ItineraryResult = {
  title: string;
  markdown: string;
  recommendedPlaces: Place[];
  routeSource: string;
  dayPlans: DayPlan[];
  strategy?: { priorities: string[]; dayThemes: string[]; conflicts: string[]; reviewIssues: string[] };
  metrics?: { transportMinutes: number[]; walkingMinutes: number[]; requiredCoverage: number };
};

type ProgressEvent =
  | { type: "progress"; percent: number; label: string }
  | { type: "complete"; percent: number; label: string; result: ItineraryResult }
  | { type: "error"; detail: string }
  | { type: "heartbeat" };

export async function generateItineraryWithProgress(
  input: ItineraryInput,
  onProgress: (percent: number, label: string) => void,
): Promise<ItineraryResult> {
  const controller = new AbortController();
  let expired = false;
  const timeout = window.setTimeout(() => {
    expired = true;
    controller.abort();
  }, 180000);
  try {
    const response = await fetch(`${apiBase}/api/itineraries/generate/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
      signal: controller.signal,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => null);
      throw new Error(typeof error?.detail === "string" ? error.detail : `生成失败（HTTP ${response.status}）`);
    }
    if (!response.body) throw new Error("服务端未开启行程进度流");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let outcome: ItineraryResult | null = null;

    const handleLine = (line: string) => {
      if (!line.trim()) return;
      const event = JSON.parse(line) as ProgressEvent;
      if (event.type === "progress") {
        onProgress(Math.max(0, Math.min(99, event.percent)), event.label);
      } else if (event.type === "error") {
        throw new Error(event.detail || "行程生成失败");
      } else if (event.type === "complete") {
        outcome = event.result;
        onProgress(100, "行程已生成，正在保存");
      }
    };

    while (true) {
      const { value, done } = await reader.read();
      if (value) buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) handleLine(line);
      if (done) {
        if (buffer.trim()) handleLine(buffer);
        break;
      }
    }
    if (!outcome) throw new Error("进度流提前结束，行程未保存，请重试");
    return outcome;
  } catch (error) {
    if (expired) throw new Error("行程生成超时，请缩短行程或稍后重试");
    if (error instanceof TypeError) throw new Error("无法连接行程生成服务");
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}
