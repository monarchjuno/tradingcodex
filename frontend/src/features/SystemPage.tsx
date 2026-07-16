import { ReactNode } from "react";

import { asRecord, asStringList, asText, recordsFrom, sectionError, titleCase } from "../domain";
import { EmptyState, ErrorNotice, PageHeader, SectionHeader, StatusPill } from "../ui";
import { sectionData } from "../viewer-data.js";

export function SystemPage({ state }: { state: Record<string, unknown> }) {
  const workspaceSection = asRecord(sectionData(state, "workspace"));
  const workspace = asRecord(workspaceSection.context);
  const profile = asRecord(workspaceSection.profile);
  const investorContext = asRecord(sectionData(state, "investor_context"));
  const brokers = recordsFrom(sectionData(state, "brokers"), "connections");
  const capabilityInventory = asRecord(sectionData(state, "codex_capabilities"));
  const capabilities = recordsFrom(capabilityInventory, "capabilities");
  const capabilityWarnings = asStringList(capabilityInventory.warnings);
  const orders = recordsFrom(sectionData(state, "orders"), "tickets");
  return <section className="page system-page" aria-labelledby="system-title">
    <PageHeader eyebrow="Workspace system" title="Local state and safeguards." titleId="system-title" description="Inspect workspace identity, account scope, and execution-sensitive service posture. Changes belong in native Codex, tcx, or Admin." action={<a className="secondary-button button-link" href="/admin/">Open Admin <span aria-hidden="true">↗</span></a>} />
    {sectionError(state, "workspace") && <ErrorNotice>{sectionError(state, "workspace")}</ErrorNotice>}
    <section className="settings-section workspace-settings"><SectionHeader eyebrow="Selected environment" title="Workspace" aside={<StatusPill value={asText(workspace.execution_mode, "local")} />} /><div className="workspace-identity"><div className="workspace-monogram" aria-hidden="true">{asText(workspace.project_name, "TC").slice(0, 2).toUpperCase()}</div><div><h3>{asText(workspace.project_name, "Local workspace")}</h3><code>{asText(workspace.path, "Path not reported")}</code></div></div><dl className="settings-facts"><div><dt>Paper account</dt><dd>{asText(profile.label, asText(profile.account_id, "Workspace paper account"))}</dd></div><div><dt>Base currency</dt><dd>{asText(profile.base_currency, "Not reported")}</dd></div><div><dt>Git branch</dt><dd>{asText(workspace.git_branch, "Not reported")}{workspace.git_dirty === true ? " · uncommitted changes" : ""}</dd></div><div><dt>MCP scope</dt><dd>{titleCase(asText(workspace.mcp_scope, "project-scoped"))}</dd></div></dl></section>

    <section className="settings-section"><SectionHeader eyebrow="Personal defaults" title="Investor Context" aside={<StatusPill value={investorContext.configured === true ? "configured" : "not configured"} />} />{sectionError(state, "investor_context") ? <ErrorNotice>{sectionError(state, "investor_context")}</ErrorNotice> : <div className="settings-copy"><p>{investorContext.configured === true ? "Saved investor preferences can be applied by native analysis runs. Each run seals the applied context digest for provenance." : "No workspace Investor Context is configured. Native analysis uses the server-owned default without a personal overlay."}</p><dl><div><dt>Default</dt><dd>{investorContext.configured === true ? investorContext.enabled_by_default === false ? "Off" : "On" : "Not available"}</dd></div><div><dt>Fields</dt><dd>{asText(investorContext.field_count, "0")}</dd></div><div><dt>Updated</dt><dd>{asText(investorContext.updated_at, "Not stated")}</dd></div></dl></div>}</section>

    <div className="service-settings-grid">
      <ServiceSection title="Broker posture" eyebrow="Connections" count={brokers.length} error={sectionError(state, "brokers")} empty="No broker connections are registered.">{brokers.map((item, index) => <div className="service-row" key={asText(item.broker_id, String(index))}><div><strong>{asText(item.display_name, asText(item.broker_id, "Broker"))}</strong><span>{titleCase(asText(item.provider_id, "unknown provider"))} · {titleCase(asText(item.transport, "unknown transport"))}</span></div><StatusPill value={asText(item.status, "unknown")} /></div>)}</ServiceSection>
      <ServiceSection title="Codex capabilities" eyebrow="User-installed · BYOR" count={capabilities.length} error={sectionError(state, "codex_capabilities")} empty="No external Codex capabilities were discovered.">{capabilities.slice(0, 16).map((item, index) => <div className="service-row" key={`${asText(item.kind)}:${asText(item.id, String(index))}`}><div><strong>{asText(item.label, asText(item.id, "Capability"))}</strong><span>{[titleCase(asText(item.kind, "capability")), titleCase(asText(item.scope, "codex")), asText(item.parent_plugin) ? `Plugin ${asText(item.parent_plugin)}` : ""].filter(Boolean).join(" · ")}</span></div><StatusPill value={asText(item.availability, "unknown")} /></div>)}</ServiceSection>
      <ServiceSection title="Order state" eyebrow="Account scope" count={orders.length} error={sectionError(state, "orders")} empty="No order tickets in this workspace account.">{orders.slice(0, 8).map((item, index) => <div className="service-row" key={asText(item.ticket_id, String(index))}><div><strong>{asText(item.symbol, asText(item.ticket_id, "Order ticket"))}</strong><span>{[asText(item.side), asText(item.quantity)].filter(Boolean).join(" ") || "No order details"}</span></div><StatusPill value={asText(item.current_state, "draft")} /></div>)}</ServiceSection>
    </div>
    {capabilityWarnings.length > 0 && <ErrorNotice>Codex capability inventory is {asText(capabilityInventory.status, "partial")}: {capabilityWarnings.join(" · ")}</ErrorNotice>}

    <section className="safety-panel" aria-labelledby="safety-title"><div className="safety-symbol" aria-hidden="true">⟂</div><div><span className="eyebrow">Non-negotiable boundary</span><h2 id="safety-title">The viewer cannot act.</h2><p>This surface only reads registered workspace and Codex capability state. User-installed capabilities operate under their own licenses and permissions and are outside TradingCodex safety and audit guarantees.</p></div><StatusPill value="read only" /></section>
  </section>;
}

function ServiceSection({ title, eyebrow, count, error, empty, children }: { title: string; eyebrow: string; count: number; error: string; empty: string; children: ReactNode }) {
  return <section className="service-settings"><SectionHeader eyebrow={eyebrow} title={title} aside={<span className="count">{count}</span>} />{error ? <ErrorNotice>{error}</ErrorNotice> : count ? <div>{children}</div> : <EmptyState title="Nothing pending">{empty}</EmptyState>}</section>;
}
