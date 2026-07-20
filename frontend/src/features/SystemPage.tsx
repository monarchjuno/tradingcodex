import { ReactNode } from "react";
import { Card } from "@blueprintjs/core";

import { asRecord, asText, recordsFrom, sectionError } from "../domain";
import { ErrorNotice, PageHeader, SectionHeader, StatusText } from "../ui";
import { sectionData } from "../viewer-data.js";

export function SystemPage({ state }: { state: Record<string, unknown> }) {
  const workspaceSection = asRecord(sectionData(state, "workspace"));
  const workspace = asRecord(workspaceSection.context);
  const profile = asRecord(workspaceSection.profile);
  const investorContext = asRecord(sectionData(state, "investor_context"));
  const brokers = recordsFrom(sectionData(state, "brokers"), "connections");
  const orders = recordsFrom(sectionData(state, "orders"), "tickets");
  return <section className="page system-page" aria-labelledby="system-title">
    <PageHeader title="System" titleId="system-title" description="Workspace, account, broker connections, and orders." />
    {sectionError(state, "workspace") && <ErrorNotice>{sectionError(state, "workspace")}</ErrorNotice>}
    <Card className="settings-section workspace-settings" elevation={0}><SectionHeader title="Workspace" /><div className="workspace-identity"><h3>{asText(workspace.project_name, "Local workspace")}</h3></div><dl className="settings-facts"><div><dt>Account</dt><dd>{asText(profile.label, asText(profile.account_id, "Paper account"))}</dd></div><div><dt>Currency</dt><dd>{asText(profile.base_currency, "Not reported")}</dd></div></dl></Card>

    <Card className="settings-section" elevation={0}><SectionHeader title="Investing preferences" />{sectionError(state, "investor_context") ? <ErrorNotice>{sectionError(state, "investor_context")}</ErrorNotice> : <p className="muted">{investorContext.configured === true ? `Saved preferences are ${investorContext.enabled_by_default === false ? "off" : "on"} by default.` : "No personal investing preferences are configured."}</p>}</Card>

    <div className="service-settings-grid">
      <ServiceSection title="Broker connections" count={brokers.length} error={sectionError(state, "brokers")} empty="No broker connections.">{brokers.map((item, index) => <div className="service-row" key={asText(item.broker_id, String(index))}><strong>{asText(item.display_name, asText(item.broker_id, "Broker"))}</strong><StatusText value={asText(item.status, "unknown")} /></div>)}</ServiceSection>
      <ServiceSection title="Orders" count={orders.length} error={sectionError(state, "orders")} empty="No orders in this workspace.">{orders.slice(0, 8).map((item, index) => <div className="service-row" key={asText(item.ticket_id, String(index))}><div><strong>{asText(item.symbol, asText(item.ticket_id, "Order"))}</strong><span>{[asText(item.side), asText(item.quantity)].filter(Boolean).join(" ") || "No order details"}</span></div><StatusText value={asText(item.current_state, "draft")} /></div>)}</ServiceSection>
    </div>
  </section>;
}

function ServiceSection({ title, count, error, empty, children }: { title: string; count: number; error: string; empty: string; children: ReactNode }) {
  return <Card className="service-settings" elevation={0}><SectionHeader title={title} />{error ? <ErrorNotice>{error}</ErrorNotice> : count ? <div>{children}</div> : <p className="muted">{empty}</p>}</Card>;
}
