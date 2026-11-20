import { useState } from "react";
import { api, type ServiceStatus } from "../lib/api";

type Props = {
  initialService: "amap" | "ai";
  requireAmapWebService: boolean;
  status: ServiceStatus | null;
  onClose: () => void;
  onSaved: (status: ServiceStatus) => void;
};

export function ServiceSettings({ initialService, requireAmapWebService, status, onClose, onSaved }: Props) {
  const [service, setService] = useState(initialService);
  const [provider, setProvider] = useState<"deepseek" | "openai">(status?.ai.provider || "deepseek");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(status?.ai.models?.[status.ai.provider] || status?.ai.model || (status?.ai.provider === "openai" ? "gpt-4.1-mini" : "deepseek-flash"));
  const [jsKey, setJsKey] = useState("");
  const [securityCode, setSecurityCode] = useState("");
  const [webServiceKey, setWebServiceKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const save = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const next = service === "amap"
        ? await api.saveAmapConfig({ jsKey, securityCode, webServiceKey })
        : await api.saveAIConfig({ provider, apiKey, model });
      onSaved(next); setApiKey(""); setSecurityCode(""); setWebServiceKey(""); setJsKey("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "配置验证失败，请重试");
    } finally { setBusy(false); }
  };
  const remove = async (name: "amap" | "ai") => {
    if (!window.confirm("确定移除" + (name === "amap" ? "高德地图" : "AI 服务") + "配置吗？现有环境变量不会被删除。")) return;
    setBusy(true); setError("");
    try { onSaved(await api.removeServiceConfig(name)); setService(name); }
    catch (e) { setError(e instanceof Error ? e.message : "移除失败，请重试"); }
    finally { setBusy(false); }
  };

  return <div className="service-settings-backdrop" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
    <section className="service-settings" role="dialog" aria-modal="true" aria-labelledby="service-settings-title">
      <header><div><span>本机服务</span><h2 id="service-settings-title">{service === "amap" ? "连接高德地图" : "连接 AI 服务"}</h2></div><button type="button" onClick={onClose} aria-label="关闭" disabled={busy}>×</button></header>
      <div className="service-settings-tabs">
        <button type="button" className={service === "amap" ? "active" : ""} onClick={() => { setService("amap"); setError(""); }}>高德地图 · {status?.amap.configured ? "已配置" : "未配置"}</button>
        <button type="button" className={service === "ai" ? "active" : ""} onClick={() => { setService("ai"); setError(""); }}>AI 服务 · {status?.ai.configured ? "已配置" : "未配置"}</button>
      </div>
      <form onSubmit={save}>
        {service === "amap" ? <>
          <label>高德 JS API Key<input required={!status?.amap.configured} autoComplete="off" value={jsKey} onChange={(e) => setJsKey(e.target.value)} placeholder={status?.amap.configured ? "已配置；留空保留现有 Key" : "浏览器地图运行所需"} /></label>
          <label>高德 JS API 安全密钥<input required={!status?.amap.configured} type="password" autoComplete="new-password" value={securityCode} onChange={(e) => setSecurityCode(e.target.value)} placeholder={status?.amap.configured ? "已配置；留空保留现有密钥" : "后端代理使用，不发送给浏览器"} /></label>
          <label>高德 Web Service Key <span className="service-optional">{requireAmapWebService && !status?.amap.webServiceConfigured ? "当前操作需要 · POI 搜索、路线、地理编码" : "可选 · POI 搜索、路线、地理编码"}</span><input required={requireAmapWebService && !status?.amap.webServiceConfigured} type="password" autoComplete="new-password" value={webServiceKey} onChange={(e) => setWebServiceKey(e.target.value)} placeholder={status?.amap.webServiceConfigured ? "已配置；输入新 Key 以更换" : "当前操作需要时填写，否则可稍后配置"} /></label>
          <p className="service-settings-note">JS Key 会提供给浏览器加载地图，无法完全保密。请设置高德控制台域名白名单。</p>
        </> : <>
          <label>供应商<select value={provider} onChange={(e) => { const next = e.target.value as "deepseek" | "openai"; setProvider(next); setModel(status?.ai.models?.[next] || (next === "deepseek" ? "deepseek-flash" : "gpt-4.1-mini")); }}><option value="deepseek">DeepSeek</option><option value="openai">OpenAI</option></select></label>
          <label>API Key<div className="service-password"><input required={!status?.ai.availableProviders?.[provider]} type={showKey ? "text" : "password"} autoComplete="new-password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={status?.ai.availableProviders?.[provider] ? "留空以复用本机已有配置" : "仅用于后端请求，不会返回到浏览器"} /><button type="button" onClick={() => setShowKey((v) => !v)}>{showKey ? "隐藏" : "显示"}</button></div></label>
          <label>模型 ID<input required value={model} onChange={(e) => setModel(e.target.value)} /></label>
          <p className="service-settings-note">仅允许连接所选供应商的官方 API 地址，不接受自定义 Base URL。</p>
        </>}
        {error && <p className="service-settings-error" role="alert">{error}</p>}
        <footer>
          {((service === "amap" && status?.amap.configured) || (service === "ai" && status?.ai.configured)) && <button type="button" className="service-remove" disabled={busy} onClick={() => void remove(service)}>移除配置</button>}
          <button type="button" className="service-cancel" disabled={busy} onClick={onClose}>取消</button>
          <button type="submit" className="service-save" disabled={busy}>{busy ? "正在验证…" : "验证并保存"}</button>
        </footer>
      </form>
    </section>
  </div>;
}
