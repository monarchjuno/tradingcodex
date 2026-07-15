export type RecordValue = Record<string, unknown>;
export type Section = "skills" | "library" | "system";
export type Theme = "auto" | "dark" | "light";

export type Skill = {
  id: string;
  label: string;
  description: string;
  owner: string;
  boundary: string;
  kind: string;
  status: string;
  availableInCodex: boolean;
  visible: boolean;
  protectedAction: boolean;
  raw: RecordValue;
};

export type Artifact = {
  id: string;
  title: string;
  type: string;
  sourceAsOf: string;
  confidence: string;
  readiness: string;
  summary: string;
  missingEvidence: string[];
  raw: RecordValue;
};

export function asRecord(value: unknown): RecordValue {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : {};
}

export function asText(value: unknown, fallback = ""): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return fallback;
}

export function asStringList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((item) => asText(item)).filter(Boolean);
  return typeof value === "string" && value.trim() ? [value] : [];
}

export function recordsFrom(value: unknown, key = ""): RecordValue[] {
  const items = Array.isArray(value) ? value : key ? asRecord(value)[key] : [];
  return Array.isArray(items)
    ? items.map(asRecord).filter((item) => Object.keys(item).length > 0)
    : [];
}

export function sectionError(state: RecordValue, key: string): string {
  const value = asRecord(state[key]);
  if (value.ok !== false) return "";
  const error = asRecord(value.error);
  return asText(error.message, asText(error.code, "Section unavailable."));
}

export function normalizeSkill(value: RecordValue, index: number): Skill {
  const id = asText(value.id, `skill-${index + 1}`);
  const riskTags = asStringList(value.risk_tags);
  const protectedAction = riskTags.some((tag) => ["order", "approval", "execution", "secret"].includes(tag));
  return {
    id,
    label: asText(value.label, titleCase(id)),
    description: asText(value.description),
    owner: asStringList(value.owner_roles).join(", ") || "head-manager",
    boundary: "Guides analysis; does not grant role, approval, or execution authority.",
    kind: asText(value.source, "built-in"),
    status: asText(value.status, "active"),
    availableInCodex: value.available_in_codex === true,
    visible: value.user_visible === true || value.scope !== "mainagent" || value.source !== "core",
    protectedAction,
    raw: value,
  };
}

export function normalizeArtifact(value: RecordValue, index: number): Artifact {
  const id = asText(value.artifact_id, `artifact-${index + 1}`);
  return {
    id,
    title: asText(value.title, titleCase(id)),
    type: asText(value.artifact_type, "research artifact"),
    sourceAsOf: asText(value.source_as_of),
    confidence: asText(value.confidence, "not stated"),
    readiness: asText(value.readiness_label, asText(value.handoff_state, "waiting")),
    summary: asText(value.reader_summary),
    missingEvidence: asStringList(value.missing_evidence),
    raw: value,
  };
}

export function statusTone(status: string): "good" | "warn" | "bad" | "neutral" {
  const normalized = status.toLowerCase();
  if (["complete", "completed", "succeeded", "accepted", "ready", "active", "valid", "local"].includes(normalized)) return "good";
  if (["blocked", "error", "failed", "denied", "invalid", "timed_out"].includes(normalized)) return "bad";
  if (["waiting", "revise", "needs_review", "pending", "running", "starting", "queued"].includes(normalized)) return "warn";
  return "neutral";
}

export function formatDate(value: string): string {
  if (!value) return "Not stated";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function titleCase(value: string): string {
  return value
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function formatForecastValue(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => asText(item)).filter(Boolean).join(" – ");
  if (value !== null && typeof value === "object") {
    const record = asRecord(value);
    const lower = asText(record.lower);
    const upper = asText(record.upper);
    if (lower || upper) return [lower, upper].filter(Boolean).join(" – ");
    return Object.entries(record)
      .map(([key, item]) => `${key.replaceAll("_", " ")}: ${asText(item)}`)
      .filter((item) => !item.endsWith(": "))
      .join(" · ");
  }
  return asText(value);
}
