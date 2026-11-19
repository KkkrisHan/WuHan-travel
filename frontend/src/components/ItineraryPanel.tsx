import { useState } from "react";
import {
  IconAlertCircle,
  IconArrowDown,
  IconArrowRight,
  IconArrowUp,
  IconBookmark,
  IconCalendar,
  IconCheck,
  IconClock,
  IconDownload,
  IconHistory,
  IconMapPin,
  IconPlus,
  IconRoute,
  IconSparkles,
  IconTrash,
  IconX,
} from "@tabler/icons-react";
import type { Candidate, Place, Trip } from "../types";

type Props = {
  trip: Trip;
  candidates: Candidate[];
  previewId: string | null;
  activeDay: number;
  onDay: (index: number) => void;
  showAll: boolean;
  onShowAll: (value: boolean) => void;
  planning: boolean;
  progress: string;
  onGenerate: () => void;
  onOptimize: () => void;
  onCancel: () => void;
  onPreview: (id: string | null) => void;
  onAdopt: (candidate: Candidate) => void;
  onSelectPlace: (place: Place) => void;
  onRemovePending: (place: Place) => void;
  onAddStop: (place: Place, day: number) => void;
  onRemoveStop: (day: number, stopId: string) => void;
  onMoveStop: (day: number, stopId: string, direction: -1 | 1) => void;
  onRestore: (versionId: string) => void;
  onExport: (type: "md" | "pdf", days: number[]) => void;
};

