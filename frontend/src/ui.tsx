import { ReactNode } from "react";

import { Button, Callout, Icon, NonIdealState, Spinner } from "@blueprintjs/core";

import { statusTone } from "./domain";

export function ErrorNotice({ children, retry }: { children: ReactNode; retry?: () => void }) {
  return <Callout className="notice" icon="error" intent="danger" role="alert" title="Something needs attention">{children}{retry && <Button intent="danger" minimal onClick={retry} text="Retry" />}</Callout>;
}

export function EmptyState({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return <NonIdealState className="empty-state" icon="search" title={title} description={children} action={action ? <>{action}</> : undefined} />;
}

export function LoadingState({ label = "Loading…", compact = false }: { label?: string; compact?: boolean }) {
  return <div className={`loading-state${compact ? " loading-compact" : ""}`} role="status" aria-live="polite"><Spinner size={compact ? 14 : 18} /><span>{label}</span></div>;
}

export function StatusText({ value }: { value: string }) {
  const tone = statusTone(value);
  const icon = tone === "good" ? "tick-circle" : tone === "warn" ? "warning-sign" : tone === "bad" ? "error" : "dot";
  return <span className={`status-text status-${tone}`}><Icon icon={icon} size={12} />{value.replaceAll("_", " ")}</span>;
}

export function FieldList({ values, empty = "None reported" }: { values: string[]; empty?: string }) {
  if (!values.length) return <span className="muted">{empty}</span>;
  return <ul className="field-list">{values.map((value, index) => <li key={`${value}-${index}`}>{value}</li>)}</ul>;
}

export function PageHeader({ eyebrow, title, titleId, description, action }: { eyebrow?: string; title: string; titleId?: string; description?: string; action?: ReactNode }) {
  return <header className="page-header"><div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<h1 id={titleId}>{title}</h1>{description && <p>{description}</p>}</div>{action && <div className="page-header-action">{action}</div>}</header>;
}

export function SectionHeader({ eyebrow, title, titleId, aside }: { eyebrow?: string; title: string; titleId?: string; aside?: ReactNode }) {
  return <header className="section-header"><div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<h2 id={titleId}>{title}</h2></div>{aside}</header>;
}
