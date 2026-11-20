import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { IconLink, IconExternalLink } from "@tabler/icons-react";
import type { MessageSource } from "../types";
import { normalizeSources } from "./sourceUtils";

function Favicon({ source, size }: { source: MessageSource; size: number }) {
  const [failed, setFailed] = useState(false);
  const domain = source.domain || new URL(source.url).hostname;
  return failed ? <IconLink size={size * .58}/> : <img width={size} height={size} src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64`} alt="" onError={() => setFailed(true)}/>;
}

export function SourcePopover({ sources: rawSources }: { sources: MessageSource[] }) {
  const sources = normalizeSources(rawSources);
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [locked, setLocked] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const buttonRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const openTimer = useRef<number | undefined>(undefined);
  const closeTimer = useRef<number | undefined>(undefined);
  const exitTimer = useRef<number | undefined>(undefined);

  const clearTimers = useCallback(() => { window.clearTimeout(openTimer.current); window.clearTimeout(closeTimer.current); window.clearTimeout(exitTimer.current); }, []);
  const dismiss = useCallback(() => {
    setOpen(false);
    setLocked(false);
    window.clearTimeout(exitTimer.current);
    exitTimer.current = window.setTimeout(() => setMounted(false), 120);
  }, []);
  const placePopover = useCallback(() => {
    const anchor = buttonRef.current?.getBoundingClientRect();
    if (!anchor) return;
    const width = Math.min(340, window.innerWidth - 24);
    const height = Math.min(popoverRef.current?.offsetHeight || 360, 360);
    const top = anchor.top >= height + 12 ? anchor.top - height - 8 : Math.min(anchor.bottom + 8, window.innerHeight - height - 12);
    setPosition({ top: Math.max(12, top), left: Math.min(Math.max(12, anchor.left), window.innerWidth - width - 12) });
  }, []);

  useEffect(() => {
    if (!mounted) return;
    placePopover();
    const outside = (event: PointerEvent) => {
      if (!buttonRef.current?.contains(event.target as Node) && !popoverRef.current?.contains(event.target as Node)) dismiss();
    };
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); dismiss(); buttonRef.current?.focus(); } };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", key);
    window.addEventListener("resize", placePopover);
    window.addEventListener("scroll", placePopover, true);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", key); window.removeEventListener("resize", placePopover); window.removeEventListener("scroll", placePopover, true); };
  }, [mounted, placePopover, dismiss]);
  useEffect(() => () => clearTimers(), [clearTimers]);
  if (!sources.length) return null;

  const show = () => { clearTimers(); openTimer.current = window.setTimeout(() => { setMounted(true); setOpen(true); }, 150); };
  const hide = () => { clearTimers(); if (!locked) closeTimer.current = window.setTimeout(dismiss, 200); };
  const toggle = () => { clearTimers(); if (open && locked) dismiss(); else { setMounted(true); setOpen(true); setLocked(true); } };
  const shown = sources.slice(0, 3);
  return <div className="message-sources" onMouseEnter={show} onMouseLeave={hide}>
    <button ref={buttonRef} className="source-trigger" type="button" aria-label={`查看来源：${sources[0].domain || sources[0].title}`} aria-expanded={open} onClick={toggle}>
      {shown.map((source) => <span className="source-trigger-icon" key={source.id}><Favicon source={source} size={16}/></span>)}
      {sources.length > 3 && <span className="source-trigger-more">+{sources.length - 3}</span>}
    </button>
    {mounted && createPortal(<div ref={popoverRef} className={`source-popover${open ? "" : " is-closing"}`} role="dialog" aria-label={`信息来源 · ${sources.length}`} aria-hidden={!open} style={{ top: position.top, left: position.left }} onMouseEnter={() => { clearTimers(); setOpen(true); }} onMouseLeave={hide}>
      <h3>信息来源 · {sources.length}</h3>
      <div className="source-popover-list">{sources.map((source) => <article className="source-item" key={source.id}>
        <Favicon source={source} size={20}/><div className="source-item-body"><span className="source-domain">{source.domain}</span><strong title={source.title}>{source.title}</strong>
          {source.snippet?.trim() && <p>{source.snippet}</p>}
          <a href={source.url} target="_blank" rel="noopener noreferrer">打开来源 <IconExternalLink size={13}/></a>
        </div>
      </article>)}</div>
    </div>, document.body)}
  </div>;
}