export function ItineraryPanel(props: Props) {
  const {
    trip,
    candidates,
    previewId,
    activeDay,
    onDay,
    showAll,
    onShowAll,
    planning,
    progress,
    onGenerate,
    onOptimize,
    onCancel,
    onPreview,
    onAdopt,
    onSelectPlace,
    onRemovePending,
    onAddStop,
    onRemoveStop,
    onMoveStop,
    onRestore,
    onExport,
  } = props;
  const [subtab, setSubtab] = useState<"days" | "pending" | "history">("days");
  const [exportOpen, setExportOpen] = useState(false);
  const [exportDays, setExportDays] = useState<number[]>([]);
  const [addDay, setAddDay] = useState<string | null>(null);
  const preview = candidates.find((candidate) => candidate.id === previewId);
  const days = preview?.days || trip.itinerary;
  const active = days[activeDay] || days[0];
  const openExport = () => {
    setExportDays(trip.itinerary.map((_, index) => index));
    setExportOpen(true);
  };
  const compare = (candidate: Candidate) => {
    const beforeStops = trip.itinerary.reduce(
      (sum, day) => sum + day.stops.length,
      0,
    );
    const afterStops = candidate.days.reduce(
      (sum, day) => sum + day.stops.length,
      0,
    );
    const beforeTravel = trip.itinerary.every(
      (day) => day.transportMinutes !== null,
    )
      ? trip.itinerary.reduce(
          (sum, day) => sum + (day.transportMinutes || 0),
          0,
        )
      : null;
    const afterTravel = candidate.days.every(
      (day) => day.transportMinutes !== null,
    )
      ? candidate.days.reduce(
          (sum, day) => sum + (day.transportMinutes || 0),
          0,
        )
      : null;
    return `相比当前行程：地点 ${afterStops - beforeStops >= 0 ? "+" : ""}${afterStops - beforeStops} 处 · 交通 ${beforeTravel === null || afterTravel === null ? "变化待核实" : `${afterTravel - beforeTravel >= 0 ? "+" : ""}${afterTravel - beforeTravel} 分钟`}`;
  };
  return (
    <div className="itinerary-panel">
      <div className="panel-heading itinerary-heading">
        <div>
          <div className="panel-kicker">
            <IconRoute size={15} /> 旅行行程
          </div>
          <h2>把想去的，串成路线</h2>
        </div>
        <button
          className="icon-button"
          title="导出行程"
          aria-label="导出行程"
          disabled={!trip.itinerary.length}
          onClick={openExport}
        >
          <IconDownload size={20} />
        </button>
      </div>
      <div className="itinerary-tabs" role="tablist" aria-label="行程内容">
        <button
          role="tab"
          aria-selected={subtab === "days"}
          className={subtab === "days" ? "active" : ""}
          onClick={() => setSubtab("days")}
        >
          每日行程 <span>{trip.itinerary.length}</span>
        </button>
        <button
          role="tab"
          aria-selected={subtab === "pending"}
          className={subtab === "pending" ? "active" : ""}
          onClick={() => setSubtab("pending")}
        >
          待安排 <span>{trip.pendingPlaces.length}</span>
        </button>
        <button
          role="tab"
          aria-selected={subtab === "history"}
          className={subtab === "history" ? "active" : ""}
          onClick={() => setSubtab("history")}
        >
          历史版本
        </button>
      </div>
      <div className="itinerary-scroll">
        {subtab === "days" && (
          <>
            <div className="trip-summary">
              <div>
                <span>旅行设置</span>
                <strong>
                  {trip.preferences.days} 天 · {trip.preferences.people} 人 ·{" "}
                  {trip.preferences.pace}节奏
                </strong>
              </div>
              <div>
                <span>出发日期</span>
                <strong>{trip.preferences.startDate || "尚未确定"}</strong>
              </div>
            </div>
            {planning && (
              <div className="planning-status">
                <span className="pulse-dot" />
                <div>
                  <strong>正在规划路线</strong>
                  <p>{progress || "正在整理旅行信息…"}</p>
                </div>
                <button onClick={onCancel}>取消</button>
              </div>
            )}
            {candidates.length > 0 && (
              <section className="candidates">
                <div className="section-inline-title">
                  <IconSparkles size={17} />
                  <h3>为你找到 {candidates.length} 套路线</h3>
                </div>
                <p className="small-muted">
                  预览仅影响地图显示，采用后才会保存正式行程。
                </p>
                {candidates.map((candidate) => (
                  <article
                    className={`candidate ${previewId === candidate.id ? "selected" : ""}`}
                    key={candidate.id}
                  >
                    <div className="candidate-head">
                      <div>
                        <strong>{candidate.title}</strong>
                        <span>{candidate.subtitle}</span>
                      </div>
                      {previewId === candidate.id && (
                        <span className="preview-badge">预览中</span>
                      )}
                    </div>
                    <p>{candidate.rationale}</p>
                    <div className="candidate-meta">
                      <IconCalendar size={15} />
                      {candidate.days.length} 天 <IconMapPin size={15} />
                      {candidate.days.reduce(
                        (sum, day) => sum + day.stops.length,
                        0,
                      )}{" "}
                      处地点
                    </div>
                    {trip.itinerary.length > 0 && (
                      <div className="candidate-diff">{compare(candidate)}</div>
                    )}
                    {candidate.warnings.length > 0 && (
                      <div className="candidate-warning">
                        <IconAlertCircle size={15} />
                        {candidate.warnings[0]}
                      </div>
                    )}
                    <div className="candidate-actions">
                      <button
                        className="button secondary"
                        onClick={() =>
                          onPreview(
                            previewId === candidate.id ? null : candidate.id,
                          )
                        }
                      >
                        {previewId === candidate.id ? "退出预览" : "地图预览"}
                      </button>
                      <button
                        className="button primary"
                        disabled={planning}
                        onClick={() => onAdopt(candidate)}
                      >
                        采用方案 <IconArrowRight size={16} />
                      </button>
                    </div>
                  </article>
                ))}
              </section>
            )}
            {days.length > 0 ? (
              <section className="days-section">
                <div className="days-top">
                  <h3>{preview ? "方案预览" : "已保存行程"}</h3>
                  <button
                    className={`all-days-button ${showAll ? "active" : ""}`}
                    onClick={() => onShowAll(!showAll)}
                  >
                    {showAll ? "查看单日" : "显示全部"}
                  </button>
                </div>
                <div className="day-switcher">
                  {days.map((day, index) => (
                    <button
                      className={
                        activeDay === index && !showAll ? "active" : ""
                      }
                      key={day.id}
                      onClick={() => {
                        onDay(index);
                        onShowAll(false);
                      }}
                    >
                      {day.date.startsWith("第")
                        ? day.date
                        : `第 ${index + 1} 天`}
                      <small>
                        {day.date.startsWith("第") ? "" : day.date.slice(5)}
                      </small>
                    </button>
                  ))}
                </div>
                {(showAll ? days : active ? [active] : []).map((day) => {
                  const dayIndex = days.findIndex((item) => item.id === day.id);
                  return (
                    <div className="day-detail" key={day.id}>
                      <div className="day-detail-head">
                        <strong>{day.date}</strong>
                        <span>
                          <IconClock size={15} />
                          游览{" "}
                          {Math.round((day.sightseeingMinutes / 60) * 10) /
                            10}{" "}
                          小时 · 交通{" "}
                          {day.transportMinutes === null
                            ? "待核实"
                            : `${day.transportMinutes} 分钟`}
                        </span>
                      </div>
                      <div className="stop-list">
                        {day.stops.map((stop, index) => (
                          <div
                            className="stop-block"
                            id={`stop-${stop.id}`}
                            key={stop.id}
                          >
                            <div className="stop-row">
                              <span className="stop-order">
                                {String(index + 1).padStart(2, "0")}
                              </span>
                              <button
                                className="stop-info"
                                onClick={() => onSelectPlace(stop.place)}
                              >
                                <strong>{stop.place.name}</strong>
                                <small>
                                  {stop.arrival} - {stop.departure} · 建议停留{" "}
                                  {stop.minutes} 分钟
                                </small>
                              </button>
                              {!preview && !planning && (
                                <div className="stop-actions">
                                  <button
                                    title="上移"
                                    aria-label={`上移${stop.place.name}`}
                                    disabled={index === 0}
                                    onClick={() =>
                                      onMoveStop(dayIndex, stop.id, -1)
                                    }
                                  >
                                    <IconArrowUp size={15} />
                                  </button>
                                  <button
                                    title="下移"
                                    aria-label={`下移${stop.place.name}`}
                                    disabled={index === day.stops.length - 1}
                                    onClick={() =>
                                      onMoveStop(dayIndex, stop.id, 1)
                                    }
                                  >
                                    <IconArrowDown size={15} />
                                  </button>
                                  <button
                                    title="移除"
                                    aria-label={`移除${stop.place.name}`}
                                    onClick={() =>
                                      onRemoveStop(dayIndex, stop.id)
                                    }
                                  >
                                    <IconX size={15} />
                                  </button>
                                </div>
                              )}
                            </div>
                            {stop.warning && (
                              <div className="inline-warning">
                                <IconAlertCircle size={14} />
                                {stop.warning}
                              </div>
                            )}
                            {day.legs[index] && (
                              <div className="leg-row">
                                <span className="leg-line" />
                                <span>
                                  {day.legs[index].mode} ·{" "}
                                  {day.legs[index].minutesMin != null
                                    ? `预计 ${day.legs[index].minutesMin}–${day.legs[index].minutesMax} 分钟`
                                    : day.legs[index].minutes === null
                                    ? "耗时待核实"
                                    : `预计 ${day.legs[index].minutes} 分钟`}{" "}
                                  · {day.legs[index].source}
                                </span>
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                      {day.meals.map((meal) => (
                        <div key={meal} className="meal-note">
                          用餐 / 休息 · {meal}
                        </div>
                      ))}
                      <div className="budget-note">
                        预算提示：{day.budgetNote}
                      </div>
                      {day.warnings.map((warning) => (
                        <div className="day-warning" key={warning}>
                          <IconAlertCircle size={16} />
                          {warning}
                        </div>
                      ))}
                    </div>
                  );
                })}
              </section>
            ) : (
              <div className="empty-itinerary">
                <IconRoute size={30} stroke={1.3} />
                <h3>路线还没开始</h3>
                <p>先把地点加入「待安排」，再生成两套可比较的路线。</p>
                <button
                  className="button secondary"
                  onClick={() => setSubtab("pending")}
                >
                  查看待安排 <IconArrowRight size={17} />
                </button>
              </div>
            )}
          </>
        )}
        {subtab === "pending" && (
          <section className="pending-section">
            <div className="section-inline-title">
              <IconBookmark size={17} />
              <h3>待安排地点</h3>
            </div>
            <p className="small-muted">
              从地图加入的地点先放这里，规划时会优先考虑。
            </p>
            {trip.pendingPlaces.length === 0 ? (
              <div className="compact-empty">
                还没有地点。试试地图搜索或点击推荐地点。
              </div>
            ) : (
              <div className="pending-list">
                {trip.pendingPlaces.map((place) => (
                  <div className="pending-item" key={place.placeId}>
                    <button onClick={() => onSelectPlace(place)}>
                      <span className="pending-icon">
                        <IconMapPin size={18} />
                      </span>
                      <span>
                        <strong>{place.name}</strong>
                        <small>
                          {place.category} · {place.address}
                        </small>
                      </span>
                    </button>
                    <div>
                      <button
                        title="加入某一天"
                        aria-label={`安排${place.name}`}
                        disabled={planning || !trip.itinerary.length}
                        onClick={() =>
                          setAddDay(
                            addDay === place.placeId ? null : place.placeId,
                          )
                        }
                      >
                        <IconPlus size={17} />
                      </button>
                      <button
                        title="移除待安排"
                        aria-label={`移除${place.name}`}
                        disabled={planning}
                        onClick={() => onRemovePending(place)}
                      >
                        <IconTrash size={17} />
                      </button>
                    </div>
                    {addDay === place.placeId && (
                      <div className="add-day-picker">
                        加入：
                        {trip.itinerary.map((day, index) => (
                          <button
                            key={day.id}
                            onClick={() => {
                              onAddStop(place, index);
                              setAddDay(null);
                            }}
                          >
                            第 {index + 1} 天
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
            <div className="saved-count">
              已收藏 {trip.savedPlaces.length} 个地点
            </div>
            {trip.savedPlaces.length > 0 && (
              <div className="saved-list">
                {trip.savedPlaces.map((place) => (
                  <button
                    key={place.placeId}
                    onClick={() => onSelectPlace(place)}
                  >
                    <IconBookmark size={15} />
                    {place.name}
                  </button>
                ))}
              </div>
            )}
          </section>
        )}
        {subtab === "history" && (
          <section className="history-section">
            <div className="section-inline-title">
              <IconHistory size={17} />
              <h3>历史版本</h3>
            </div>
            <p className="small-muted">
              恢复前会自动保存当前版本，revision 会继续增加。
            </p>
            {trip.history.length === 0 ? (
              <div className="compact-empty">尚无历史版本。</div>
            ) : (
              [...trip.history].reverse().map((version) => (
                <div className="history-row" key={version.id}>
                  <div>
                    <strong>{version.summary}</strong>
                    <small>
                      {new Date(version.at).toLocaleString("zh-CN")} ·{" "}
                      {version.source === "agent" ? "AI" : "用户"}
                    </small>
                  </div>
                  <button
                    className="text-button"
                    disabled={planning}
                    onClick={() => onRestore(version.id)}
                  >
                    恢复
                  </button>
                </div>
              ))
            )}
          </section>
        )}
      </div>
      <div className="itinerary-footer">
        <div className="planning-actions">
          <button
            className="button primary full"
            disabled={
              planning ||
              !(
                trip.pendingPlaces.length ||
                trip.savedPlaces.length ||
                trip.itinerary.length
              )
            }
            onClick={onGenerate}
          >
            <IconSparkles size={18} />
            {candidates.length ? "重新生成方案" : "生成旅行方案"}
          </button>
          {trip.itinerary.length > 0 && (
            <button
              className="button secondary full"
              disabled={planning}
              onClick={onOptimize}
            >
              <IconRoute size={17} />
              优化当前行程
            </button>
          )}
        </div>
        <span>规划以当前旅行快照为准 · 采用前可预览</span>
      </div>
      {exportOpen && (
        <div className="modal-backdrop">
          <div
            className="export-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="export-title"
          >
            <div className="modal-head">
              <h3 id="export-title">导出行程</h3>
              <button
                className="icon-button"
                aria-label="关闭导出"
                onClick={() => setExportOpen(false)}
              >
                <IconX size={20} />
              </button>
            </div>
            <p>选择要包含的日期。导出需要高德静态地图服务。</p>
            <div className="export-days">
              {trip.itinerary.map((day, index) => (
                <label key={day.id}>
                  <input
                    type="checkbox"
                    checked={exportDays.includes(index)}
                    onChange={(event) =>
                      setExportDays((current) =>
                        event.target.checked
                          ? [...current, index].sort()
                          : current.filter((item) => item !== index),
                      )
                    }
                  />
                  <span>{day.date}</span>
                  <IconCheck size={16} />
                </label>
              ))}
            </div>
            <div className="export-actions">
              <button
                className="button secondary"
                disabled={!exportDays.length}
                onClick={() => {
                  onExport("md", exportDays);
                  setExportOpen(false);
                }}
              >
                Markdown
              </button>
              <button
                className="button primary"
                disabled={!exportDays.length}
                onClick={() => {
                  onExport("pdf", exportDays);
                  setExportOpen(false);
                }}
              >
                打印 / 保存 PDF
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
