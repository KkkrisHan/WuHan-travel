import type { RefObject } from "react";
import { IconX } from "@tabler/icons-react";
import type { Message, Place } from "../types";
import { AssistantPanel } from "./AssistantPanel";

type Props = {
  open: boolean;
  closeRef: RefObject<HTMLButtonElement | null>;
  messages: Message[];
  selected: Place | null;
  modelConfigured: boolean;
  hasTrip: boolean;
  onClose: () => void;
  onSend: (message: string) => void;
  onRetry: (message: Message) => void;
  onIntro: (place: Place) => void;
};

export function AssistantDrawer({ open, closeRef, messages, selected, modelConfigured, hasTrip, onClose, onSend, onRetry, onIntro }: Props) {
  return (
    <div className={`assistant-drawer-backdrop${open ? " is-open" : ""}`} aria-hidden={!open} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside className="assistant-drawer" role="dialog" aria-modal="true" aria-hidden={!open} inert={!open} aria-label="武汉数命 SPU 城市 AI 接待员" onKeyDown={(event) => {
        if (event.key !== "Tab") return;
        const focusable = event.currentTarget.querySelectorAll<HTMLElement>('button:not([disabled]), textarea:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])');
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }}>
        <div className="assistant-drawer-top"><span>武汉数命 SPU 城市 AI 接待员</span><button ref={closeRef} type="button" onClick={onClose} aria-label="关闭 AI 接待员"><IconX size={19}/></button></div>
        <AssistantPanel messages={messages} selected={selected} modelConfigured={modelConfigured} hasTrip={hasTrip} onSend={onSend} onRetry={onRetry} onIntro={onIntro}/>
      </aside>
    </div>
  );
}
