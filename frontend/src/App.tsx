import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { renderToStaticMarkup } from "react-dom/server";
import { useLiveQuery } from "dexie-react-hooks";
import {
  IconArrowLeft,
  IconArrowsMaximize,
  IconArrowsMinimize,
  IconBookmark,
  IconChevronDown,
  IconCompass,
  IconHistory,
  IconLayoutSidebarRight,
  IconMapPin,
  IconRoute,
  IconSparkles,
} from "@tabler/icons-react";
import { Dashboard } from "./components/Dashboard";
import { Wizard } from "./components/Wizard";
import { MapWorkspace } from "./components/MapWorkspace";
import { AssistantPanel } from "./components/AssistantPanel";
import { ItineraryPanel } from "./components/ItineraryPanel";
import { MustVisitPanel } from "./components/MustVisitPanel";
import { api, subscribe } from "./lib/api";
import {
  db,
  deleteTrip,
  duplicateTrip,
  mutateTrip,
  newTrip,
  restoreVersion,
  undoTrip,
  updateMessages,
} from "./lib/db";
import { exportTrip } from "./lib/export";
import { recoverItinerary } from "./lib/itinerary";
import { generateItineraryWithProgress } from "./lib/itineraryStream";
import type {
  Candidate,
  Message,
  Place,
  Preferences,
  TaskCreated,
  TaskEvent,
  Trip,
  GeneratedItinerary,
} from "./types";

type Page = "dashboard" | "wizard" | "workspace";
const newMessage = (
  role: Message["role"],
  kind: Message["kind"],
  text: string,
  place?: Place,
): Message => ({
  id: crypto.randomUUID(),
  role,
  kind,
  text,
  status: role === "user" ? "completed" : "running",
  place,
  createdAt: new Date().toISOString(),
});
const debounceCleanup = async () => {
  for (const item of await db.cleanup.toArray()) {
    try {
      await api.cleanup(item);
      await db.cleanup.delete(item.id);
    } catch {
      /* Retry on next launch or online event. */
    }
  }
};

