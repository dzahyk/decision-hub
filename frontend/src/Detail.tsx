import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api, ApiError, AuditEvent, BriefDetail, dateTime, FIELD_LABEL, FieldValue, humanize, money,
  NotificationRow, QUOTE_STATUS, RateCard, RequirementSchema, Revision, User,
} from "./api";
import { ErrorBanner, StatusChip } from "./App";
import { nextStep } from "./Approvals";

const FIELD_ORDER = [
  "client_sector", "campaign_objective", "audience", "geo", "deliverables", "budget", "timeline",
];
const CAN_READ_AUDIT = new Set(["auditor", "admin", "analyst", "manager"]);
const KNOWN_CURRENCIES = ["USD", "IDR", "EUR"];

export default function Detail({ me }: { me?: User }) {
  const { id = "" } = useParams();
  const [brief, setBrief] = useState<BriefDetail | null>(null);
  const [schema, setSchema] = useState<RequirementSchema | null>(null);
  const [card, setCard] = useState<RateCard | null>(null);
  const [audit, setAudit] = useState<AuditEvent[] | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<"extract" | "quote" | "edit" | "review" | null>(null);
  const [notes, setNotes] = useState<NotificationRow[] | null>(null);
  const [viewRev, setViewRev] = useState<number | null>(null);
  const [activeField, setActiveField] = useState<string | null>(null);

  const isAnalyst = me?.role === "analyst";

  const refreshAudit = useCallback(() => {
    if (me && CAN_READ_AUDIT.has(me.role)) {
      api.audit(id).then(setAudit).catch(() => setAudit(null));
      api.notifications(id).then(setNotes).catch(() => setNotes(null));
    }
  }, [id, me]);

  const load = useCallback(() => {
    setLoadError(null);
    Promise.all([api.brief(id), api.schema(), api.rateCard()])
      .then(([b, s, c]) => { setBrief(b); setSchema(s); setCard(c); })
      .catch(setLoadError);
    refreshAudit();
  }, [id, refreshAudit]);
  useEffect(load, [load]);

  const run = async (
    kind: "extract" | "quote" | "edit" | "review", fn: () => Promise<BriefDetail | void>,
  ) => {
    setBusy(kind);
    setActionError(null);
    try {
      const next = await fn();
      setBrief(next ?? (await api.brief(id)));
      setViewRev(null);
      refreshAudit();
      return true;
    } catch (e) {
      setActionError(e as ApiError);
      return false;
    } finally {
      setBusy(null);
    }
  };

  if (loadError) return <><BackLink /><ErrorBanner error={loadError} onRetry={load} /></>;
  if (!brief || !schema || !card) return <LoadingWorkspace />;

  const latest = brief.latest_revision;
  const shown: Revision | null =
    (viewRev && brief.revisions.find((r) => r.revision === viewRev)) || latest;
  const isLatest = !!shown && shown.id === latest?.id;
  const locked = brief.status === "finalized";
  const canChange = isAnalyst && !locked;
  const activeQuote = activeField && shown ? shown.fields[activeField]?.source_quote ?? null : null;

  return (
    <>
      <BackLink />
      <div className="page-head">
        <div>
          <h1>{brief.title}</h1>
          <p>Received {dateTime(brief.received_at)} from {brief.created_by}</p>
        </div>
        <StatusChip status={brief.status} />
      </div>

      {actionError && (
        <div className="banner banner-bad" role="alert" style={{ marginBottom: 16 }}>
          <span><strong>That didn't work.</strong> {actionError.message}</span>
          <button className="btn btn-sm" onClick={() => setActionError(null)}>Dismiss</button>
        </div>
      )}

      <div className="workspace">
        <section className="panel card panel-sticky" aria-labelledby="src-h">
          <div className="card-head"><h2 id="src-h">Original brief</h2><span className="chip chip-muted">Read-only</span></div>
          <div className="source-text" tabIndex={0} aria-label="Original brief text">
            <Highlighted text={brief.raw_text} quote={activeQuote} />
          </div>
          <dl className="meta">
            <dt>Source</dt><dd>{brief.source}</dd>
            <dt>Owner</dt><dd>{brief.created_by}</dd>
            <dt>Request ID</dt><dd className="mono">{brief.id.slice(0, 8)}</dd>
          </dl>
        </section>

        <section className="panel card" aria-labelledby="fields-h">
          <div className="card-head">
            <h2 id="fields-h">Structured requirements</h2>
            {locked && <span className="chip chip-muted">Locked — final proposal sent</span>}
            {canChange && (
              <button className={`btn btn-sm ${latest ? "" : "btn-primary"}`} disabled={busy !== null}
                onClick={() => run("extract", () => api.extract(id))}>
                {busy === "extract" ? "Extracting…" : latest ? "Re-run extraction" : "Run extraction"}
              </button>
            )}
          </div>

          {!shown ? (
            <div className="empty">
              <strong>Not extracted yet</strong>
              <span>{isAnalyst
                ? "Run extraction to get suggested fields, each linked to the sentence it came from."
                : "An analyst will review this brief."}</span>
            </div>
          ) : (
            <>
              <div className="section" style={{ gap: 8 }}>
                <div className="rev-tabs" role="group" aria-label="Revisions">
                  {[...brief.revisions].reverse().map((r) => (
                    <button key={r.id} className="rev-tab" aria-pressed={r.id === shown.id}
                      onClick={() => setViewRev(r.revision)}>
                      r{r.revision} · {r.origin === "extraction" ? "extracted" : "analyst edit"}
                    </button>
                  ))}
                </div>
                <span className="provider-badge">
                  {shown.origin === "extraction"
                    ? <>By {shown.provider === "mock" ? "mock rules (not an LLM)" : shown.provider} · {shown.model_version} · prompt {shown.prompt_version}</>
                    : <>Edited by {shown.created_by}</>}
                  {" · "}{dateTime(shown.created_at)}
                </span>
                {!isLatest && <div className="banner banner-info">Viewing an older revision. Editing is only possible on the latest.</div>}
              </div>
              <div className="fields">
                {FIELD_ORDER.map((name) => (
                  <FieldRow key={name + shown.id} name={name} fv={shown.fields[name]}
                    issue={shown.validation.issues.find((i) => i.field === name)?.message}
                    schema={schema} card={card}
                    active={activeField === name} onActivate={setActiveField}
                    editable={canChange && isLatest} busy={busy !== null}
                    onSave={(value) => run("edit", () => api.editFields(id, { [name]: value }))} />
                ))}
              </div>
            </>
          )}
        </section>

        <aside className="panel card" aria-labelledby="insp-h">
          <div className="card-head"><h2 id="insp-h">Decision inspector</h2></div>
          <ValidationSection rev={latest} />
          <PricingSection brief={brief} card={card} canPrice={canChange} busy={busy}
            onPrice={(d) => run("quote", async () => { await api.quote(id, d); })} />
          {brief.latest_quote && (
            <ReviewSection brief={brief} me={me} busy={busy !== null}
              act={(fn) => run("review", async () => { await fn(); })} />
          )}
          {notes && notes.length > 0 && <NotificationsSection rows={notes} />}
          {audit && <AuditSection events={audit} />}
        </aside>
      </div>
    </>
  );
}

