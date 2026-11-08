// Typed client for the Decision Hub API. Mirrors backend/app/api/routes.py response models.

export type Role = "sales" | "analyst" | "manager" | "operations" | "admin" | "auditor";
export interface User { id: string; display_name: string; role: Role }

export type FieldStatus = "found" | "inferred" | "missing" | "edited";
export interface FieldValue {
  value: any;
  status: FieldStatus;
  source_quote: string | null;
  note: string | null;
}
export interface Issue { field: string; code: string; message: string }
export interface Validation {
  status: "valid" | "incomplete" | "invalid";
  issues: Issue[];
  normalized: Record<string, any>;
}
export interface Revision {
  id: string;
  revision: number;
  origin: "extraction" | "analyst";
  schema_version: string;
  fields: Record<string, FieldValue>;
  validation: Validation;
  provider: string | null;
  model_version: string | null;
  prompt_version: string | null;
  latency_ms: number | null;
  created_by: string;
  created_at: string;
}
export interface Flag { code: string; message: string; requires: "manager" | "operations" | "info" }
export interface Line {
  type: string; label: string; volume: number; in_catalog: boolean;
  unit_price: string | null; price_multiplier: string | null;
  line_list: string | null; line_cost: string | null; capacity: number | null;
}
export interface Quote {
  id: string;
  revision: number;
  requirement_revision_id: string;
  rate_card_version: string;
  discount_pct: string;
  list_total: string;
  net_total: string;
  cost_total: string;
  margin_pct: string | null;
  status: "draft" | "needs_review";
  created_by: string;
  created_at: string;
  pricing: {
    currency: string; campaign_months: number; blended_region: string;
    discount_amount: string; lines: Line[]; flags: Flag[];
    requires_manager_review: boolean; requires_operations_review: boolean;
  };
}
export type BriefStatus = "received" | "clarification_needed" | "ready_for_pricing" | "priced";
export interface BriefSummary {
  id: string; title: string; source: string; status: BriefStatus;
  created_by: string; received_at: string; updated_at: string;
}
export interface BriefDetail extends BriefSummary {
  raw_text: string;
  latest_revision: Revision | null;
  revisions: Revision[];
  latest_quote: Quote | null;
}
export interface AuditEvent {
  id: number; actor: string; action: string;
  before: Record<string, any> | null; after: Record<string, any> | null;
  request_id: string | null; at: string;
}
export interface RequirementSchema {
  required: string[];
  enums: { client_sector: string[]; campaign_objective: string[]; geo: string[] };
}
export interface RateCard {
  version: string;
  catalog: Record<string, { label: string; unit_price: string }>;
  discount_review_threshold_pct: string;
  max_discount_pct: string;
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

let currentUser = localStorageGet("dh.user") ?? "budi.analyst";
export const getUserId = () => currentUser;
export function setUserId(id: string) {
  currentUser = id;
  try { localStorage.setItem("dh.user", id); } catch { /* storage may be blocked */ }
}
function localStorageGet(k: string): string | null {
  try { return localStorage.getItem(k); } catch { return null; }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-User": currentUser,
        ...(init.headers ?? {}),
      },
    });
  } catch {
    throw new ApiError(0, "network", "Can't reach the API. Check that the backend is running.");
  }
  if (!res.ok) {
    let code = "http_" + res.status;
    let message = res.statusText || "Request failed";
    try {
      const body = await res.json();
      if (body?.detail?.code) { code = body.detail.code; message = body.detail.message; }
      else if (Array.isArray(body?.detail)) {
        code = "validation";
        message = body.detail.map((d: any) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join("; ");
      }
    } catch { /* non-JSON error */ }
    throw new ApiError(res.status, code, message);
  }
  return res.json() as Promise<T>;
}

export const api = {
  users: () => request<User[]>("/v1/users"),
  schema: () => request<RequirementSchema>("/v1/schema/requirement"),
  rateCard: () => request<RateCard>("/v1/rate-cards/active"),
  health: () => request<{ status: string; llm_provider: string }>("/v1/health"),
  briefs: () => request<BriefSummary[]>("/v1/briefs"),
  brief: (id: string) => request<BriefDetail>(`/v1/briefs/${id}`),
  createBrief: (body: { title: string; raw_text: string }, idempotencyKey: string) =>
    request<BriefDetail>("/v1/briefs", {
      method: "POST",
      body: JSON.stringify({ ...body, source: "form" }),
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  extract: (id: string) => request<BriefDetail>(`/v1/briefs/${id}/extract`, { method: "POST" }),
  editFields: (id: string, fields: Record<string, any>) =>
    request<BriefDetail>(`/v1/briefs/${id}/requirements`, {
      method: "PATCH",
      body: JSON.stringify({ fields }),
    }),
  quote: (id: string, discount_pct: string) =>
    request<Quote>(`/v1/briefs/${id}/quotes`, {
      method: "POST",
      body: JSON.stringify({ discount_pct }),
    }),
  audit: (id: string) => request<AuditEvent[]>(`/v1/audit/${id}`),
};

// ---------------------------------------------------------------- formatting

export const STATUS_LABEL: Record<BriefStatus, string> = {
  received: "Received",
  clarification_needed: "Needs clarification",
  ready_for_pricing: "Ready for pricing",
  priced: "Priced",
};

export const FIELD_LABEL: Record<string, string> = {
  client_sector: "Client sector",
  campaign_objective: "Campaign objective",
  audience: "Audience",
  geo: "Regions",
  deliverables: "Deliverables",
  budget: "Budget",
  timeline: "Timeline",
};

export const humanize = (s: string) => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export function money(v: string | number | null | undefined, currency = "USD") {
  if (v === null || v === undefined || v === "") return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(Number(v));
}

export function dateTime(iso: string) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  }).format(new Date(iso));
}
