import type { MessageSource } from "../types";

export function normalizeSources(sources: MessageSource[] | undefined): MessageSource[] {
  if (!Array.isArray(sources)) return [];
  const seen = new Set<string>();
  return sources.flatMap((source, index) => {
    if (!source || typeof source.title !== "string" || typeof source.url !== "string") return [];
    try {
      const parsed = new URL(source.url);
      if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) return [];
      const key = parsed.href.replace(/#.*$/, "").replace(/\/$/, "").toLowerCase();
      if (seen.has(key)) return [];
      seen.add(key);
      return [{ ...source, id: source.id || `${key}-${index}`, url: parsed.href, domain: source.domain || parsed.hostname.replace(/^www\./, "") }];
    } catch { return []; }
  });
}
