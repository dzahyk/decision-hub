import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, dateTime, money, Quote, QUOTE_STATUS, QueueItem, QuoteStatus, User } from "./api";
import { ErrorBanner } from "./App";

const TABS: { status: QuoteStatus; label: string }[] = [
  { status: "pending_approval", label: "Awaiting decision" },
  { status: "approved", label: "Approved — ready to finalize" },
  { status: "rejected", label: "Rejected" },
  { status: "finalized", label: "Finalized" },
];

/** Who has to act next on a quote, in plain words. */
export function nextStep(q: Pick<Quote, "status" | "pricing" | "review">) {
  if (q.status === "approved") return { who: "analyst", text: "Finalize proposal" };
  if (q.status !== "pending_approval") return null;
  if (q.pricing.requires_operations_review && !q.review.feasibility) {
    return { who: "operations", text: "Operations feasibility check" };
  }
  if (q.review.feasibility && !q.review.feasibility.feasible) {
    return { who: "manager", text: "Manager — reject (not feasible)" };
  }
  return {
    who: "manager",
    text: q.pricing.requires_manager_review ? "Manager decision with reason" : "Manager decision",
  };
}

export default function Approvals({ me }: { me: User }) {
  const [tab, setTab] = useState<QuoteStatus>("pending_approval");
  const [rows, setRows] = useState<QueueItem[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    setRows(null);
    setError(null);
    api.queue(tab).then(setRows).catch(setError);
  }, [tab]);
  useEffect(load, [load]);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Approvals</h1>
          <p>Every quote needs a manager decision before it can become a final proposal.</p>
        </div>
      </div>
      <div className="rev-tabs" role="tablist" aria-label="Quote status" style={{ marginBottom: 12 }}>
        {TABS.map((t) => (
          <button key={t.status} role="tab" className="rev-tab" aria-selected={tab === t.status}
            aria-pressed={tab === t.status} onClick={() => setTab(t.status)}>{t.label}</button>
        ))}
      </div>
      {error ? <ErrorBanner error={error} onRetry={load} /> : (
        <div className="card">
          {rows === null ? (
            <div style={{ padding: 20, display: "grid", gap: 12 }} aria-busy="true">
              {[0, 1].map((i) => <div key={i} className="skeleton" style={{ height: 22 }} />)}
            </div>
          ) : rows.length === 0 ? (
            <div className="empty"><strong>Nothing here</strong><span>Quotes appear when an analyst prices a brief.</span></div>
          ) : (
            <table className="table">
              <thead>
                <tr><th>Request</th><th className="num">Net price</th><th className="num hide-sm">Margin</th><th>Next step</th><th className="hide-sm">Priced</th></tr>
              </thead>
              <tbody>
                {rows.map((q) => {
                  const step = nextStep(q);
                  const mine = step && (step.who === me.role || (step.who === "analyst" && me.role === "manager"));
                  const [cls, label] = QUOTE_STATUS[q.status];
                  return (
                    <tr key={q.id}>
                      <td>
                        <Link className="row-link" to={`/briefs/${q.brief_id}`}>{q.brief_title}</Link>
                        <div className="hint">Quote r{q.revision} · {q.pricing.flags.filter((f) => f.requires !== "info").length} review flag(s)</div>
                      </td>
                      <td className="num">{money(q.net_total)}</td>
                      <td className="num hide-sm">{q.margin_pct ? `${q.margin_pct}%` : "—"}</td>
                      <td>
                        {step
                          ? <span className={`chip ${mine ? "chip-warn" : "chip-muted"}`}>{mine ? "Your turn: " : ""}{step.text}</span>
                          : <span className={`chip ${cls}`}>{label}</span>}
                      </td>
                      <td className="hide-sm muted">{dateTime(q.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}
    </>
  );
}
