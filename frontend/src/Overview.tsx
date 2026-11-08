import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, BriefStatus, dateTime, humanize, money, Overview as OverviewData, STATUS_LABEL } from "./api";
import { ErrorBanner } from "./App";

const pct = (x: number | null | undefined) => (x === null || x === undefined ? "—" : `${(x * 100).toFixed(1)}%`);
const hours = (h: number | null) => {
  if (h === null) return "—";
  if (h < 1) return `${Math.round(h * 60)} min`;
  if (h < 48) return `${h.toFixed(1)} h`;
  return `${(h / 24).toFixed(1)} days`;
};
const FUNNEL: BriefStatus[] = ["received", "clarification_needed", "ready_for_pricing", "priced", "approved", "rejected", "finalized"];

function Tile({ label, value, sub, warn }: { label: string; value: string; sub?: string; warn?: boolean }) {
  return (
    <div className="card tile">
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value}{warn && <span className="chip chip-warn tile-chip">check</span>}</span>
      {sub && <span className="hint">{sub}</span>}
    </div>
  );
}

/** Single-series column chart: one hue, rounded data ends on the baseline, hover tooltip,
 *  keyboard-focusable marks, table fallback. One series -> no legend; the title names it. */
function ColumnChart({ title, data, format }: {
  title: string; data: { label: string; value: number }[]; format: (v: number) => string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const max = Math.max(...data.map((d) => d.value), 0);
  const W = 560, H = 180, pad = { l: 44, r: 8, t: 12, b: 26 };
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const slot = iw / data.length;
  const bw = Math.max(4, Math.min(28, slot - 2)); // >= 2px surface gap between columns
  const ticks = max > 0 ? [0, max / 2, max] : [0];
  const y = (v: number) => pad.t + ih - (max ? (v / max) * ih : 0);
  const id = title.replace(/\W+/g, "-").toLowerCase();

  return (
    <section className="card chart-card" aria-labelledby={`h-${id}`}>
      <div className="card-head">
        <h2 id={`h-${id}`}>{title}</h2>
        <button className="btn btn-ghost btn-sm" onClick={() => setTable((t) => !t)} aria-pressed={table}>
          {table ? "Show chart" : "Show table"}
        </button>
      </div>
      {max === 0 ? (
        <div className="empty" style={{ padding: 32 }}><span>No data in this period yet.</span></div>
      ) : table ? (
        <table className="table"><thead><tr><th>Day</th><th className="num">Value</th></tr></thead>
          <tbody>{data.map((d) => <tr key={d.label}><td>{d.label}</td><td className="num">{format(d.value)}</td></tr>)}</tbody>
        </table>
      ) : (
        <div className="chart-wrap">
          <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${title}, ${data.length} days`} className="chart">
            {ticks.map((t) => (
              <g key={t}>
                <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} className="grid" />
                <text x={pad.l - 6} y={y(t) + 4} textAnchor="end" className="axis">{format(t)}</text>
              </g>
            ))}
            {data.map((d, i) => {
              const x = pad.l + i * slot + (slot - bw) / 2;
              const h = Math.max(0, pad.t + ih - y(d.value));
              const r = Math.min(4, h, bw / 2);
              const top = pad.t + ih - h;
              // rounded data end (top), square at the baseline
              const path = h > 0
                ? `M${x},${pad.t + ih} V${top + r} Q${x},${top} ${x + r},${top} H${x + bw - r} Q${x + bw},${top} ${x + bw},${top + r} V${pad.t + ih} Z`
                : "";
              return (
                <g key={d.label}>
                  {path && <path d={path} className={`bar ${hover === i ? "is-hover" : ""}`} />}
                  <rect x={pad.l + i * slot} y={pad.t} width={slot} height={ih} fill="transparent"
                    tabIndex={0} aria-label={`${d.label}: ${format(d.value)}`}
                    onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
                    onFocus={() => setHover(i)} onBlur={() => setHover(null)} />
                  {(i === 0 || i === data.length - 1 || i % 7 === 0) && (
                    <text x={x + bw / 2} y={H - 8} textAnchor="middle" className="axis">{d.label.slice(5)}</text>
                  )}
                </g>
              );
            })}
            <line x1={pad.l} x2={W - pad.r} y1={pad.t + ih} y2={pad.t + ih} className="baseline" />
          </svg>
          {hover !== null && (
            <div className="tooltip" role="status" style={(() => {
              // keep the tooltip inside the card: anchor left/right near the edges
              const p = (pad.l + hover * slot + slot / 2) / W;
              const shift = p > 0.8 ? "-100%" : p < 0.2 ? "0%" : "-50%";
              return { left: `${p * 100}%`, transform: `translateX(${shift})` };
            })()}>
              <span className="hint">{data[hover].label}</span>
              <strong>{format(data[hover].value)}</strong>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

export default function Overview() {
  const [data, setData] = useState<OverviewData | null>(null);
  const [error, setError] = useState<unknown>(null);
  const load = useCallback(() => {
    setError(null);
    api.overview(14).then(setData).catch(setError);
  }, []);
  useEffect(load, [load]);

  if (error) return <ErrorBanner error={error} onRetry={load} />;
  if (!data) {
    return (
      <div aria-busy="true" aria-label="Loading overview">
        <div className="skeleton" style={{ height: 32, width: 240, margin: "8px 0 24px" }} />
        <div className="tiles">{[0, 1, 2, 3].map((i) => <div key={i} className="card skeleton" style={{ height: 96 }} />)}</div>
      </div>
    );
  }
  const d = data;
  const failedNotes = d.notifications.failed;
  const evalRun = d.eval;
  const maxFunnel = Math.max(1, ...FUNNEL.map((s) => d.briefs.by_status[s] ?? 0));

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Overview</h1>
          <p>Computed from the audit trail and model-call log · updated {dateTime(d.generated_at)}</p>
        </div>
        <button className="btn btn-sm" onClick={load}>Refresh</button>
      </div>

      <div className="tiles">
        <Tile label="Requests" value={String(d.briefs.total)} sub={`${d.quotes.total} quote revision(s)`} />
        <Tile label="Time to first quote" value={hours(d.lead_time.to_first_quote.median_h)}
          sub={d.lead_time.to_first_quote.n ? `median of ${d.lead_time.to_first_quote.n} · p90 ${hours(d.lead_time.to_first_quote.p90_h)}` : "no quotes yet"} />
        <Tile label="Time to final proposal" value={hours(d.lead_time.to_final_proposal.median_h)}
          sub={d.lead_time.to_final_proposal.n ? `median of ${d.lead_time.to_final_proposal.n}` : "none finalized yet"} />
        <Tile label="Rejection rate" value={pct(d.decisions.rejection_rate)}
          sub={`${d.decisions.approved} approved · ${d.decisions.rejected} rejected · ${d.decisions.self_approval_blocked} self-approval blocked`} />
        <Tile label="Quotes needing manager review" value={pct(d.quotes.manager_review_share)}
          sub={`operations review ${pct(d.quotes.operations_review_share)}`} />
        <Tile label="Extraction cost" value={money(d.extraction.cost_usd_total)}
          sub={`today ${money(d.extraction.cost_usd_today)} · ${d.extraction.tokens.input.toLocaleString()} in / ${d.extraction.tokens.output.toLocaleString()} out tokens`} />
        <Tile label="Extraction error rate" value={pct(d.extraction.error_rate)} warn={(d.extraction.error_rate ?? 0) > 0.05}
          sub={Object.keys(d.extraction.errors).length ? Object.entries(d.extraction.errors).map(([k, v]) => `${humanize(k)} ${v}`).join(" · ") : `${d.extraction.calls} call(s), none failed`} />
        <Tile label="Personal data redacted" value={String(d.extraction.pii_items_redacted)} sub="items replaced before any model call" />
        <Tile label="Notifications" value={`${d.notifications.sent} sent`} warn={failedNotes > 0}
          sub={`${d.notifications.pending} pending · ${failedNotes} failed`} />
      </div>

      <div className="grid-2">
        <ColumnChart title="Requests received per day" format={(v) => String(Math.round(v))}
          data={d.daily.map((x) => ({ label: x.day, value: x.briefs }))} />
        <ColumnChart title="Extraction cost per day (USD)" format={(v) => `$${v.toFixed(v < 1 ? 3 : 2)}`}
          data={d.daily.map((x) => ({ label: x.day, value: Number(x.cost_usd) }))} />
      </div>

      <div className="grid-2">
        <section className="card" aria-labelledby="funnel-h">
          <div className="card-head"><h2 id="funnel-h">Requests by status</h2></div>
          <ul className="funnel">
            {FUNNEL.map((s) => {
              const n = d.briefs.by_status[s] ?? 0;
              return (
                <li key={s}>
                  <span className="funnel-label">{STATUS_LABEL[s]}</span>
                  <span className="funnel-track"><span className="funnel-bar" style={{ width: `${(n / maxFunnel) * 100}%` }} /></span>
                  <span className="num funnel-n">{n}</span>
                </li>
              );
            })}
          </ul>
        </section>
        <section className="card" aria-labelledby="eval-h">
          <div className="card-head"><h2 id="eval-h">Latest extraction evaluation</h2></div>
          {evalRun ? (
            <div className="section">
              <p><strong>{evalRun.provider}</strong> · {evalRun.model} · {evalRun.cases} synthetic cases · {dateTime(evalRun.started_at)}</p>
              <div className="totals">
                <span className="muted">Field-level exact match</span><span>{pct(evalRun.field_exact_match)} <span className="hint">(target ≥ 90%)</span></span>
                <span className="muted">Valid briefs priced differently</span><span>{pct(evalRun.pricing_mismatch_rate)}</span>
                <span className="muted">Personal data sent to the model</span><span>{evalRun.pii_leaks} leak(s)</span>
              </div>
              {evalRun.provider === "mock" && (
                <p className="hint">This baseline is the rule-based mock, not an LLM. Run the Claude evaluation to compare.</p>
              )}
              <p className="hint mono">{evalRun.file}</p>
            </div>
          ) : <div className="empty"><span>No evaluation run yet. Run <code>python -m app.eval.run</code>.</span></div>}
        </section>
      </div>
      <p className="hint" style={{ marginTop: 16 }}>
        Numbers come from a simulated portfolio environment with synthetic data. <Link to="/monitor">Open the workflow monitor</Link> for individual calls and events.
      </p>
    </>
  );
}