function App() {
  void ItineraryPanel; void restoreVersion;
  const trips = useLiveQuery(() => db.trips.toArray(), [], []);
  const [page, setPage] = useState<Page>("dashboard");
  const [tripId, setTripId] = useState<string | null>(null);
  const trip = useLiveQuery(
    async () => (tripId ? await db.trips.get(tripId) : undefined),
    [tripId],
  );
  const [featured, setFeatured] = useState<Place[]>([]);
  const [selected, setSelected] = useState<Place | null>(null);
  const [rightTab, setRightTab] = useState("assistant");
  const [routeDocumentId, setRouteDocumentId] = useState<string | null>(null);
  const [generateOpen, setGenerateOpen] = useState(false);
  const [generateBusy, setGenerateBusy] = useState(false);
  const [generateProgress, setGenerateProgress] = useState({ percent: 0, label: "准备生成行程" });
  const [generateError, setGenerateError] = useState("");
  const [specialRequirements, setSpecialRequirements] = useState("");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [candidateBase, setCandidateBase] = useState<number | null>(null);
  const [activeDay, setActiveDay] = useState(0);
  const [showAll, setShowAll] = useState(false);
  const [planningTask, setPlanningTask] = useState<TaskCreated | null>(null);
  const [progress, setProgress] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [split, setSplit] = useState(() =>
    Number(localStorage.getItem("wuhan-map-split") || 55),
  );
  const [fullscreen, setFullscreen] = useState(false);
  const [modelConfigured, setModelConfigured] = useState(false);
  const activeTasks = useRef(
    new Map<string, { task: TaskCreated; close: () => void }>(),
  );
  const workspaceRef = useRef<HTMLDivElement>(null);
  const splitDragging = useRef(false);
  const isLocked = !!planningTask;

  const inform = useCallback(
    (error: unknown) =>
      setNotice(error instanceof Error ? error.message : String(error)),
    [],
  );
  useEffect(() => {
    api
      .featured()
      .then((data) => setFeatured(data.places))
      .catch(() => setNotice("后端尚未连接；启动服务后可搜索地点和规划行程"));
    api
      .health()
      .then((status) => setModelConfigured(status.modelConfigured))
      .catch(() => undefined);
    debounceCleanup();
    const online = () => debounceCleanup();
    window.addEventListener("online", online);
    return () => window.removeEventListener("online", online);
  }, []);
  useEffect(() => {
    const sync = () => setFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, []);
  useEffect(() => {
    const move = (event: PointerEvent) => {
      if (!splitDragging.current || !workspaceRef.current) return;
      const rect = workspaceRef.current.getBoundingClientRect();
      setSplit(
        Math.max(
          35,
          Math.min(
            70,
            Math.round(((event.clientX - rect.left) / rect.width) * 100),
          ),
        ),
      );
    };
    const up = () => {
      if (splitDragging.current) {
        splitDragging.current = false;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, []);
  useEffect(() => {
    localStorage.setItem("wuhan-map-split", String(split));
  }, [split]);
  useEffect(() => {
    const cancelAll = () => {
      for (const { task, close } of activeTasks.current.values()) {
        close();
        api.cancel(task).catch(() => undefined);
      }
      activeTasks.current.clear();
    };
    window.addEventListener("pagehide", cancelAll);
    return () => {
      window.removeEventListener("pagehide", cancelAll);
      cancelAll();
    };
  }, []);

  const openTrip = (next: Trip) => {
    const latest = next.generatedItineraries?.at(-1);
    if (!next.itinerary.length && latest) {
      const recovered = recoverItinerary(latest);
      if (recovered.length) {
        mutateTrip(next.id, "恢复已生成的当前行程路线", (draft) => {
          if (!draft.itinerary.length) draft.itinerary = recovered;
        }, undefined, "agent").catch(inform);
      }
    }
    setTripId(next.id);
    setSelected(null);
    setCandidates([]);
    setPreviewId(null);
    setCandidateBase(null);
    setActiveDay(0);
    setShowAll(false);
    setRightTab("assistant");
    setRouteDocumentId(null);
    setPage("workspace");
  };
  const backToDashboard = () => {
    if (planningTask) api.cancel(planningTask).catch(() => undefined);
    for (const { task, close } of activeTasks.current.values()) {
      close();
      api.cancel(task).catch(() => undefined);
    }
    activeTasks.current.clear();
    setPlanningTask(null);
    setTripId(null);
    setPage("dashboard");
  };
  const create = async (explore: boolean, preferences?: Preferences) => {
    try {
      const created = await newTrip(explore, preferences);
      openTrip(created);
      const requirements = preferences?.requirements?.trim();
      if (!explore && requirements) {
        setNotice("AI 正在从特殊信息中识别必去景点…");
        try {
          const extracted = await api.extractMustVisit(requirements);
          if (extracted.places.length) {
            await mutateTrip(created.id, "自动识别特殊信息中的必去景点", (draft) => {
              const ids = new Set(draft.pendingPlaces.map((place) => place.placeId));
              for (const place of extracted.places) {
                if (!ids.has(place.placeId)) {
                  draft.pendingPlaces.push(place);
                  ids.add(place.placeId);
                }
              }
            }, undefined, "agent");
          }
          setNotice(
            extracted.places.length
              ? `已从特殊信息中添加 ${extracted.places.length} 个必去景点：${extracted.places.map((place) => place.name).join("、")}${extracted.unresolved.length ? "；部分地点未找到，请手动搜索添加" : ""}`
              : extracted.unresolved.length
                ? "识别到地点要求，但地图未找到完全匹配的地点，请手动添加。"
                : "已检查特殊信息，未找到明确要求的必去景点。",
          );
        } catch (error) {
          setNotice(error instanceof Error ? `自动识别未完成：${error.message}，可手动添加必去景点。` : "自动识别未完成，可手动添加必去景点。");
        }
      }
    } catch (error) {
      inform(error);
    }
  };
  const change = async (
    summary: string,
    mutate: (value: Trip) => void,
    expected?: number,
    source?: "user" | "agent",
  ) => {
    if (!trip) return;
    if (isLocked) {
      setNotice("规划期间不能修改旅行，请先取消规划");
      return;
    }
    try {
      await mutateTrip(trip.id, summary, mutate, expected, source);
    } catch (error) {
      inform(error);
    }
  };
  const addPending = (place: Place) =>
    change(`加入${place.name}到待安排`, (draft) => {
      if (!draft.pendingPlaces.some((item) => item.placeId === place.placeId))
        draft.pendingPlaces.push(place);
    });
  const openGenerator = () => {
    if (!trip) return;
    setSpecialRequirements(trip.preferences.requirements || "");
    setGenerateError("");
    setGenerateProgress({ percent: 0, label: "准备生成行程" });
    setGenerateOpen(true);
  };
  const generateDocument = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!trip) return;
    const prefs = trip.preferences;
    if (!Number.isInteger(prefs.people) || prefs.people < 1 || !Number.isInteger(prefs.days) || prefs.days < 1) {
      setGenerateError("旅行设置中的人数或天数无效，请先修改设置。");
      return;
    }
    setGenerateBusy(true);
    setGenerateError("");
    setGenerateProgress({ percent: 0, label: "正在连接行程规划服务" });
    try {
      const requirements = [
        prefs.company ? `同行类型：${prefs.company}` : "",
        `预算：${prefs.budget}；游玩节奏：${prefs.pace}`,
        prefs.interests.length ? `兴趣：${prefs.interests.join("、")}` : "",
        specialRequirements.trim(),
      ].filter(Boolean).join("\n");
      const generated = await generateItineraryWithProgress({
        mustVisitPlaces: trip.pendingPlaces,
        people: prefs.people,
        days: prefs.days,
        requirements,
        startDate: prefs.startDate || null,
        transport: prefs.transport,
        lodging: prefs.lodging || "",
        lodgingLocation: prefs.lodgingLocation || null,
      }, (percent, label) => setGenerateProgress({ percent, label }));
      if (generated.dayPlans.length !== prefs.days || generated.dayPlans.some((day) => !day.stops.length))
        throw new Error("服务端返回的日程不完整，未保存，请重试。");
      const document = {
        id: crypto.randomUUID(),
        title: generated.title,
        markdown: generated.markdown,
        people: prefs.people,
        days: prefs.days,
        requirements,
        mustVisitPlaces: structuredClone(trip.pendingPlaces),
        recommendedPlaces: generated.recommendedPlaces,
        dayPlans: generated.dayPlans,
        createdAt: new Date().toISOString(),
      };
      await mutateTrip(trip.id, "生成当前行程", (draft) => {
        draft.generatedItineraries = [...(draft.generatedItineraries || []), document].slice(-5);
        draft.itinerary = structuredClone(generated.dayPlans);
      });
      setActiveDay(0);
      setShowAll(false);
      setRightTab(document.id);
      setRouteDocumentId(null);
      setGenerateOpen(false);
    } catch (error) {
      setGenerateError(error instanceof Error ? error.message : "行程生成失败");
    } finally {
      setGenerateBusy(false);
    }
  };

  const track = (
    task: TaskCreated,
    handler: (event: TaskEvent) => void,
    failed: (message: string) => void,
  ) => {
    const close = subscribe(
      task,
      (event) => {
        handler(event);
        if (
          ["task_completed", "task_failed", "task_cancelled"].includes(
            event.type,
          )
        )
          activeTasks.current.delete(task.taskId);
      },
      (error) => {
        activeTasks.current.delete(task.taskId);
        failed(error.message);
      },
    );
    activeTasks.current.set(task.taskId, { task, close });
  };
  const runMessage = async (
    message: Message,
    sourceTrip: Trip,
    input?: string,
  ) => {
    try {
      const task =
        message.kind === "intro" && message.place
          ? await api.intro(sourceTrip, message.place)
          : await api.chat(sourceTrip, selected, input || "");
      track(
        task,
        (event) => {
          if (event.type === "message_delta")
            updateMessages(sourceTrip.id, (items) =>
              items.map((item) =>
                item.id === message.id
                  ? {
                      ...item,
                      text: item.text + String(event.payload.text || ""),
                    }
                  : item,
              ),
            ).catch(inform);
          if (event.type === "task_completed") {
            const result = event.payload.result as {
              text?: string;
              action?: string;
              patch?: {
                operation: string;
                dayId: string;
                stopId: string;
                placeName: string;
                baseRevision: number;
              };
            };
            if (result?.patch?.operation === "remove_stop") {
              mutateTrip(
                sourceTrip.id,
                `AI 移除${result.patch.placeName}`,
                (draft) => {
                  const day = draft.itinerary.find(
                    (item) => item.id === result.patch!.dayId,
                  );
                  const removed = day?.stops.find(
                    (stop) => stop.id === result.patch!.stopId,
                  );
                  if (!day || !removed)
                    throw new Error("目标地点已变化，请重试");
                  day.stops = day.stops.filter(
                    (stop) => stop.id !== removed.id,
                  );
                  day.legs = [];
                  day.transportMinutes = null;
                  day.sightseeingMinutes = day.stops.reduce(
                    (sum, stop) => sum + stop.minutes,
                    0,
                  );
                  day.warnings.push("AI 修改后，交通时间需重新核对。");
                  if (
                    !draft.pendingPlaces.some(
                      (place) => place.placeId === removed.place.placeId,
                    )
                  )
                    draft.pendingPlaces.push(removed.place);
                },
                result.patch.baseRevision,
                "agent",
              )
                .then(() =>
                  updateMessages(sourceTrip.id, (items) =>
                    items.map((item) =>
                      item.id === message.id
                        ? {
                            ...item,
                            text: `已从行程中移除${result.patch!.placeName}，地点已放回待安排。交通时间需要重新核对。`,
                            status: "completed",
                          }
                        : item,
                    ),
                  ),
                )
                .catch((error) =>
                  updateMessages(sourceTrip.id, (items) =>
                    items.map((item) =>
                      item.id === message.id
                        ? {
                            ...item,
                            text: "修改未应用。",
                            status: "failed",
                            error:
                              error instanceof Error
                                ? error.message
                                : "版本冲突",
                          }
                        : item,
                    ),
                  ),
                );
            } else {
              updateMessages(sourceTrip.id, (items) =>
                items.map((item) =>
                  item.id === message.id
                    ? {
                        ...item,
                        text: String(result?.text || item.text),
                        status: "completed",
                      }
                    : item,
                ),
              ).catch(inform);
              if (result?.action === "optimization") {
                setRightTab("must-visit");
                generate("optimize");
              }
            }
          }
          if (event.type === "task_failed" || event.type === "task_cancelled")
            updateMessages(sourceTrip.id, (items) =>
              items.map((item) =>
                item.id === message.id
                  ? {
                      ...item,
                      status: "failed",
                      error: String(event.payload.message || "生成失败"),
                    }
                  : item,
              ),
            ).catch(inform);
        },
        (error) =>
          updateMessages(sourceTrip.id, (items) =>
            items.map((item) =>
              item.id === message.id
                ? { ...item, status: "failed", error }
                : item,
            ),
          ).catch(inform),
      );
    } catch (error) {
      await updateMessages(sourceTrip.id, (items) =>
        items.map((item) =>
          item.id === message.id
            ? {
                ...item,
                status: "failed",
                error: error instanceof Error ? error.message : "请求失败",
              }
            : item,
        ),
      );
      inform(error);
    }
  };
  const sendChat = async (input: string) => {
    if (!trip) return;
    const user = newMessage("user", "chat", input),
      answer = newMessage("assistant", "chat", "");
    await updateMessages(trip.id, (items) => [...items, user, answer]);
    setRightTab("assistant");
    runMessage(answer, trip, input);
  };
  const introPlace = async (place: Place) => {
    if (!trip) return;
    const message = newMessage("assistant", "intro", "", place);
    await updateMessages(trip.id, (items) => [...items, message]);
    setRightTab("assistant");
    runMessage(message, trip);
  };
  const retryMessage = async (message: Message) => {
    if (!trip) return;
    await updateMessages(trip.id, (items) =>
      items.map((item) =>
        item.id === message.id
          ? { ...item, text: "", error: undefined, status: "running" }
          : item,
      ),
    );
    runMessage(
      message,
      trip,
      trip.messages.find(
        (_, index) => trip.messages[index + 1]?.id === message.id,
      )?.text,
    );
  };
  const generate = async (mode: "plan" | "optimize" = "plan") => {
    if (!trip) return;
    setCandidates([]);
    setPreviewId(null);
    setProgress("");
    try {
      const task =
        mode === "optimize"
          ? await api.optimize(trip, selected)
          : await api.plan(trip, selected);
      setPlanningTask(task);
      setCandidateBase(task.baseRevision);
      track(
        task,
        (event) => {
          if (event.type === "progress")
            setProgress(String(event.payload.label || "正在规划…"));
          if (event.type === "candidate_created")
            setCandidates((items) => [
              ...items,
              event.payload.candidate as Candidate,
            ]);
          if (
            event.type === "task_completed" ||
            event.type === "task_failed" ||
            event.type === "task_cancelled"
          ) {
            setPlanningTask(null);
            if (event.type === "task_failed")
              setNotice(String(event.payload.message || "规划失败"));
            if (event.type === "task_cancelled")
              setNotice("规划已取消，已生成的候选方案保留供查看");
          }
        },
        (error) => {
          setPlanningTask(null);
          setNotice(error);
        },
      );
    } catch (error) {
      inform(error);
    }
  };
  const cancelPlan = async () => {
    if (!planningTask) return;
    try {
      await api.cancel(planningTask);
    } catch (error) {
      inform(error);
    }
  };
  const adopt = async (candidate: Candidate) => {
    if (!trip || candidateBase === null) return;
    if (isLocked) {
      setNotice("请等待规划完成或取消");
      return;
    }
    if (
      candidate.warnings.some((warning) =>
        /固定时间|未找到明确要求|必去地点超过/.test(warning),
      ) &&
      !window.confirm(
        `此方案仍有未满足或未核实的硬性安排：\n${candidate.warnings.join("\n")}\n\n仍要将它作为草稿行程保存吗？`,
      )
    )
      return;
    try {
      await mutateTrip(
        trip.id,
        `采用${candidate.title}方案`,
        (draft) => {
          draft.itinerary = structuredClone(candidate.days);
        },
        candidateBase,
        "agent",
      );
      setCandidates([]);
      setPreviewId(null);
      setActiveDay(0);
      setShowAll(false);
      setNotice(`已采用「${candidate.title}」方案`);
    } catch (error) {
      inform(error);
    }
  };
  const addStop = (place: Place, day: number) =>
    change(`将${place.name}加入第${day + 1}天`, (draft) => {
      const target = draft.itinerary[day];
      if (!target) return;
      target.stops.push({
        id: crypto.randomUUID(),
        place,
        arrival: "待调整",
        departure: "待调整",
        minutes: 90,
        warning: "手动加入后，开放与路线时间待核实",
      });
      target.sightseeingMinutes += 90;
      target.transportMinutes = null;
      target.legs = [];
      target.warnings = [
        ...target.warnings,
        "手动修改后，交通与时间需重新核对。",
      ];
      draft.pendingPlaces = draft.pendingPlaces.filter(
        (item) => item.placeId !== place.placeId,
      );
    });
  const removeStop = (day: number, stopId: string) =>
    change("移除行程地点", (draft) => {
      const target = draft.itinerary[day];
      if (!target) return;
      const removed = target.stops.find((stop) => stop.id === stopId);
      target.stops = target.stops.filter((stop) => stop.id !== stopId);
      target.legs = [];
      target.transportMinutes = null;
      target.sightseeingMinutes = target.stops.reduce(
        (sum, stop) => sum + stop.minutes,
        0,
      );
      target.warnings = [
        ...target.warnings,
        "地点已调整，交通时间需重新核对。",
      ];
      if (
        removed &&
        !draft.pendingPlaces.some(
          (item) => item.placeId === removed.place.placeId,
        )
      )
        draft.pendingPlaces.push(removed.place);
    });
  const moveStop = (day: number, stopId: string, direction: -1 | 1) =>
    change("调整行程顺序", (draft) => {
      const target = draft.itinerary[day];
      if (!target) return;
      const index = target.stops.findIndex((stop) => stop.id === stopId),
        next = index + direction;
      if (index < 0 || next < 0 || next >= target.stops.length) return;
      [target.stops[index], target.stops[next]] = [
        target.stops[next],
        target.stops[index],
      ];
      target.legs = [];
      target.transportMinutes = null;
      target.warnings = [
        ...target.warnings,
        "顺序已调整，交通时间需重新核对。",
      ];
    });
  const selectedCandidate = candidates.find(
    (candidate) => candidate.id === previewId,
  );
  const routeInput = JSON.stringify(
    selectedCandidate?.days || trip?.itinerary || [],
  );
  const routeSourceDays = useMemo(
    () => JSON.parse(routeInput) as Trip["itinerary"],
    [routeInput],
  );
  const routeDays = useMemo(() => {
    return showAll
      ? routeSourceDays
      : routeSourceDays[activeDay]
        ? [routeSourceDays[activeDay]]
        : [];
  }, [routeSourceDays, activeDay, showAll]);
  const routeDocumentData = JSON.stringify(
    routeDocumentId
      ? (trip?.generatedItineraries || []).find((doc) => doc.id === routeDocumentId) || null
      : null,
  );
  const connectedRouteDays = useMemo(() => {
    if (!routeDocumentData || routeDocumentData === "null") return routeDays;
    const document = JSON.parse(routeDocumentData) as GeneratedItinerary;
    return recoverItinerary(document);
  }, [routeDocumentData, routeDays]);
  const selectPlace = (place: Place | null) => {
    setSelected(place);
    if (!place) return;
    const days = selectedCandidate?.days || trip?.itinerary || [];
    const dayIndex = days.findIndex((day) =>
      day.stops.some((stop) => stop.place.placeId === place.placeId),
    );
    if (dayIndex >= 0) {
      const stopId = days[dayIndex].stops.find(
        (stop) => stop.place.placeId === place.placeId,
      )?.id;
      setRightTab("must-visit");
      setActiveDay(dayIndex);
      setShowAll(false);
      if (stopId)
        window.setTimeout(
          () =>
            document
              .getElementById(`stop-${stopId}`)
              ?.scrollIntoView({ block: "center", behavior: "smooth" }),
          50,
        );
    }
  };
  const handleExport = async (format: "md" | "pdf", days: number[]) => {
    if (!trip) return;
    try {
      await exportTrip(trip, days, format);
    } catch (error) {
      inform(error);
    }
  };
  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch (error) {
      inform(error);
    }
  };

  void progress; void cancelPlan; void adopt; void addStop; void removeStop; void moveStop; void handleExport;
  if (page === "wizard")
    return (
      <Wizard
        onBack={() => setPage("dashboard")}
        onFinish={(preferences) => create(false, preferences)}
      />
    );
  if (page === "dashboard" || !trip)
    return (
      <Dashboard
        trips={trips}
        onOpen={openTrip}
        onExplore={() => create(true)}
        onPlan={() => setPage("wizard")}
        onRename={(target, name) =>
          mutateTrip(target.id, "重命名旅行", (draft) => {
            draft.name = name;
            draft.nameSource = "manual";
          }).catch(inform)
        }
        onCopy={(target) =>
          duplicateTrip(target)
            .then((copy) => setNotice(`已复制「${copy.name}」`))
            .catch(inform)
        }
        onDelete={(target) => {
          if (window.confirm(`确定删除「${target.name}」？此操作无法撤销。`))
            deleteTrip(target)
              .then(() => debounceCleanup())
              .catch(inform);
        }}
      />
    );
  return (
    <div className="app-shell">
      <header className="global-header workspace-header">
        <div className="workspace-head-left">
          <button
            className="back-button"
            onClick={backToDashboard}
            aria-label="返回旅行列表"
          >
            <IconArrowLeft size={20} />
          </button>
          <div className="brand">
            <span className="brand-mark small">
              <IconCompass size={17} />
            </span>
            <span>漫游武汉</span>
          </div>
          <span className="header-divider" />
          <span className="trip-title">
            {trip.name}
            <IconChevronDown size={16} />
          </span>
        </div>
        <div className="workspace-head-right">
          <button
            className="header-tool"
            disabled={!trip.history.length || isLocked}
            title="撤销"
            onClick={() => undoTrip(trip.id).catch(inform)}
          >
            <IconHistory size={18} />
            撤销
          </button>
          <button
            className="header-tool"
            disabled={!trip.redo.length || isLocked}
            title="重做"
            onClick={() => undoTrip(trip.id, true).catch(inform)}
          >
            重做
          </button>
          <span className="header-divider" />
          <span className="save-indicator">
            <span className="status-dot" />
            已保存到本地
          </span>
          <button
            className="icon-button"
            title={fullscreen ? "退出全屏" : "全屏"}
            aria-label={fullscreen ? "退出全屏" : "全屏"}
            onClick={toggleFullscreen}
          >
            {fullscreen ? (
              <IconArrowsMinimize size={19} />
            ) : (
              <IconArrowsMaximize size={19} />
            )}
          </button>
        </div>
      </header>
      <div
        className={`workspace ${fullscreen ? "is-fullscreen" : ""}`}
        ref={workspaceRef}
        style={{ "--map-width": `${split}%` } as React.CSSProperties}
      >
        <MapWorkspace
          trip={trip}
          featured={featured}
          selected={selected}
          onSelect={selectPlace}
          onAddPending={addPending}
          onIntro={introPlace}
          routeDays={connectedRouteDays}
          routeFocus={!!routeDocumentId}
          routeLabel={
            routeDocumentId
              ? "行程路线 · 全部天数"
              : selectedCandidate
              ? `预览 · ${selectedCandidate.title}`
              : routeDays.length
                ? showAll
                  ? "全部日期路线"
                  : `第 ${activeDay + 1} 天路线`
                : "探索武汉"
          }
          onMapError={setNotice}
        />
        <div
          className="split-handle"
          role="separator"
          aria-label="调整地图与面板比例"
          aria-orientation="vertical"
          tabIndex={0}
          onPointerDown={(event) => {
            splitDragging.current = true;
            document.body.style.cursor = "col-resize";
            document.body.style.userSelect = "none";
            event.currentTarget.setPointerCapture(event.pointerId);
          }}
          onKeyDown={(event) => {
            if (event.key === "ArrowLeft")
              setSplit((value) => Math.max(35, value - 2));
            if (event.key === "ArrowRight")
              setSplit((value) => Math.min(70, value + 2));
          }}
        >
          <span />
        </div>
        <aside className="right-panel">
          <div className="right-tabs" role="tablist" aria-label="工作区">
            <button
              role="tab"
              aria-selected={rightTab === "assistant"}
              className={rightTab === "assistant" ? "active" : ""}
              onClick={() => setRightTab("assistant")}
            >
              <IconSparkles size={18} />
              AI 助手
            </button>
            <button
              role="tab"
              aria-selected={rightTab === "must-visit"}
              className={rightTab === "must-visit" ? "active" : ""}
              onClick={() => setRightTab("must-visit")}
            >
              <IconLayoutSidebarRight size={18} />
              必去景点 <span>{trip.pendingPlaces.length}</span>
            </button>
            {(trip.generatedItineraries || []).map((document) => <button role="tab" aria-selected={rightTab === document.id} className={rightTab === document.id ? "active" : ""} key={document.id} onClick={() => setRightTab(document.id)}>{document.title}</button>)}
          </div>
          {rightTab === "assistant" ? (
            <AssistantPanel
              messages={trip.messages}
              selected={selected}
              modelConfigured={modelConfigured}
              onSend={sendChat}
              onRetry={retryMessage}
              onIntro={introPlace}
            />
          ) : rightTab === "must-visit" ? <MustVisitPanel places={trip.pendingPlaces} onSelect={selectPlace} onRemove={(place) => change(`移除必去地点${place.name}`, draft => { draft.pendingPlaces = draft.pendingPlaces.filter(item => item.placeId !== place.placeId); })} onGenerate={openGenerator} /> : <GeneratedDocument document={(trip.generatedItineraries || []).find(item => item.id === rightTab)} routeActive={routeDocumentId === rightTab} onToggleRoute={() => setRouteDocumentId(current => current === rightTab ? null : rightTab)} />}
        </aside>
      </div>
      <div className="mobile-nav">
        <button
          onClick={() => setRightTab("assistant")}
          className={rightTab === "assistant" ? "active" : ""}
        >
          <IconSparkles size={18} />
          助手
        </button>
        <button
          onClick={() => setRightTab("must-visit")}
          className={rightTab === "must-visit" ? "active" : ""}
        >
          <IconRoute size={18} />
          必去
        </button>
        <button onClick={() => setSelected(null)}>
          <IconMapPin size={18} />
          地图
        </button>
        <button onClick={backToDashboard}>
          <IconBookmark size={18} />
          旅行
        </button>
      </div>
      {notice && (
        <div className="toast" role="status">
          <span>{notice}</span>
          <button onClick={() => setNotice(null)} aria-label="关闭提示">
            ×
          </button>
        </div>
      )}
      {generateOpen && (
        <div className="modal-backdrop" role="presentation">
          <form className="generator-modal" onSubmit={generateDocument}>
            <button type="button" className="modal-close" onClick={() => setGenerateOpen(false)}>×</button>
            <h2>生成旅行行程</h2>
            <p className="form-note">以下资料已从旅行设置自动读取，无需再次填写。</p>
            <div className="generator-summary">
              <div><span>同行人数与类型</span><strong>{trip.preferences.people} 人 · {trip.preferences.company || "未指定类型"}</strong></div>
              <div><span>天数与日期</span><strong>{trip.preferences.days} 天 · {trip.preferences.startDate || "日期未定"}</strong></div>
              <div><span>主要交通方式</span><strong>{trip.preferences.transport}</strong></div>
              <div><span>住宿 / 每日出发地点</span><strong>{trip.preferences.lodging || "未指定"}</strong></div>
            </div>
            <label>补充行程要求 <span>可选，已带入旅行设置中的要求</span>
              <textarea value={specialRequirements} onChange={(e) => setSpecialRequirements(e.target.value)} rows={3} placeholder="例如：周六下午有固定活动，优先安排附近景点" maxLength={1000} />
            </label>
            <p>必去景点 {trip.pendingPlaces.length} 个 · 生成后自动保存为当前行程并显示路线。</p>
            {(generateBusy || generateProgress.percent > 0) && (
              <div className="generate-progress" role="status" aria-live="polite">
                <div className="generate-progress-head"><strong>{generateProgress.label}</strong><span>{generateProgress.percent}%</span></div>
                <progress aria-label="行程生成阶段进度" max={100} value={generateProgress.percent} />
                <small>根据景点搜索、偏好筛选与逐日路线计算的阶段性进度更新；复杂路线可能耗时较长。</small>
              </div>
            )}
            {generateError && <div role="alert">{generateError}</div>}
            <button type="button" disabled={generateBusy} onClick={() => setGenerateOpen(false)}>取消</button>
            <button className="button primary" disabled={generateBusy}>{generateBusy ? "正在生成行程…" : "生成当前行程"}</button>
          </form>
        </div>
      )}
    </div>
  );
}

function GeneratedDocument({document,routeActive,onToggleRoute}: {document?: GeneratedItinerary;routeActive:boolean;onToggleRoute:()=>void}) {
  if (!document) return null;
  const exportPdf = async () => {
    const popup = window.open("", "_blank");
    if (!popup) { window.alert("请允许弹出打印窗口"); return; }
    const html = renderToStaticMarkup(<ReactMarkdown remarkPlugins={[remarkGfm]}>{document.markdown}</ReactMarkdown>);
    const title = document.title.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
    popup.document.open();
    popup.document.write(`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>${title}</title><style>
      @page{size:A4;margin:15mm 13mm}*{box-sizing:border-box}html,body{height:auto!important;overflow:visible!important}body{margin:0;color:#17252a;background:#fff;font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif;font-size:10.5pt;line-height:1.55}
      .markdown-content{width:100%;max-width:none;overflow:visible;overflow-wrap:anywhere}h1{font-size:22pt}h2{font-size:16pt;margin-top:1.25em}h3{font-size:12pt}h1,h2,h3{break-after:avoid-page}p,li,blockquote{orphans:2;widows:2}table{width:100%;border-collapse:collapse;table-layout:fixed;break-inside:auto}thead{display:table-header-group}tr{break-inside:avoid-page}th,td{border:1px solid #7a8a8d;padding:5px 7px;text-align:left;overflow-wrap:anywhere;vertical-align:top}blockquote{border-left:3px solid #5e8e90;padding-left:12px;margin-left:0}a{color:#145c77}hr{border:0;border-top:1px solid #aaa}
      </style></head><body><main class="markdown-content">${html}</main></body></html>`);
    popup.document.close();
    await popup.document.fonts.ready;
    await Promise.all(Array.from(popup.document.images).map(img => img.complete ? Promise.resolve() : new Promise<void>(resolve => { img.onload = img.onerror = () => resolve(); })));
    popup.focus(); popup.print();
  };
  const exportMarkdown = () => { const url = URL.createObjectURL(new Blob([document.markdown], {type:"text/markdown;charset=utf-8"})); const link = Object.assign(window.document.createElement("a"), {href:url, download:`${document.title}.md`}); link.click(); URL.revokeObjectURL(url); };
  return <article className="generated-document"><div className="document-actions"><button className={`route-connect-button ${routeActive ? "active" : ""}`} aria-pressed={routeActive} onClick={onToggleRoute}><IconRoute size={16}/>{routeActive ? "关闭路线连线" : "路线连线"}</button><button onClick={exportMarkdown}>导出 Markdown</button><button onClick={exportPdf}>导出 PDF</button></div><div className="markdown-content"><ReactMarkdown remarkPlugins={[remarkGfm]}>{document.markdown}</ReactMarkdown></div></article>;
}

export default App;
