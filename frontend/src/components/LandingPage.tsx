import { useEffect, useRef, useState } from "react";
import { IconArrowDown, IconArrowUpRight, IconMessageCircle } from "@tabler/icons-react";
import "../landing.css";
import type { Trip } from "../types";
import type { ServiceStatus } from "../lib/api";
import content from "../data/saigKnowledge.json";

type Props = {
  currentTrip?: Trip;
  onOpen: (trip: Trip) => void;
  onPlan: () => void;
  onAsk: (question: string) => void;
  onOpenAssistant: () => void;
  onOpenServices: () => void;
  services: ServiceStatus | null;
};

const navItems = [
  ["wuhan", "武汉"],
  ["spu", "SPU 社区"],
  ["saig", "SAIG 大赛"],
  ["travel", "开始旅行"],
] as const;

export function LandingPage({ onAsk, currentTrip, onOpen, onPlan, onOpenAssistant, onOpenServices, services }: Props) {
  const [active, setActive] = useState("wuhan");
  const pendingNavigation = useRef<string | null>(null);
  const scrollIdleTimer = useRef<number | null>(null);
  useEffect(() => {
    const sections = navItems.map(([id]) => document.getElementById(id)).filter((item): item is HTMLElement => !!item);
    const updateActive = () => {
      const last = sections.at(-1);
      const atPageBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
      if (atPageBottom && last) {
        setActive(last.id);
        return;
      }
      const marker = window.innerHeight * 0.5;
      const current = sections.filter((section) => section.getBoundingClientRect().top <= marker).at(-1);
      setActive(current?.id || sections[0]?.id || "wuhan");
    };
    const finishNavigation = () => {
      if (scrollIdleTimer.current !== null) window.clearTimeout(scrollIdleTimer.current);
      scrollIdleTimer.current = null;
      if (pendingNavigation.current) {
        pendingNavigation.current = null;
        updateActive();
      }
    };
    const onScroll = () => {
      if (!pendingNavigation.current) {
        updateActive();
        return;
      }
      setActive(pendingNavigation.current);
      if (scrollIdleTimer.current !== null) window.clearTimeout(scrollIdleTimer.current);
      scrollIdleTimer.current = window.setTimeout(finishNavigation, 180);
    };
    const onResize = () => {
      finishNavigation();
      updateActive();
    };
    updateActive();
    window.addEventListener("scroll", onScroll, { passive: true });
    document.addEventListener("scrollend", finishNavigation);
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("scroll", onScroll);
      document.removeEventListener("scrollend", finishNavigation);
      window.removeEventListener("resize", onResize);
      if (scrollIdleTimer.current !== null) window.clearTimeout(scrollIdleTimer.current);
    };
  }, []);
  const go = (id: string) => {
    setActive(id);
    const target = document.getElementById(id);
    if (target && active !== id) pendingNavigation.current = id;
    target?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="landing-page">
      <nav className="landing-nav" aria-label="页面导航">
        <a className="landing-wordmark" href="#wuhan" onClick={(event) => { event.preventDefault(); go("wuhan"); }}>Made By Kris Han</a>
        <div className="landing-nav-links">{navItems.map(([id, label]) => <button key={id} className={active === id ? "active" : ""} onClick={() => go(id)} aria-current={active === id ? "location" : undefined}>{label}</button>)}</div>
        <button className="landing-assistant-entry" onClick={onOpenAssistant}><IconMessageCircle size={17}/> AI 接待员</button>
        <button className="landing-assistant-entry" onClick={onOpenServices} title={`高德地图：${services?.amap.configured ? "已配置" : "未配置"}；AI：${services?.ai.provider || "未配置"} · ${services?.ai.model || "未配置"} · ${services?.ai.configured ? "已配置" : "未配置"}`}>服务连接 · 地图{services?.amap.configured ? "已配置" : "未配置"} · AI{services?.ai.configured ? "已配置" : "未配置"}</button>
      </nav>

      <section className="landing-hero" id="wuhan">
        <div className="landing-hero-image" aria-hidden="true" />
        <div className="landing-hero-grain" aria-hidden="true" />
        <div className="landing-hero-content">
          <p className="landing-eyebrow"><span /> WUHAN · CITY / PEOPLE / AI</p>
          <h1>{content.wuhan.headline}</h1>
          <p className="landing-hero-subtitle">武汉数命 SPU 城市 AI 接待员 · SAIG 线上赛参赛作品</p>
          <p className="landing-hero-copy">{content.wuhan.summary}</p>
          <button className="landing-primary" onClick={() => go("travel")}>探索武汉 <IconArrowUpRight size={18}/></button>
        </div>
        <div className="landing-coordinate">30°35′N&nbsp; 114°18′E <span>长江 · 汉江 · 武汉</span></div>
        <div className="landing-hero-caption">长江与武汉长江大桥<br/><a href="https://commons.wikimedia.org/wiki/File:Wuhan_Yangtze_River_Bridge_in_2020.jpg" target="_blank" rel="noreferrer">ZhengZhou · CC BY-SA 4.0 · Wikimedia Commons</a></div>
      </section>

      <section className="landing-spu" id="spu">
        <div className="landing-section-index">01 / THE IDEA</div>
        <div className="landing-spu-layout">
          <div className="landing-spu-copy">
            <p className="landing-eyebrow">数命 SPU · 一个人与 AI</p>
            <h2>不是一个人<br/><em>单打独斗</em></h2>
            <p className="landing-body">SPU 是 <strong>{content.spu.fullName}</strong>，意为「超级人类单元」。它描述一种生产方式：一个人结合 AI，形成能够独立完成任务与交付的单元。</p>
            <p className="landing-body">数命科技推动这一理念，并建设数命 SPU 社区。社区把 AI 学习放进真实问题：理解需求、动手实践、交付成果，再通过展示和使用反馈继续改进。</p>
            <button className="landing-text-link" onClick={() => onAsk("请介绍 SPU 的英文全称、核心理念，以及数命 SPU 社区如何把学习、真实订单和成果交付联系起来。")}>向 AI 了解 SPU <IconArrowUpRight size={17}/></button>
          </div>
          <div className="landing-formula" aria-label="一个人加 AI 等于 SPU">
            <div className="formula-topline">A NEW UNIT OF PRODUCTION <span>01—04</span></div>
            <div className="formula-main"><span>1</span><small>PERSON</small><b>+</b><span className="formula-ai">AI</span><b>=</b><span>SPU</span></div>
            <p>一个人 × AI<br/>把能力带到真实场景</p>
            <div className="formula-orbit orbit-a"/><div className="formula-orbit orbit-b"/><span className="formula-dot"/>
          </div>
        </div>
        <div className="landing-process">
          <p>从学习走向交付</p>
          <div>{content.spu.stages.map((stage, index) => <div className="process-step" key={stage}><span>0{index + 1}</span><strong>{stage}</strong>{index < content.spu.stages.length - 1 && <i/>}</div>)}</div>
        </div>
      </section>

      <section className="landing-saig" id="saig">
        <div className="saig-topline"><span>02 / THE GATHERING</span><a href={content.sources.saig} target="_blank" rel="noreferrer">官方赛事网站 ↗</a></div>
        <div className="saig-title-row"><div><p className="landing-eyebrow">SAIG · 数命 SPU 国际 AI 大赛</p><h2>AI For<br/><em>Everyone</em></h2></div><p className="saig-intro">不是在虚构题目里比技术。<br/>108 位选手，带着 AI 走进真实需求，<br/>让百年街区成为城市的体验现场。</p></div>
        <div className="saig-stats"><div><strong>108</strong><span>位海内外选手</span></div><div><strong>16<span>h</span></strong><span>现场比赛 · 两天</span></div><div><strong>5</strong><span>处百年地标展区</span></div><div><strong>$1M</strong><span>现金 + AI 模型额度</span></div></div>
        <div className="saig-meta"><span>2026.12.18—20 <i>·</i> 中国武汉 · 江岸历史街区</span><span>主赛场 平和打包厂 <i>·</i> 社区主场 智民里</span></div>
        <div className="saig-flow"><div className="saig-flow-heading"><p>从作品，到真实市场</p><span>比赛与活动的时间不同</span></div><div className="saig-timeline">{content.saig.timeline.map((step, i) => <article key={step.name}><span className="timeline-index">0{i + 1}</span><small>{step.date}</small><h3>{step.name}</h3><p>{step.description}</p></article>)}</div></div>
        <div className="saig-detail-grid"><div className="saig-challenge"><span>THE CHALLENGE</span><h3>真实订单<br/>就是赛题</h3><p>{content.saig.challenge}</p><p className="saig-prize"><strong>100 万美元</strong><br/>50 万美元现金 + 50 万美元 AI 模型调用额度</p></div><div className="saig-themes"><span>开放给每一个人 · 六种体验</span><div>{content.saig.themes.map((theme, i) => <article key={theme.name}><b>0{i + 1}</b><div><strong>{theme.name}</strong><p>{theme.experience}</p></div></article>)}</div></div></div>
        <div className="saig-access"><div><span>JOIN IN</span><h3>找到你的参与方式</h3></div><p>官网当前显示报名仍开放；参赛截止日期尚未公布。公众可按后续安排预约参观，也可提交真实需求、摆摊、参展或做志愿者。票价和开售日期尚未公布，观赛方式以官网公告为准。</p><button onClick={() => onAsk("SAIG 目前有哪些参与方式？非 AI 从业者能不能参赛？请说明线上赛提交要求，以及官网尚未公布哪些事项。")}>问 AI 如何参与 <IconArrowUpRight size={17}/></button></div>
        <div className="saig-faq"><p>SAIG 常见问题</p><div>{content.faq.map((item) => <button key={item.question} onClick={() => onAsk(item.prompt)}>{item.question}<IconArrowUpRight size={15}/></button>)}</div></div>
      </section>

      <section className="landing-travel" id="travel">
        <div className="landing-travel-heading"><p className="landing-eyebrow">03 / AI 城市接待</p><h2>现在，让 AI 带你<br/><em>认识武汉</em></h2><div className="landing-travel-actions"><button className="landing-create-trip" onClick={onPlan}>创建一段旅行 <IconArrowDown size={16}/></button>{currentTrip && <button className="landing-current-trip" onClick={() => onOpen(currentTrip)}>继续当前旅行：{currentTrip.name} <IconArrowUpRight size={16}/></button>}</div><span className="landing-entry-note">旅行资料保存在此浏览器 · {currentTrip ? "当前旅行可继续编辑" : "创建后可随时返回"}</span></div>
        <div className="landing-travel-visual"><span>武汉数命 SPU 城市 AI 接待员</span><strong>从城市知识<br/>到一段真实行程</strong><div><i/>武汉 · 长江与汉江交汇</div></div>
      </section>
    </div>
  );
}