function BackLink() {
  return <Link className="crumb" to="/">← Requests</Link>;
}

function LoadingWorkspace() {
  return (
    <div aria-busy="true" aria-label="Loading request">
      <div className="skeleton" style={{ height: 32, width: 320, margin: "8px 0 24px" }} />
      <div className="workspace">
        {[0, 1, 2].map((i) => <div key={i} className="card skeleton" style={{ height: 360 }} />)}
      </div>
    </div>
  );
}

function Highlighted({ text, quote }: { text: string; quote: string | null }) {
  const at = quote ? text.indexOf(quote) : -1;
  if (!quote || at < 0) return <>{text}</>;
  return <>{text.slice(0, at)}<mark>{quote}</mark>{text.slice(at + quote.length)}</>;
}

const STATUS_CHIP: Record<string, [string, string]> = {
  found: ["chip-ok", "Found in brief"],
  inferred: ["chip-info", "Inferred — check"],
  missing: ["chip-warn", "Missing"],
  edited: ["chip-edit", "Edited by analyst"],
};

function formatValue(name: string, v: any, card: RateCard): string {
  if (v === null || v === undefined || v === "" || (Array.isArray(v) && v.length === 0)) return "Not provided";
  switch (name) {
    case "client_sector":
    case "campaign_objective": return humanize(String(v));
    case "geo": return (v as string[]).join(", ");
    case "deliverables":
      return (v as { type: string; volume: number }[])
        .map((d) => `${Number(d.volume).toLocaleString("en-US")} × ${card.catalog[d.type]?.label ?? `${humanize(d.type)} (not on rate card)`}`)
        .join("\n");
    case "budget": {
      const cur = KNOWN_CURRENCIES.includes(v.currency) ? v.currency : "USD";
      return money(v.amount, cur) + (v.currency !== "USD" ? ` (${v.currency})` : "");
    }
    case "timeline": return v.start_date ? `${v.start_date} → ${v.end_date}` : `By ${v.end_date}`;
    default: return typeof v === "string" ? v : JSON.stringify(v);
  }
}

