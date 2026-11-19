import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { IconArrowUp, IconRefresh, IconSparkles } from "@tabler/icons-react";
import type { Message, Place } from "../types";

type Props = {
  messages: Message[];
  selected: Place | null;
  modelConfigured: boolean;
  onSend: (message: string) => void;
  onRetry: (message: Message) => void;
  onIntro: (place: Place) => void;
};

export function AssistantPanel({
  messages,
  selected,
  modelConfigured,
  onSend,
  onRetry,
  onIntro,
}: Props) {
  const [input, setInput] = useState("");
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!input.trim()) return;
    onSend(input.trim());
    setInput("");
  };
  return (
    <div className="assistant-panel">
      <div className="panel-heading">
        <div>
          <div className="panel-kicker">
            <IconSparkles size={15} /> 旅行助手
          </div>
          <h2>和武汉聊聊</h2>
        </div>
        <span className="panel-status">
          {modelConfigured ? "已连接模型" : "基础模式"}
        </span>
      </div>
      <div className="message-list" role="log" aria-live="polite">
        {messages.length === 0 ? (
          <div className="assistant-empty">
            <div className="assistant-orb">
              <IconSparkles size={30} stroke={1.3} />
            </div>
            <h3>从你的好奇开始</h3>
            <p>搜一个地点、问一个问题，或者让我们一起想想这次武汉之行。</p>
            <div className="prompt-suggestions">
              <button onClick={() => onSend("第一次去武汉，适合从哪里开始？")}>
                第一次去武汉，去哪里？
              </button>
              <button onClick={() => onSend("帮我看看当前旅行计划的安排。")}>
                看看当前旅行计划
              </button>
            </div>
          </div>
        ) : (
          messages.map((message) => (
            <article className={`message ${message.role}`} key={message.id}>
              <div className="message-avatar">
                {message.role === "user" ? "我" : <IconSparkles size={17} />}
              </div>
              <div className="message-body">
                <div className="message-head">
                  <strong>
                    {message.role === "user"
                      ? "你"
                      : message.kind === "intro"
                        ? `${message.place?.name || "地点"} · AI 介绍`
                        : "旅行助手"}
                  </strong>
                  <time>
                    {new Date(message.createdAt).toLocaleTimeString("zh-CN", {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </time>
                </div>
                <div className="message-text markdown-content">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {message.text ||
                      (message.status === "running" ? "正在整理信息…" : "")}
                  </ReactMarkdown>
                </div>
                {message.status === "running" && (
                  <div className="message-progress">
                    <span className="pulse-dot" />
                    正在生成
                  </div>
                )}
                {message.status === "failed" && (
                  <div className="message-error">
                    {message.error || "生成失败"}
                    <button onClick={() => onRetry(message)}>
                      <IconRefresh size={15} />
                      重试
                    </button>
                  </div>
                )}
              </div>
            </article>
          ))
        )}
      </div>
      {selected && (
        <div className="selected-context">
          <IconSparkles size={16} />
          <span>
            当前地点：<strong>{selected.name}</strong>
          </span>
          <button onClick={() => onIntro(selected)}>生成介绍</button>
        </div>
      )}
      <form className="chat-composer" onSubmit={submit}>
        <label htmlFor="chat-input" className="sr-only">
          向旅行助手提问
        </label>
        <textarea
          id="chat-input"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="问问武汉，或描述你想要的旅行……"
          rows={3}
          maxLength={2000}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              if (input.trim()) {
                onSend(input.trim());
                setInput("");
              }
            }
          }}
        />
        <div className="composer-bottom">
          <span>Enter 发送 · Shift + Enter 换行</span>
          <button type="submit" disabled={!input.trim()} aria-label="发送消息">
            <IconArrowUp size={19} />
          </button>
        </div>
      </form>
    </div>
  );
}
