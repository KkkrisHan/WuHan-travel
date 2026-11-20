import { useState } from "react";
import {
  IconArrowLeft,
  IconArrowRight,
  IconCalendar,
  IconCheck,
  IconCoinYuan,
  IconMapPin,
  IconUsers,
} from "@tabler/icons-react";
import { defaultPreferences } from "../lib/db";
import { HotelPicker } from "./HotelPicker";
import type { Preferences } from "../types";

type Props = {
  amapJsKey: string;
  amapConfigRevision: string;
  webServiceConfigured: boolean;
  onBack: () => void;
  onFinish: (preferences: Preferences) => void;
  onRequireAmapWebService: (action: () => void) => void;
};
const interests = [
  "历史人文",
  "自然风光",
  "城市漫步",
  "博物馆",
  "美食",
  "亲子",
];
const steps = [
  {
    title: "什么时候出发",
    note: "确定旅行日期或先按天数规划",
    icon: IconCalendar,
  },
  { title: "和谁一起出发", note: "让路线适合同行的人与预算", icon: IconUsers },
  {
    title: "喜欢怎样游玩",
    note: "节奏、交通和兴趣都可以调整",
    icon: IconMapPin,
  },
  {
    title: "住在哪里",
    note: "地图与文字搜索选定酒店或每日出发位置（可跳过）",
    icon: IconMapPin,
  },
  {
    title: "还有特别安排吗",
    note: "写下必去地点、固定活动和其他要求",
    icon: IconCoinYuan,
  },
];