function FieldRow(props: {
  name: string; fv?: FieldValue; issue?: string; schema: RequirementSchema; card: RateCard;
  active: boolean; onActivate: (n: string | null) => void;
  editable: boolean; busy: boolean; onSave: (value: any) => Promise<boolean>;
}) {
  const { name, issue, schema, card, active, onActivate, editable, busy, onSave } = props;
  const fv: FieldValue = props.fv ?? { value: null, status: "missing", source_quote: null, note: null };
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<any>(fv.value);
  const [chipCls, chipText] = STATUS_CHIP[fv.status];
  const missing = fv.value === null || fv.value === undefined;

  const save = async () => { if (await onSave(draft)) setEditing(false); };

  return (
    <div className={`frow ${active ? "is-active" : ""}`}
      onMouseEnter={() => onActivate(name)} onMouseLeave={() => onActivate(null)}
      onFocus={() => onActivate(name)}>
      <div className="frow-top">
        <span className="label">{FIELD_LABEL[name]}</span>
        <span className={`chip ${chipCls}`}>{chipText}</span>
      </div>
      {!editing && <div className={`value ${missing ? "is-missing" : ""}`} style={{ whiteSpace: "pre-line" }}>{formatValue(name, fv.value, card)}</div>}
      {issue && !editing && <div className="err-text" role="note">⚠ {issue}</div>}
      {!editing && (fv.source_quote
        ? <div className="quote">“{fv.source_quote}”</div>
        : <div className="quote none">No supporting text found in the brief</div>)}
      {fv.note && !editing && <div className="note">{fv.note}</div>}

      {editing && (
        <div className="editor">
          <FieldEditor name={name} value={draft} onChange={setDraft} schema={schema} card={card} />
          <div className="editor-row">
            <button className="btn btn-primary btn-sm" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save as new revision"}</button>
            <button className="btn btn-sm" onClick={() => { setDraft(fv.value); setEditing(false); }} disabled={busy}>Cancel</button>
          </div>
        </div>
      )}
      {editable && !editing && (
        <div><button className="btn btn-ghost btn-sm" onClick={() => { setDraft(structuredClone(fv.value)); setEditing(true); }}>
          {missing ? "Add value" : "Correct"}
        </button></div>
      )}
    </div>
  );
}

