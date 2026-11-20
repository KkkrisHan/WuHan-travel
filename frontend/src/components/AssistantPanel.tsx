import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { IconArrowDown, IconArrowUp, IconExternalLink, IconMapPin, IconRefresh, IconSparkles } from "@tabler/icons-react";
import type { Message, Place } from "../types";

type Props = {
  messages: Message[];
  selected: Place | null;
  modelConfigured: boolean;
  onSend: (message: string) => void;
  onRetry: (message: Message) => void;
  onIntro: (place: Place) => void;
};

type TravelCardData = {
  title: string;
  description?: string;
  category?: string;
  tip?: string;
  url?: string;
};

function readTravelCard(raw: string): TravelCardData | null {
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const card = value as Record<string, unknown>;
    if (typeof card.title !== "string" || !card.title.trim() || card.title.length > 120) return null;
    for (const key of ["description", "category", "tip", "url"]) {
      if (card[key] !== undefined && (typeof card[key] !== "string" || card[key].length > 2000)) return null;
    }
    return card as TravelCardData;
  } catch {
    return null;
  }
}

function TravelCard({ card }: { card: TravelCardData }) {
  const link = card.url && /^https:\/\//i.test(card.url) ? card.url : null;
  return (
    <div className="assistant-travel-card">
      <div className="assistant-travel-card-kicker"><IconMapPin size={15}/>{card.category || "旅行推荐"}</div>
      <strong>{card.title}</strong>
      {card.description && <p>{card.description}</p>}
      {card.tip && <div className="assistant-travel-card-tip">{card.tip}</div>}
      {link && <a href={link} target="_blank" rel="noopener noreferrer">查看来源 <IconExternalLink size={13}/></a>}
    </div>
  );
}

export function AssistantPanel({ messages, selected, modelConfigured, onSend, onRetry, onIntro }: Props) {
  const [input, setInput] = useState("");
  const [showJump, setShowJump] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const lastUserMessageId = useRef("");

  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const latestUserId = [...messages].reverse().find((message) => message.role === "user")?.id || "";
    const newUserMessage = latestUserId !== lastUserMessageId.current;
    lastUserMessageId.current = latestUserId;
    if (!stickToBottom.current && !newUserMessage) return;
    const frame = window.requestAnimationFrame(() => {
      list.scrollTop = list.scrollHeight;
      stickToBottom.current = true;
      setShowJump(false);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [messages]);

  const updateScroll = () => {
    const list = listRef.current;
    if (!list) return;
    const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 110;
    stickToBottom.current = nearBottom;
    setShowJump(!nearBottom);
  };
  const jumpToBottom = () => {
    const list = listRef.current;
    if (!list) return;
    stickToBottom.current = true;
    list.scrollTo({ top: list.scrollHeight, behavior: "smooth" });
    setShowJump(false);
  };
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!input.trim()) return;
    stickToBottom.current = true;
    onSend(input.trim());
    setInput("");
  };

  return (
    <div className="assistant-panel">
      <div className="panel-heading">
        <div><div className="panel-kicker"><IconSparkles size={15}/> 城市 AI 接待员</div><h2>和武汉聊聊</h2></div>
        <span className="panel-status">{modelConfigured ? "已连接模型" : "基础模式"}</span>
      </div>
      <div className="message-scroller">
        <div className="message-list" ref={listRef} role="log" aria-live="polite" onScroll={updateScroll}>
          {messages.length === 0 ? (
            <div className="assistant-empty">
              <div className="assistant-orb"><IconSparkles size={30} stroke={1.3}/></div>
              <h3>从你的好奇开始</h3>
              <p>搜一个地点、问一个问题，或者让我们一起想想这次武汉之行。</p>
              <div className="prompt-suggestions">
                <button onClick={() => { stickToBottom.current = true; onSend("第一次去武汉，适合从哪里开始？"); }}>第一次去武汉，去哪里？</button>
                <button onClick={() => { stickToBottom.current = true; onSend("帮我看看当前旅行计划的安排。"); }}>看看当前旅行计划</button>
              </div>
            </div>
          ) : messages.map((message) => (
            <article className={`message ${message.role}`} key={message.id}>
              <div className="message-avatar">{message.role === "user" ? "我" : <IconSparkles size={17}/>}</div>
              <div className="message-body">
                <div className="message-head">
                  <strong>{message.role === "user" ? "你" : message.kind === "intro" ? `${message.place?.name || "地点"} · AI 介绍` : "旅行助手"}</strong>
                  <time>{new Date(message.createdAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</time>
                </div>
                <div className="message-text markdown-content">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      pre({ children }) {
                        return <div className="message-code-block">{children}</div>;
                      },
                      code({ className, children, ...rest }) {
                        if (className === "language-travel-card") {
                          const card = readTravelCard(String(children).trim());
                          if (card) return <TravelCard card={card}/>;
                        }
                        return <code className={className} {...rest}>{children}</code>;
                      },
                    }}
                  >{message.text || (message.status === "running" ? "正在整理信息…" : "")}</ReactMarkdown>
                </div>
                {message.status === "running" && <div className="message-progress"><span className="pulse-dot"/>正在回复…</div>}
                {message.status === "failed" && (
                  <div className="message-error">{message.error || "生成失败"}
                    <button onClick={() => onRetry(message)}><IconRefresh size={15}/>重试</button>
                  </div>
                )}
              </div>
            </article>
          ))}
        </div>
        {showJump && (
          <button className="message-jump-bottom" type="button" onClick={jumpToBottom} aria-label="回到最新消息" title="回到最新消息">
            <IconArrowDown size={19}/>
          </button>
        )}
      </div>
      {selected && <div className="selected-context"><IconSparkles size={16}/><span>当前地点：<strong>{selected.name}</strong></span><button onClick={() => onIntro(selected)}>生成介绍</button></div>}
      <form className="chat-composer" onSubmit={submit}>
        <label htmlFor="chat-input" className="sr-only">向旅行助手提问</label>
        <textarea
          id="chat-input" value={input} onChange={(event) => setInput(event.target.value)}
          placeholder="问问武汉，或描述你想要的旅行……" rows={3} maxLength={2000}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              if (input.trim()) {
                stickToBottom.current = true;
                onSend(input.trim());
                setInput("");
              }
            }
          }}
        />
        <div className="composer-bottom">
          <span>Enter 发送 · Shift + Enter 换行</span>
          <button type="submit" disabled={!input.trim()} aria-label="发送消息"><IconArrowUp size={19}/></button>
        </div>
      </form>
    </div>
  );
}
