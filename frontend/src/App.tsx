import { useCallback, useEffect, useRef, useState } from "react";
import { Link, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import {
  api, ApiError, BriefStatus, BriefSummary, dateTime, rememberedPersona, STATUS_LABEL, User,
} from "./api";
import Approvals from "./Approvals";
import Detail from "./Detail";
import Monitor from "./Monitor";
import Overview from "./Overview";

export const SAMPLE_BRIEFS: { label: string; title: string; text: string }[] = [
  {
    label: "Complete brief (EN)",
    title: "Northwind Cloud — Q1 demand gen",
    text:
      "Client is a mid-market SaaS company running a demand gen program. " +
      "Target audience: IT directors and CISOs at 500-5,000 employee companies. " +
      "Regions: North America and EMEA. We need 1,500 content syndication leads and 300 MQLs. " +
      "Budget is $120k. Campaign runs 2026-12-01 to 2027-02-28.",
  },
  {
    label: "Bilingual brief (ID)",
    title: "Klien fintech — lead gen APAC",
    text:
      "Klien fintech di Indonesia ingin lead gen. Sasaran: manajer keuangan perusahaan menengah. " +
      "Butuh 2.000 content syndication leads dan 40 janji temu. Anggaran USD 95,000. " +
      "Selesai akhir Q1 2027.",
  },
  {
    label: "Incomplete brief",
    title: "Cybersecurity webinar push",
    text:
      "Quick one from the call today: the cybersecurity client wants around 800 webinar " +
      "registrations for their launch event. They also asked whether we can produce 2 explainer videos.",
  },
];

export function StatusChip({ status }: { status: BriefStatus }) {
  const cls = {
    received: "chip-muted",
    clarification_needed: "chip-warn",
    ready_for_pricing: "chip-info",
    priced: "chip-info",
    approved: "chip-ok",
    rejected: "chip-bad",
    finalized: "chip-ok",
  }[status];
  return <span className={`chip ${cls}`}>{STATUS_LABEL[status]}</span>;
}

export function ErrorBanner({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg = error instanceof ApiError ? error.message : "Something went wrong.";
  return (
    <div className="banner banner-bad" role="alert">
      <span><strong>Couldn't load.</strong> {msg}</span>
      {onRetry && <button className="btn btn-sm" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export default function App() {
  const [users, setUsers] = useState<User[]>([]);
  const [me, setMe] = useState<User | null>(null);
  const [authError, setAuthError] = useState<ApiError | null>(null);
  const [provider, setProvider] = useState<string | null>(null);

  // Resume the cookie session; in demo mode, sign in as the remembered persona if there is none.
  const boot = useCallback(async () => {
    setAuthError(null);
    try {
      const h = await api.health();
      setProvider(h.llm_provider);
      if (h.demo_mode) api.users().then(setUsers).catch(() => setUsers([]));
      const s = await api.session();
      if (s.user) setMe(s.user);
      else if (s.demo_mode) setMe(await api.demoLogin(rememberedPersona()));
      else throw new ApiError(401, "unauthenticated", "Sign-in is required. Demo mode is off on this server.");
    } catch (e) {
      setAuthError(e as ApiError);
    }
  }, []);
  useEffect(() => { boot(); }, [boot]);

  const switchUser = async (id: string) => {
    try { setMe(await api.demoLogin(id)); } catch (e) { setAuthError(e as ApiError); }
  };

  if (authError) {
    return <main className="main"><ErrorBanner error={authError} onRetry={boot} /></main>;
  }
  if (!me) {
    return <main className="main" aria-busy="true"><div className="skeleton" style={{ height: 32, width: 280 }} /></main>;
  }
  const canSeeQueue = me.role !== "sales";
  const canSeeMetrics = ["manager", "analyst", "admin", "auditor"].includes(me.role);

  return (
    <div className="shell">
      <a className="skip-link" href="#main">Skip to content</a>
      <nav className="nav" aria-label="Primary">
        <div className="brand"><span className="brand-mark" aria-hidden />Decision Hub</div>
        {canSeeMetrics && <NavLink to="/overview">Overview</NavLink>}
        <NavLink to="/" end>Requests</NavLink>
        {canSeeQueue && <NavLink to="/approvals">Approvals</NavLink>}
        {canSeeMetrics && <NavLink to="/monitor">Workflow monitor</NavLink>}
        <div className="nav-foot">
          <label htmlFor="who">Demo persona (signed session)</label>
          <select id="who" className="select" value={me.id} onChange={(e) => switchUser(e.target.value)}>
            {users.length === 0 && <option value={me.id}>{me.display_name}</option>}
            {users.map((u) => (
              <option key={u.id} value={u.id}>{u.display_name} · {u.role}</option>
            ))}
          </select>
          {provider && (
            <span className="provider-badge" title="Extraction provider configured on the server">
              Extraction: {provider === "mock" ? "mock rules (not an LLM)" : provider}
            </span>
          )}
        </div>
      </nav>
      <main id="main" className="main" key={me.id}>
        <Routes>
          <Route path="/" element={<Inbox me={me} />} />
          <Route path="/approvals" element={<Approvals me={me} />} />
          <Route path="/overview" element={<Overview />} />
          <Route path="/monitor" element={<Monitor me={me} />} />
          <Route path="/briefs/:id" element={<Detail me={me} />} />
          <Route path="*" element={<div className="empty"><strong>Page not found</strong><Link to="/">Back to requests</Link></div>} />
        </Routes>
      </main>
    </div>
  );
}

// ------------------------------------------------------------------ inbox

function Inbox({ me }: { me?: User }) {
  const [rows, setRows] = useState<BriefSummary[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [composing, setComposing] = useState(false);
  const [filter, setFilter] = useState<BriefStatus | "all">("all");

  const load = useCallback(() => {
    setError(null);
    setRows(null);
    api.briefs().then(setRows).catch(setError);
  }, []);
  useEffect(load, [load]);

  const canCreate = me && (me.role === "sales" || me.role === "analyst");
  const visible = rows?.filter((r) => filter === "all" || r.status === filter) ?? [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Requests</h1>
          <p>Campaign briefs from sales, from intake to an approved, final proposal.</p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <select aria-label="Filter by status" className="select" style={{ width: 200 }} value={filter}
            onChange={(e) => setFilter(e.target.value as BriefStatus | "all")}>
            <option value="all">All statuses</option>
            {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          {canCreate && <button className="btn btn-primary" onClick={() => setComposing(true)}>New brief</button>}
        </div>
      </div>

      {error ? <ErrorBanner error={error} onRetry={load} /> : (
        <div className="card">
          {rows === null ? (
            <div style={{ padding: 20, display: "grid", gap: 12 }} aria-busy="true" aria-label="Loading requests">
              {[0, 1, 2].map((i) => <div key={i} className="skeleton" style={{ height: 22 }} />)}
            </div>
          ) : visible.length === 0 ? (
            <div className="empty">
              <strong>{rows.length === 0 ? "No requests yet" : "Nothing with this status"}</strong>
              <span>{rows.length === 0
                ? (canCreate ? "Paste a client brief to start. Incomplete briefs are fine — the system lists what's missing." : "Switch to a Sales or Analyst identity to create one.")
                : "Try another filter."}</span>
              {rows.length === 0 && canCreate && <button className="btn btn-primary" onClick={() => setComposing(true)}>New brief</button>}
            </div>
          ) : (
            <table className="table">
              <thead><tr><th>Request</th><th>Status</th><th className="hide-sm">Owner</th><th className="hide-sm">Received</th></tr></thead>
              <tbody>
                {visible.map((r) => (
                  <tr key={r.id}>
                    <td><Link className="row-link" to={`/briefs/${r.id}`}>{r.title}</Link></td>
                    <td><StatusChip status={r.status} /></td>
                    <td className="hide-sm muted">{r.created_by}</td>
                    <td className="hide-sm muted">{dateTime(r.received_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
      {composing && <Composer onClose={() => setComposing(false)} />}
    </>
  );
}

// ------------------------------------------------------------------ composer

function Composer({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  // One key per unchanged draft: double-clicks and retries can't create duplicates (A-FR-01).
  const key = useRef(crypto.randomUUID());
  const firstField = useRef<HTMLInputElement>(null);

  useEffect(() => {
    firstField.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const tooShort = text.trim().length < 20;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (tooShort || title.trim().length < 3) return;
    setBusy(true);
    setError(null);
    try {
      const b = await api.createBrief({ title: title.trim(), raw_text: text }, key.current);
      nav(`/briefs/${b.id}`);
    } catch (err) {
      setError(err as ApiError);
      setBusy(false); // draft stays in the form; retry reuses the same key
    }
  };

  const changeDraft = (fn: () => void) => { fn(); key.current = crypto.randomUUID(); };

  return (
    <>
      <div className="sheet-backdrop" onClick={() => !busy && onClose()} />
      <form className="sheet" role="dialog" aria-modal="true" aria-labelledby="composer-title" onSubmit={submit}>
        <div className="card-head">
          <h2 id="composer-title">New brief</h2>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} disabled={busy}>Close</button>
        </div>
        <div className="sheet-body">
          <div className="field">
            <span>Try a sample</span>
            <div className="samples">
              {SAMPLE_BRIEFS.map((s) => (
                <button key={s.label} type="button" className="btn btn-sm"
                  onClick={() => changeDraft(() => { setTitle(s.title); setText(s.text); })}>{s.label}</button>
              ))}
            </div>
          </div>
          <label className="field">
            <span>Title</span>
            <input ref={firstField} className="input" value={title} maxLength={200}
              onChange={(e) => changeDraft(() => setTitle(e.target.value))} placeholder="Client — campaign" required minLength={3} />
          </label>
          <label className="field">
            <span>Brief</span>
            <textarea className="textarea" value={text} maxLength={20000}
              onChange={(e) => changeDraft(() => setText(e.target.value))}
              placeholder="Paste the brief as you received it — email, chat, notes. English or Bahasa Indonesia." />
            <span className="hint">Stored exactly as written. Extracted fields are kept separately and can be corrected.</span>
          </label>
          {error && <div className="banner banner-bad" role="alert">{error.message} Your draft is kept — you can try again.</div>}
        </div>
        <div className="sheet-foot">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy || tooShort || title.trim().length < 3}>
            {busy ? "Saving…" : "Create request"}
          </button>
        </div>
      </form>
    </>
  );
}