function FieldEditor({ name, value, onChange, schema, card }: {
  name: string; value: any; onChange: (v: any) => void; schema: RequirementSchema; card: RateCard;
}) {
  const label = `${FIELD_LABEL[name]} value`;
  if (name === "client_sector" || name === "campaign_objective") {
    return (
      <select className="select" aria-label={label} value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}>
        <option value="">Select…</option>
        {schema.enums[name].map((o) => <option key={o} value={o}>{humanize(o)}</option>)}
      </select>
    );
  }
  if (name === "audience") {
    return <input className="input" aria-label={label} value={value ?? ""} onChange={(e) => onChange(e.target.value)} placeholder="e.g. CFOs at mid-market banks" />;
  }
  if (name === "geo") {
    const set = new Set<string>(value ?? []);
    return (
      <fieldset className="checks" style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="hint">Regions</legend>
        {schema.enums.geo.map((g) => (
          <label key={g}><input type="checkbox" checked={set.has(g)}
            onChange={(e) => { if (e.target.checked) set.add(g); else set.delete(g); onChange([...set]); }} />{g}</label>
        ))}
      </fieldset>
    );
  }
  if (name === "deliverables") {
    const rows: { type: string; volume: number }[] = value ?? [];
    const types = Array.from(new Set([...Object.keys(card.catalog), ...rows.map((r) => r.type)]));
    const update = (i: number, patch: Partial<{ type: string; volume: number }>) =>
      onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
    return (
      <div className="editor">
        {rows.map((r, i) => (
          <div className="editor-row" key={i}>
            <input className="input" type="number" min={1} step={1} style={{ width: 110 }} aria-label={`Volume ${i + 1}`}
              value={r.volume} onChange={(e) => update(i, { volume: parseInt(e.target.value || "0", 10) })} />
            <select className="select" aria-label={`Deliverable ${i + 1}`} value={r.type} onChange={(e) => update(i, { type: e.target.value })}>
              {types.map((t) => <option key={t} value={t}>{card.catalog[t]?.label ?? `${humanize(t)} (not on rate card)`}</option>)}
            </select>
            <button className="btn btn-ghost btn-sm" aria-label={`Remove deliverable ${i + 1}`} onClick={() => onChange(rows.filter((_, j) => j !== i))}>Remove</button>
          </div>
        ))}
        <div><button className="btn btn-sm" onClick={() => onChange([...rows, { type: Object.keys(card.catalog)[0], volume: 100 }])}>Add deliverable</button></div>
      </div>
    );
  }
  if (name === "budget") {
    return (
      <div className="editor-row">
        <input className="input" type="number" min={1} step="0.01" aria-label="Budget amount"
          value={value?.amount ?? ""} onChange={(e) => onChange({ amount: e.target.value, currency: "USD" })} />
        <span className="hint">USD (rate card v1 is USD only)</span>
      </div>
    );
  }
  if (name === "timeline") {
    const v = value ?? { start_date: null, end_date: "" };
    return (
      <div className="editor-row" style={{ flexWrap: "wrap" }}>
        <label className="field"><span>Start (optional)</span>
          <input className="input" type="date" value={v.start_date ?? ""} onChange={(e) => onChange({ ...v, start_date: e.target.value || null })} /></label>
        <label className="field"><span>End</span>
          <input className="input" type="date" value={v.end_date ?? ""} onChange={(e) => onChange({ ...v, end_date: e.target.value })} /></label>
      </div>
    );
  }
  return null;
}

function ValidationSection({ rev }: { rev: Revision | null }) {
  if (!rev) return <div className="section"><h3>Validation</h3><p className="muted">Waiting for extraction.</p></div>;
  const v = rev.validation;
  const chip = { valid: ["chip-ok", "All required fields valid"], incomplete: ["chip-warn", "Needs clarification"], invalid: ["chip-bad", "Has invalid values"] }[v.status];
  return (
    <div className="section">
      <h3>Validation · r{rev.revision}</h3>
      <span className={`chip ${chip[0]}`}>{chip[1]}</span>
      {v.issues.length > 0 && (
        <>
          <ul className="issues">
            {v.issues.map((i) => (
              <li key={i.field}><span aria-hidden>{i.code === "missing" ? "○" : "×"}</span><span><strong>{FIELD_LABEL[i.field]}</strong> — {i.message}</span></li>
            ))}
          </ul>
          <p className="hint">Ask sales for these details, or correct the fields. No quote can be created until all are valid.</p>
        </>
      )}
    </div>
  );
}

