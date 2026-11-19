import { useState } from "react";
import {
  IconArrowUpRight,
  IconCompass,
  IconCopy,
  IconMap2,
  IconPencil,
  IconPlus,
  IconSearch,
  IconTrash,
} from "@tabler/icons-react";
import type { Trip } from "../types";

type Props = {
  trips: Trip[];
  onOpen: (trip: Trip) => void;
  onExplore: () => void;
  onPlan: () => void;
  onRename: (trip: Trip, name: string) => void;
  onCopy: (trip: Trip) => void;
  onDelete: (trip: Trip) => void;
};

export function Dashboard({
  trips,
  onOpen,
  onExplore,
  onPlan,
  onRename,
  onCopy,
  onDelete,
}: Props) {
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState("");
  const sorted = [...trips]
    .filter((trip) => trip.name.includes(query))
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  const latest = [...trips].sort((a, b) =>
    b.updatedAt.localeCompare(a.updatedAt),
  )[0];
  return (
    <div className="dashboard">
      <header className="global-header dashboard-header">
        <div className="brand">
          <span className="brand-mark">
            <IconCompass size={21} stroke={1.8} />
          </span>
          <span>
            漫游武汉<span className="brand-en"> WUHAN CONCIERGE</span>
          </span>
        </div>
        <div className="header-right">
          <span className="status-dot" /> 本地旅行工作台
        </div>
      </header>
      <main className="dashboard-main">
        <section className="dashboard-hero">
          <div className="hero-copy">
            <div className="eyebrow">WUHAN · TRAVEL STUDIO</div>
            <h1>
              把武汉，
              <br />
              <span>走成你的路线。</span>
            </h1>
            <p>从一个地点开始探索，把收藏、对话和行程放在同一张地图上。</p>
            <div className="hero-actions">
              <button className="button primary large" onClick={onPlan}>
                <IconPlus size={19} />
                规划旅行
              </button>
              <button className="button secondary large" onClick={onExplore}>
                <IconCompass size={19} />
                探索武汉
              </button>
            </div>
            {latest && (
              <button className="continue-link" onClick={() => onOpen(latest)}>
                继续上次旅行：{latest.name}
                <IconArrowUpRight size={17} />
              </button>
            )}
          </div>
          <div className="hero-art" aria-hidden="true">
            <div className="hero-grid" />
            <div className="hero-river" />
            <div className="hero-route route-one" />
            <div className="hero-route route-two" />
            <div className="art-pin pin-a">01</div>
            <div className="art-pin pin-b">02</div>
            <div className="art-pin pin-c">03</div>
            <div className="art-caption">
              30.5928° N &nbsp; 114.3055° E<br />
              <strong>从这里，发现武汉</strong>
            </div>
          </div>
        </section>
        <section className="trips-section">
          <div className="section-heading">
            <div>
              <p className="section-kicker">你的旅程</p>
              <h2>
                旅行计划 <span>{trips.length.toString().padStart(2, "0")}</span>
              </h2>
            </div>
            <label className="search-field trip-search">
              <IconSearch size={18} />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索旅行计划"
                aria-label="搜索旅行计划"
              />
            </label>
          </div>
          {sorted.length === 0 ? (
            <div className="empty-trips">
              <IconMap2 size={29} stroke={1.3} />
              <h3>{query ? "没有找到匹配的旅行" : "还没有旅行计划"}</h3>
              <p>
                {query
                  ? "试试其他关键词。"
                  : "创建一次旅行，或者先在地图上探索武汉。"}
              </p>
              {!query && (
                <button className="text-button" onClick={onPlan}>
                  开始规划 <IconArrowUpRight size={17} />
                </button>
              )}
            </div>
          ) : (
            <div className="trip-list">
              {sorted.map((trip, index) => (
                <article className="trip-row" key={trip.id}>
                  <button className="trip-main" onClick={() => onOpen(trip)}>
                    <span className="trip-index">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <span className="trip-row-info">
                      <strong>{trip.name}</strong>
                      <small>
                        {trip.itinerary.length
                          ? `${trip.itinerary.length} 天行程`
                          : "探索中"}{" "}
                        · {trip.pendingPlaces.length} 个待安排地点 ·{" "}
                        {new Date(trip.updatedAt).toLocaleDateString("zh-CN")}{" "}
                        更新
                      </small>
                    </span>
                    <IconArrowUpRight size={20} />
                  </button>
                  <div className="trip-actions">
                    <button
                      title="重命名"
                      aria-label={`重命名${trip.name}`}
                      onClick={() => {
                        setEditing(trip.id);
                        setName(trip.name);
                      }}
                    >
                      <IconPencil size={18} />
                    </button>
                    <button
                      title="复制"
                      aria-label={`复制${trip.name}`}
                      onClick={() => onCopy(trip)}
                    >
                      <IconCopy size={18} />
                    </button>
                    <button
                      title="删除"
                      aria-label={`删除${trip.name}`}
                      onClick={() => onDelete(trip)}
                    >
                      <IconTrash size={18} />
                    </button>
                  </div>
                  {editing === trip.id && (
                    <div className="rename-overlay">
                      <form
                        onSubmit={(event) => {
                          event.preventDefault();
                          if (name.trim()) onRename(trip, name.trim());
                          setEditing(null);
                        }}
                      >
                        <label htmlFor="rename-input">旅行名称</label>
                        <input
                          id="rename-input"
                          value={name}
                          onChange={(event) => setName(event.target.value)}
                          autoFocus
                          maxLength={40}
                        />
                        <div>
                          <button
                            type="button"
                            className="button ghost"
                            onClick={() => setEditing(null)}
                          >
                            取消
                          </button>
                          <button className="button primary">保存</button>
                        </div>
                      </form>
                    </div>
                  )}
                </article>
              ))}
            </div>
          )}
        </section>
      </main>
      <footer className="dashboard-footer">
        <span>漫游武汉</span>
        <span>旅行数据保存在此浏览器</span>
      </footer>
    </div>
  );
}
