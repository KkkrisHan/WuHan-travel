import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { IconExternalLink, IconMapPin } from "@tabler/icons-react";

type TravelCardData = { title: string; description?: string; category?: string; tip?: string; url?: string };

function safeUrl(value: string | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value, window.location.href);
    return ["http:", "https:"].includes(url.protocol) ? url.href : undefined;
  } catch { return undefined; }
}

function readTravelCard(raw: string): TravelCardData | null {
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const card = value as Record<string, unknown>;
    if (typeof card.title !== "string" || !card.title.trim() || card.title.length > 120) return null;
    for (const key of ["description", "category", "tip", "url"])
      if (card[key] !== undefined && (typeof card[key] !== "string" || card[key].length > 2000)) return null;
    return card as TravelCardData;
  } catch { return null; }
}

function TravelCard({ card }: { card: TravelCardData }) {
  const url = safeUrl(card.url);
  return <div className="assistant-travel-card">
    <div className="assistant-travel-card-kicker"><IconMapPin size={15}/>{card.category || "旅行推荐"}</div>
    <strong>{card.title}</strong>{card.description && <p>{card.description}</p>}
    {card.tip && <div className="assistant-travel-card-tip">{card.tip}</div>}
    {url && <a href={url} target="_blank" rel="noopener noreferrer">查看来源 <IconExternalLink size={13}/></a>}
  </div>;
}

export function MarkdownContent({ children, className = "" }: { children: string; className?: string }) {
  return <div className={`markdown-content ${className}`.trim()}>
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw, rehypeSanitize]} components={{
      pre({ children: code }) { return <div className="message-code-block">{code}</div>; },
      code({ className: codeClass, children: code, ...rest }) {
        if (codeClass === "language-travel-card") {
          const card = readTravelCard(String(code).trim());
          if (card) return <TravelCard card={card}/>;
        }
        return <code className={codeClass} {...rest}>{code}</code>;
      },
      a({ href, children: linkChildren, ...rest }) {
        const url = safeUrl(href);
        if (!url) return <span>{linkChildren}</span>;
        const external = new URL(url).origin !== window.location.origin;
        return <a {...rest} href={url} target={external ? "_blank" : undefined} rel={external ? "noopener noreferrer" : undefined}>{linkChildren}</a>;
      },
    }}>{children}</ReactMarkdown>
  </div>;
}