function PricingSection({ brief, card, canPrice, busy, onPrice }: {
  brief: BriefDetail; card: RateCard; canPrice: boolean; busy: string | null;
  onPrice: (discount: string) => Promise<boolean>;
}) {
  const q = brief.latest_quote;
  const [discount, setDiscount] = useState(q?.discount_pct ? String(Number(q.discount_pct)) : "0");
  const ready = brief.latest_revision?.validation.status === "valid";
  const stale = q && brief.latest_revision && q.requirement_revision_id !== brief.latest_revision.id;
  const d = Number(discount);
  const discountError = Number.isNaN(d) || d < 0 ? "Enter 0 or more" : d > Number(card.max_discount_pct) ? `Maximum is ${card.max_discount_pct}%` : null;
  const overThreshold = !discountError && d > Number(card.discount_review_threshold_pct);
  const revNo = useMemo(
    () => brief.revisions.find((r) => r.id === q?.requirement_revision_id)?.revision,
    [brief.revisions, q?.requirement_revision_id],
  );

  return (
    <div className="section">
      <h3>Deterministic pricing</h3>
      {canPrice && (
        <form className="editor-row" style={{ alignItems: "flex-end" }}
          onSubmit={(e) => { e.preventDefault(); if (!discountError) onPrice(String(d)); }}>
          <label className="field" style={{ width: 130 }}>
            <span>Discount %</span>
            <input className="input" type="number" min={0} max={Number(card.max_discount_pct)} step="0.01"
              value={discount} onChange={(e) => setDiscount(e.target.value)} aria-invalid={!!discountError}
              aria-describedby="disc-hint" />
          </label>
          <button className="btn btn-primary" type="submit" disabled={!ready || !!discountError || busy !== null}>
            {busy === "quote" ? "Calculating…" : q ? "Recalculate" : "Calculate quote"}
          </button>
        </form>
      )}
      {canPrice && (
        <span id="disc-hint" className={discountError ? "err-text" : "hint"}>
          {discountError ?? (overThreshold
            ? `Above ${card.discount_review_threshold_pct}% — a manager must approve.`
            : `Rate card ${card.version}. Above ${card.discount_review_threshold_pct}% needs manager approval.`)}
        </span>
      )}
      {canPrice && !ready && <p className="hint">Available once all required fields are valid.</p>}

      {stale && (
        <div className="banner banner-warn" role="status">Requirements changed after this quote (priced on r{revNo}). Recalculate before using it.</div>
      )}

      {q ? (
        <>
          <div className="totals" aria-live="polite">
            <span className="muted">Net price</span><span className="big">{money(q.net_total)}</span>
            <span className="muted">List</span><span>{money(q.list_total)}</span>
            <span className="muted">Discount {Number(q.discount_pct)}%</span><span>−{money(q.pricing.discount_amount)}</span>
            <span className="muted">Delivery cost</span><span>{money(q.cost_total)}</span>
            <span className="muted">Margin</span><span>{q.margin_pct ? `${q.margin_pct}%` : "—"}</span>
          </div>
          <table className="lines" style={{ width: "100%" }}>
            <thead><tr><th>Line</th><th className="num">Qty</th><th className="num">Amount</th></tr></thead>
            <tbody>
              {q.pricing.lines.map((l, i) => (
                <tr key={i}>
                  <td>{l.label}{!l.in_catalog && <span className="muted"> — not priced</span>}</td>
                  <td className="num">{l.volume.toLocaleString("en-US")}</td>
                  <td className="num">{l.line_list ? money(l.line_list) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="hint">
            {q.pricing.blended_region} rates (highest of the requested regions) · {q.pricing.campaign_months} month(s) · quote r{q.revision} · {q.rate_card_version}
          </p>
          {q.pricing.flags.length > 0 ? (
            <ul className="flags">
              {q.pricing.flags.map((f) => (
                <li key={f.code} className={f.requires === "info" ? "banner-info" : "banner-warn"}>
                  <span className="who">{f.requires === "info" ? "Note" : `Needs ${f.requires} review`}</span>
                  <span>{f.message}</span>
                </li>
              ))}
            </ul>
          ) : <span className="chip chip-ok">Within policy — no review flags</span>}
        </>
      ) : ready ? <p className="muted">No quote yet.</p> : null}
    </div>
  );
}

// ------------------------------------------------------------------ review & decision

function ReviewSection({ brief, me, busy, act }: {
  brief: BriefDetail; me?: User; busy: boolean; act: (fn: () => Promise<unknown>) => Promise<boolean>;
}) {
  const q = brief.latest_quote!;
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  // One key per finalize intent: a double-click or network retry replays instead of duplicating.
  const finalizeKey = useRef(crypto.randomUUID());
  const step = nextStep(q);
  const [cls, label] = QUOTE_STATUS[q.status];
  const role = me?.role;
  const needsReason = q.pricing.requires_manager_review;
  const fe = q.review.feasibility;
  const ap = q.review.approval;
  const fin = q.review.final;
  const ownQuote = me && (me.id === q.created_by || me.id === brief.created_by);
  const opsBlocks = q.pricing.requires_operations_review && (!fe || !fe.feasible);

  return (
    <div className="section">
      <h3>Review & decision · quote r{q.revision}</h3>
      <span className={`chip ${cls}`}>{label}</span>
      {step && <p className="hint">Next: {step.text}.</p>}

      {fe && (
        <div className={`banner ${fe.feasible ? "banner-info" : "banner-bad"}`}>
          <span><strong>Operations: {fe.feasible ? "feasible" : "not feasible"}</strong> — “{fe.note}” <span className="hint">({fe.actor}, {dateTime(fe.at)})</span></span>
        </div>
      )}

      {/* operations feasibility (A-BR-08) */}
      {role === "operations" && q.status === "pending_approval" && q.pricing.requires_operations_review && (
        <form className="editor" onSubmit={(e) => e.preventDefault()}>
          <label className="field"><span>Feasibility note</span>
            <textarea className="textarea" style={{ minHeight: 80 }} value={note} onChange={(e) => setNote(e.target.value)}
              placeholder="Capacity, team, timeline risks…" />
          </label>
          <div className="editor-row">
            <button type="button" className="btn btn-primary btn-sm" disabled={busy || note.trim().length < 5}
              onClick={() => act(() => api.feasibility(q.id, true, note)).then((ok) => ok && setNote(""))}>Feasible</button>
            <button type="button" className="btn btn-sm" disabled={busy || note.trim().length < 5}
              onClick={() => act(() => api.feasibility(q.id, false, note)).then((ok) => ok && setNote(""))}>Not feasible</button>
          </div>
        </form>
      )}

      {/* manager decision (A-FR-08) */}
      {role === "manager" && q.status === "pending_approval" && (
        ownQuote ? (
          <div className="banner banner-warn">You created this request, so another manager must decide.</div>
        ) : (
          <form className="editor" onSubmit={(e) => e.preventDefault()}>
            <label className="field"><span>Reason {needsReason ? "(required — quote has policy flags)" : "(required to reject)"}</span>
              <textarea className="textarea" style={{ minHeight: 80 }} value={reason} onChange={(e) => setReason(e.target.value)}
                aria-describedby="reason-hint" />
            </label>
            <span id="reason-hint" className="hint">Your decision applies to quote r{q.revision} exactly. Any later change voids it.</span>
            {opsBlocks && <span className="err-text">⚠ Approval is blocked until operations confirms feasibility.</span>}
            <div className="editor-row">
              <button type="button" className="btn btn-primary btn-sm"
                disabled={busy || opsBlocks || (needsReason && reason.trim().length < 5)}
                onClick={() => act(() => api.decide(q.id, "approve", reason, q.revision))}>Approve</button>
              <button type="button" className="btn btn-sm" disabled={busy || reason.trim().length < 5}
                onClick={() => act(() => api.decide(q.id, "reject", reason, q.revision))}>Reject</button>
            </div>
          </form>
        )
      )}

      {ap && (
        <p className="hint">
          {ap.decision === "approve" ? "Approved" : "Rejected"} by {ap.actor} · {dateTime(ap.at)}
          {ap.reason && <> — “{ap.reason}”</>}
        </p>
      )}
      {q.status === "rejected" && role === "analyst" && (
        <p className="hint">Correct the requirements or recalculate with a different discount to submit a new revision.</p>
      )}

      {/* finalize (A-FR-09) */}
      {q.status === "approved" && (role === "analyst" || role === "manager") && (
        <button className="btn btn-primary" disabled={busy}
          onClick={() => act(() => api.finalize(q.id, finalizeKey.current))}>
          {busy ? "Finalizing…" : "Finalize proposal"}
        </button>
      )}
      {fin && (
        <div className="banner banner-info" role="status">
          <span>Final proposal issued by {fin.finalized_by} · {dateTime(fin.at)}<br />
            <span className="mono">sha256 {fin.document_sha256.slice(0, 16)}…</span></span>
        </div>
      )}
      <div>
        <a className="btn btn-ghost btn-sm" href={api.proposalUrl(q.id)} target="_blank" rel="noopener">
          {fin ? "Open final proposal" : "Preview draft proposal"} ↗
        </a>
      </div>

      {brief.quote_history.length > 1 && (
        <details>
          <summary className="hint">Earlier quote revisions ({brief.quote_history.length - 1})</summary>
          <ul className="issues" style={{ marginTop: 8 }}>
            {brief.quote_history.slice(1).map((h) => (
              <li key={h.id}><span className={`chip ${QUOTE_STATUS[h.status][0]}`}>{QUOTE_STATUS[h.status][1]}</span> r{h.revision} · {money(h.net_total)}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function NotificationsSection({ rows }: { rows: NotificationRow[] }) {
  return (
    <div className="section">
      <h3>Notifications (mock)</h3>
      <ul className="issues">
        {rows.map((n) => (
          <li key={n.id}>
            <span className={`chip ${n.status === "sent" ? "chip-ok" : n.status === "failed" ? "chip-bad" : "chip-warn"}`}>{humanize(n.status)}</span>
            <span>{n.channel === "email" ? "Email to sales" : "CRM update"} · {n.attempts} attempt(s){n.last_error && <span className="err-text"> — {n.last_error}</span>}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function AuditSection({ events }: { events: AuditEvent[] }) {
  const describe = (e: AuditEvent) => {
    const a = e.after ?? {};
    switch (e.action) {
      case "brief.created": return "Brief received";
      case "requirements.extracted": return `Extraction r${a.revision} by ${a.provider} — ${a.validation}`;
      case "requirements.edited": return `Fields corrected (r${a.revision}): ${Object.keys(a.changes ?? {}).map((k) => FIELD_LABEL[k] ?? k).join(", ")}`;
      case "brief.status_changed": return `Status → ${humanize(a.status)}`;
      case "extraction.failed": return `Extraction failed: ${a.message}`;
      case "quote.priced": return `Quote r${a.revision} priced: ${money(a.net_total)} on ${a.rate_card_version}`;
      case "quote.superseded": return `Quote r${e.before?.revision} superseded (${a.reason})`;
      case "quote.feasibility_noted": return `Feasibility: ${a.feasible ? "feasible" : "not feasible"} — ${a.note}`;
      case "quote.approved": return `Quote r${a.revision} approved${a.reason ? ` — ${a.reason}` : ""}`;
      case "quote.rejected": return `Quote r${a.revision} rejected — ${a.reason}`;
      case "quote.decision_blocked": return "Decision blocked: self-approval";
      case "quote.finalized": return `Final proposal issued (sha256 ${String(a.document_sha256).slice(0, 10)}…)`;
      default: return humanize(e.action.replace(".", " "));
    }
  };
  return (
    <div className="section">
      <h3>Audit trail</h3>
      <ol className="timeline-list">
        {events.map((e) => (
          <li key={e.id}><span className="dot" aria-hidden /><span>
            {describe(e)}<br /><span className="hint">{e.actor} · {dateTime(e.at)}</span>
          </span></li>
        ))}
      </ol>
    </div>
  );
}
