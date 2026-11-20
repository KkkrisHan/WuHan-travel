import { IconChevronLeft, IconChevronRight } from "@tabler/icons-react";

export function AssistantDrawerHandle({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return <button
    className={`assistant-drawer-handle${open ? " is-open" : ""}`}
    type="button"
    aria-label={open ? "收起 AI 接待员" : "展开 AI 接待员"}
    aria-expanded={open}
    onClick={onToggle}
  >{open ? <IconChevronRight size={18}/> : <IconChevronLeft size={18}/>}</button>;
}
