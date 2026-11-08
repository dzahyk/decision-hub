// Typed client for the Decision Hub API. Mirrors backend/app/api/routes.py response models.
// Auth: the server sets an HttpOnly session cookie; this client never sees or stores the token.

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
export type QuoteStatus = "pending_approval" | "approved" | "rejected" | "superseded" | "finalized";
export interface Review {
  feasibility: { actor: string; feasible: boolean; note: string; at: string } | null;
  approval: { id: string; actor: string; decision: "approve" | "reject"; reason: string | null; quote_revision: number; at: string } | null;
  final: { id: string; finalized_by: string; document_sha256: string; at: string } | null;
}
export interface Quote {
  id: string;
  brief_id: string;
  revision: number;
  requirement_revision_id: string;
  rate_card_version: string;
  discount_pct: string;
  list_total: string;
  net_total: string;
  cost_total: string;
  margin_pct: string | null;
  status: QuoteStatus;
  created_by: string;
  created_at: string;
  review: Review;
  pricing: {
    currency: string; campaign_months: number; blended_region: string;
    discount_amount: string; lines: Line[]; flags: Flag[];
    requires_manager_review: boolean; requires_operations_review: boolean;
  };
}
export interface QueueItem extends Quote { brief_title: string; brief_created_by: string }
export type BriefStatus =
  | "received" | "clarification_needed" | "ready_for_pricing" | "priced"
  | "approved" | "rejected" | "finalized";
export interface BriefSummary {
  id: string; title: string; source: string; status: BriefStatus;
  created_by: string; received_at: string; updated_at: string;
}
export interface BriefDetail extends BriefSummary {
  raw_text: string;
  latest_revision: Revision | null;
  revisions: Revision[];
  latest_quote: Quote | null;
  quote_history: { id: string; revision: number; status: QuoteStatus; net_total: string; created_at: string }[];
}
export interface AuditEvent {
  id: number; aggregate_type: string; actor: string; action: string;
  before: Record<string, any> | null; after: Record<string, any> | null;
  request_id: string | null; at: string;
}
export interface NotificationRow {
  id: string; channel: string; status: "pending" | "sent" | "failed";
  attempts: number; last_error: string | null; created_at: string; sent_at: string | null;
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

// Remembered persona for the demo picker only (not a credential).
export function rememberedPersona(): string {
  try { return localStorage.getItem("dh.persona") ?? "budi.analyst"; } catch { return "budi.analyst"; }
}
function rememberPersona(id: string) {
  try { localStorage.setItem("dh.persona", id); } catch { /* storage may be blocked */ }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      credentials: "same-origin",
      ...init,
      headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
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
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown, headers?: Record<string, string>) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body), headers });

export const api = {
  health: () => request<{ status: string; llm_provider: string; demo_mode: boolean }>("/v1/health"),
  users: () => request<User[]>("/v1/users"),
  me: () => request<User>("/v1/me"),
  demoLogin: async (user_id: string) => {
    const s = await post<{ user: User }>("/v1/auth/demo-login", { user_id });
    rememberPersona(user_id);
    return s.user;
  },
  logout: () => post<void>("/v1/auth/logout"),

  schema: () => request<RequirementSchema>("/v1/schema/requirement"),
  rateCard: () => request<RateCard>("/v1/rate-cards/active"),
  briefs: () => request<BriefSummary[]>("/v1/briefs"),
  brief: (id: string) => request<BriefDetail>(`/v1/briefs/${id}`),
  createBrief: (body: { title: string; raw_text: string }, idempotencyKey: string) =>
    post<BriefDetail>("/v1/briefs", { ...body, source: "form" }, { "Idempotency-Key": idempotencyKey }),
  extract: (id: string) => post<BriefDetail>(`/v1/briefs/${id}/extract`),
  editFields: (id: string, fields: Record<string, any>) =>
    request<BriefDetail>(`/v1/briefs/${id}/requirements`, { method: "PATCH", body: JSON.stringify({ fields }) }),
  quote: (id: string, discount_pct: string) => post<Quote>(`/v1/briefs/${id}/quotes`, { discount_pct }),
  audit: (id: string) => request<AuditEvent[]>(`/v1/briefs/${id}/audit`),
  notifications: (id: string) => request<NotificationRow[]>(`/v1/briefs/${id}/notifications`),

  queue: (status = "pending_approval") => request<QueueItem[]>(`/v1/quotes?status=${status}`),
  feasibility: (qid: string, feasible: boolean, note: string) =>
    post<Quote>(`/v1/quotes/${qid}/feasibility`, { feasible, note }),
  decide: (qid: string, decision: "approve" | "reject", reason: string, expected_revision: number) =>
    post<Quote>(`/v1/quotes/${qid}/decision`, { decision, reason: reason || null, expected_revision }),
  finalize: (qid: string, idempotencyKey: string) =>
    post<Quote>(`/v1/quotes/${qid}/finalize`, undefined, { "Idempotency-Key": idempotencyKey }),
  proposalUrl: (qid: string) => `/v1/quotes/${qid}/proposal`,
};

// ---------------------------------------------------------------- formatting

export const STATUS_LABEL: Record<BriefStatus, string> = {
  received: "Received",
  clarification_needed: "Needs clarification",
  ready_for_pricing: "Ready for pricing",
  priced: "Awaiting approval",
  approved: "Approved",
  rejected: "Rejected",
  finalized: "Final proposal sent",
};

export const QUOTE_STATUS: Record<QuoteStatus, [string, string]> = {
  pending_approval: ["chip-warn", "Awaiting approval"],
  approved: ["chip-ok", "Approved"],
  rejected: ["chip-bad", "Rejected"],
  superseded: ["chip-muted", "Superseded"],
  finalized: ["chip-ok", "Finalized"],
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