export function Wizard({ amapJsKey, amapConfigRevision, webServiceConfigured, onBack, onFinish, onRequireAmapWebService }: Props) {
  const [step, setStep] = useState(0);
  const [value, setValue] = useState<Preferences>({ ...defaultPreferences });
  const companionChoices = value.company.split("、").filter(Boolean);
  const solo = companionChoices.includes("独自出行");
  const toggleCompany = (option: string) => {
    if (option === "独自出行") {
      const next = solo ? "" : "独自出行";
      setValue((current) => ({ ...current, company: next, people: 1 }));
      return;
    }
    if (solo) return;
    const next = companionChoices.includes(option)
      ? companionChoices.filter((item) => item !== option)
      : [...companionChoices, option];
    set("company", next.join("、"));
  };
  const set = <K extends keyof Preferences>(key: K, next: Preferences[K]) =>
    setValue((current) => ({ ...current, [key]: next }));
  const toggleInterest = (interest: string) =>
    set(
      "interests",
      value.interests.includes(interest)
        ? value.interests.filter((item) => item !== interest)
        : [...value.interests, interest],
    );
  const CurrentIcon = steps[step].icon;
  return (
    <div className="wizard-page">
      <header className="global-header">
        <button className="brand brand-button" onClick={onBack}>
          <span className="workspace-author">Made By Kris Han</span>
        </button>
        <span className="wizard-count">
          旅行设置 / {String(step + 1).padStart(2, "0")} OF {String(steps.length).padStart(2, "0")}
        </span>
      </header>
      <main className="wizard-main">
        <aside className="wizard-aside">
          <span className="eyebrow">为你量身安排</span>
          <h1>
            一段好旅程，
            <br />
            从你的想法开始。
          </h1>
          <div className="wizard-track">
            {steps.map((item, index) => (
              <div
                key={item.title}
                className={`wizard-step ${index === step ? "active" : ""} ${index < step ? "done" : ""}`}
              >
                <span className="step-number">
                  {index < step ? (
                    <IconCheck size={15} />
                  ) : (
                    String(index + 1).padStart(2, "0")
                  )}
                </span>
                <span>{item.title}</span>
              </div>
            ))}
          </div>
          <p className="aside-note">
            这些信息只保存在此浏览器。你可以稍后继续修改。
          </p>
        </aside>
        <section className="wizard-form">
          <div className="form-icon">
            <CurrentIcon size={25} stroke={1.4} />
          </div>
          <p className="section-kicker">第 {step + 1} 步，共 {steps.length} 步</p>
          <h2>{steps[step].title}</h2>
          <p className="form-note">{steps[step].note}</p>
          {step === 0 && (
            <div className="field-stack">
              <label>
                出发日期 <span>可稍后决定</span>
                <input
                  type="date"
                  value={value.startDate || ""}
                  onChange={(event) =>
                    set("startDate", event.target.value || null)
                  }
                />
              </label>
              <label>
                旅行天数 <span>1 至 7 天</span>
                <div className="stepper">
                  <button
                    onClick={() => set("days", Math.max(1, value.days - 1))}
                    aria-label="减少天数"
                  >
                    −
                  </button>
                  <strong>{value.days} 天</strong>
                  <button
                    onClick={() => set("days", Math.min(7, value.days + 1))}
                    aria-label="增加天数"
                  >
                    ＋
                  </button>
                </div>
              </label>
            </div>
          )}
          {step === 1 && (
            <div className="field-stack">
              <label>
                同行人数
                <div className="stepper">
                  <button
                    onClick={() => set("people", Math.max(1, value.people - 1))}
                    disabled={solo}
                    aria-label="减少人数"
                  >
                    −
                  </button>
                  <strong>{value.people} 人</strong>
                  <button
                    onClick={() =>
                      set("people", Math.min(20, value.people + 1))
                    }
                    disabled={solo}
                    aria-label="增加人数"
                  >
                    ＋
                  </button>
                </div>
              </label>
              <div className="field-label">同行类型 <span>可多选；独自出行与其他类型互斥</span></div>
              <div className="choice-grid">
                {["独自出行", "情侣", "朋友", "亲子", "长辈"].map((option) => (
                  <button
                    className={`choice ${companionChoices.includes(option) ? "selected" : ""}`}
                    key={option}
                    disabled={solo && option !== "独自出行"}
                    aria-pressed={companionChoices.includes(option)}
                    onClick={() => toggleCompany(option)}
                  >
                    {option}
                  </button>
                ))}
              </div>
              <div className="field-label">预算倾向</div>
              <div className="choice-grid">
                {["经济", "适中", "宽松", "灵活"].map((option) => (
                  <button
                    className={`choice ${value.budget === option ? "selected" : ""}`}
                    key={option}
                    onClick={() => set("budget", option)}
                  >
                    {option}
                  </button>
                ))}
              </div>
            </div>
          )}
          {step === 2 && (
            <div className="field-stack">
              <div className="field-label">游玩节奏</div>
              <div className="choice-grid">
                {["舒缓", "适中", "紧凑"].map((option) => (
                  <button
                    className={`choice ${value.pace === option ? "selected" : ""}`}
                    key={option}
                    onClick={() => set("pace", option)}
                  >
                    {option}
                  </button>
                ))}
              </div>
              <div className="field-label">主要交通</div>
              <div className="choice-grid">
                {["步行与公共交通", "打车 / 驾车"].map((option) => (
                  <button
                    className={`choice ${value.transport === option ? "selected" : ""}`}
                    key={option}
                    onClick={() => set("transport", option)}
                  >
                    {option}
                  </button>
                ))}
              </div>
              <div className="field-label">
                感兴趣的内容 <span>可多选</span>
              </div>
              <div className="choice-grid">
                {interests.map((option) => (
                  <button
                    className={`choice ${value.interests.includes(option) ? "selected" : ""}`}
                    key={option}
                    onClick={() => toggleInterest(option)}
                  >
                    {option}
                  </button>
                ))}
              </div>
            </div>
          )}
          {step === 3 && (
            <div className="field-stack hotel-field-stack">
              <HotelPicker
                amapJsKey={amapJsKey}
                amapConfigRevision={amapConfigRevision}
                webServiceConfigured={webServiceConfigured}
                value={value.lodging || ""}
                location={value.lodgingLocation || null}
                onChange={(lodging, lodgingLocation) =>
                  setValue((current) => ({ ...current, lodging, lodgingLocation }))
                }
                onRequireAmapWebService={onRequireAmapWebService}
              />
            </div>
          )}
          {step === 4 && (
            <div className="field-stack">
              <label>
                特殊信息 / 个性化要求 <span>选填</span>
                <textarea
                  rows={7}
                  value={value.requirements}
                  onChange={(event) => set("requirements", event.target.value)}
                  placeholder="例如：一定要去黄鹤楼；周六下午有固定活动；希望少走路……"
                  maxLength={1000}
                />
              </label>
              <div className="form-hint">
                进入旅行后，AI 会自动识别明确要求的必去景点并加入列表；夜间等时间偏好也会用于行程规划。实际开放时间需要核实。
              </div>
            </div>
          )}
          <div className="wizard-nav">
            <button
              className="button ghost"
              onClick={() => (step ? setStep(step - 1) : onBack())}
            >
              <IconArrowLeft size={18} />
              上一步
            </button>
            <button
              className="button primary"
              onClick={() => (step === steps.length - 1 ? onFinish(value) : setStep(step + 1))}
            >
              {step === steps.length - 1 ? "进入地图" : "下一步"}
              <IconArrowRight size={18} />
            </button>
          </div>
        </section>
      </main>
    </div>
  );
}
