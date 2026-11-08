import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, dateTime, humanize, MonitorData, User } from "./api";
import { ErrorBanner } from "./App";

type Tab = "calls" | "notifications" | "events";

export default function Monitor({ me }: { me: User }) {
  const [data, setData] = useState<MonitorData | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [tab, setTab] = useState<Tab>("calls");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api.monitor().then(setData).catch(setError);
  }, []);
  useEffect(load, [load]);

  const act = async (fn: () => Promise<unknown>, done: string) => {
    setBusy(true);
    setNotice(null);
    try { await fn(); setNotice(done); load(); }
    catch (e) { setNotice((e as ApiError).message); }
    finally { setBusy(false); }
  };

  if (error) return <ErrorBanner error={error} onRetry={load} />;
  const isAdmin = me.role === "admin";
  const failed = data?.notifications.filter((n) => n.status === "failed").length ?? 0;
  const pending = data?.notifications.filter((n) => n.status === "pending").length ?? 0;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Workflow monitor</h1>
          <p>Model calls, outgoing notifications and the latest audit events.</p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {isAdmin && (
            <button className="btn btn-sm" disabled={busy || pending === 0}
              onClick={() => act(api.dispatch, "Delivery run finished.")}>Deliver pending ({pending})</button>
          )}
          <button className="btn btn-sm" onClick={load}>Refresh</button>
        </div>
      </div>
      {notice && <div className="banner banner-info" role="status" style={{ marginBottom: 12 }}>{notice}</div>}
      <div className="rev-tabs" role="tablist" style={{ marginBottom: 12 }}>
        {([["calls", "Model calls"], ["notifications", `Notifications${failed ? ` · ${failed} failed` : ""}`], ["events", "Audit events"]] as [Tab, string][]).map(([k, l]) => (
          <button key={k} role="tab" className="rev-tab" aria-selected={tab === k} aria-pressed={tab === k} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      <div className="card" style={{ overflowX: "auto" }}>
        {!data ? (
          <div style={{ padding: 20 }} aria-busy="true"><div className="skeleton" style={{ height: 22 }} /></div>
        ) : tab === "calls" ? (
          data.llm_calls.length === 0 ? <div className="empty"><span>No extraction calls yet.</span></div> : (
            <table className="table">
              <thead><tr><th>When</th><th>Request</th><th>Provider</th><th>Result</th><th className="num">Latency</th><th className="num hide-sm">Tokens in/out</th><th className="num">Cost</th><th className="hide-sm">PII redacted</th></tr></thead>
              <tbody>{data.llm_calls.map((c) => (
                <tr key={c.id}>
                  <td className="muted">{dateTime(c.at)}</td>
                  <td><Link className="row-link" to={`/briefs/${c.brief_id}`}>{c.brief_title ?? "—"}</Link></td>
                  <td>{c.provider === "mock" ? "mock rules" : c.provider}<div className="hint">{c.model}</div></td>
                  <td>{c.status === "ok" ? <span className="chip chip-ok">OK</span> : <span className="chip chip-bad">{humanize(c.error_class ?? "error")}</span>}</td>
                  <td className="num">{c.latency_ms} ms</td>
                  <td className="num hide-sm">{c.input_tokens ?? "—"} / {c.output_tokens ?? "—"}</td>
                  <td className="num">{c.cost_usd ? `$${Number(c.cost_usd).toFixed(4)}` : "—"}</td>
                  <td className="hide-sm muted">{Object.entries(c.pii_redacted).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(", ") || "none"}</td>
                </tr>
              ))}</tbody>
            </table>
          )
        ) : tab === "notifications" ? (
          data.notifications.length === 0 ? <div className="empty"><span>No notifications yet. They are sent when a proposal is finalized.</span></div> : (
            <table className="table">
              <thead><tr><th>Created</th><th>Request</th><th>Channel</th><th>Status</th><th className="num">Attempts</th><th>Last error</th>{isAdmin && <th />}</tr></thead>
              <tbody>{data.notifications.map((n) => (
                <tr key={n.id}>
                  <td className="muted">{dateTime(n.created_at)}</td>
                  <td><Link className="row-link" to={`/briefs/${n.brief_id}`}>{n.brief_title ?? "—"}</Link></td>
                  <td>{n.channel === "email" ? "Email (mock)" : "CRM (mock)"}</td>
                  <td><span className={`chip ${n.status === "sent" ? "chip-ok" : n.status === "failed" ? "chip-bad" : "chip-warn"}`}>{humanize(n.status)}</span></td>
                  <td className="num">{n.attempts}</td>
                  <td className="err-text">{n.last_error ?? ""}</td>
                  {isAdmin && <td>{n.status === "failed" && (
                    <button className="btn btn-sm" disabled={busy} onClick={() => act(() => api.requeue(n.id), "Queued again with the same idempotency key.")}>Retry</button>
                  )}</td>}
                </tr>
              ))}</tbody>
            </table>
          )
        ) : (
          <table className="table">
            <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Request</th><th className="hide-sm">Request ID</th></tr></thead>
            <tbody>{data.events.map((e) => (
              <tr key={e.id}>
                <td className="muted">{dateTime(e.at)}</td>
                <td>{e.actor}</td>
                <td className="mono">{e.action}</td>
                <td>{e.brief_id ? <Link className="row-link" to={`/briefs/${e.brief_id}`}>{e.brief_title ?? "—"}</Link> : "—"}</td>
                <td className="hide-sm mono muted">{e.request_id?.slice(0, 12) ?? ""}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>
    </>
  );
}
