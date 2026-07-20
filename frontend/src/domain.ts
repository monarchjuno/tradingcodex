export type RecordValue = Record<string, unknown>;
export type Section = "library" | "wiki" | "system";
export type Theme = "auto" | "dark" | "light";

export type Artifact = {
  id: string;
  title: string;
  type: string;
  sourceAsOf: string;
  confidence: string;
  readiness: string;
  summary: string;
  missingEvidence: string[];
  resourceKind: "artifact" | "dataset" | "calculation";
  detailPath: string;
  raw: RecordValue;
};

export type WikiPageCard = {
  wikiId: string;
  path: string;
  title: string;
  summary: string;
  type: string;
  status: string;
  aliases: string[];
  tags: string[];
  updatedAt: string;
  sourceCount: number;
  backlinkCount: number;
  raw: RecordValue;
};

export function normalizeWikiPage(value: RecordValue): WikiPageCard {
  return {
    wikiId: asText(value.wiki_id),
    path: asText(value.path),
    title: asText(value.title, "Untitled page"),
    summary: asText(value.summary),
    type: asText(value.type, "concept"),
    status: asText(value.status, "draft"),
    aliases: asStringList(value.aliases),
    tags: asStringList(value.tags),
    updatedAt: asText(value.updated_at),
    sourceCount: Number(value.source_count || 0),
    backlinkCount: Number(value.backlink_count || 0),
    raw: value,
  };
}

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
    resourceKind: "artifact",
    detailPath: `/api/viewer/artifacts/${encodeURIComponent(id)}/`,
    raw: value,
  };
}

export function normalizeDataset(value: RecordValue, index: number): Artifact {
  const id = asText(value.object_id, `dataset-${index + 1}`);
  const details = asRecord(value.details);
  return {
    id,
    title: asText(value.title, titleCase(id)),
    type: "dataset",
    sourceAsOf: asText(value.knowledge_cutoff, asText(value.known_at)),
    confidence: "profiled",
    readiness: asText(value.status, "available"),
    summary: asText(value.summary, "Immutable reusable tabular evidence."),
    missingEvidence: asStringList(value.warnings),
    resourceKind: "dataset",
    detailPath: `/api/viewer/datasets/${encodeURIComponent(id)}/`,
    raw: { ...value, ...details },
  };
}

export function normalizeCalculation(value: RecordValue, index: number): Artifact {
  const id = asText(value.calculation_run_id, asText(value.object_id, `calculation-${index + 1}`));
  const details = asRecord(value.details);
  const originalRunId = asText(value.original_run_id, asText(details.original_run_id));
  return {
    id,
    title: `${titleCase(asText(value.calculation_type, "calculation"))} · ${id}`,
    type: "calculation run",
    sourceAsOf: asText(value.knowledge_cutoff),
    confidence: "reproducible",
    readiness: asText(value.status, "unknown"),
    summary: originalRunId ? "Reused calculation result." : "Recorded calculation result.",
    missingEvidence: asStringList(value.warnings),
    resourceKind: "calculation",
    detailPath: `/api/viewer/calculations/${encodeURIComponent(id)}/`,
    raw: { ...value, ...details },
  };
}

export function statusTone(status: string): "good" | "warn" | "bad" | "neutral" {
  const normalized = status.toLowerCase();
  if (["complete", "completed", "succeeded", "accepted", "ready", "active", "valid", "local"].includes(normalized)) return "good";
  if (["blocked", "error", "failed", "denied", "invalid", "timed_out"].includes(normalized)) return "bad";
  if (["waiting", "revise", "needs_review", "pending", "running", "starting", "queued", "draft", "contested", "superseded"].includes(normalized)) return "warn";
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
