import { Link } from "react-router-dom";
import "./landing.css";

// Public product page (spec §02/§03): cinematic hero, then the mechanism shown with real UI.
// Every number here is a measured result recorded in the repo — nothing illustrative.

const STEPS = [
  {
    n: "01",
    title: "Paste the brief as it arrived",
    body: "Email, chat or call notes, in English or Bahasa Indonesia. The original text is stored untouched; everything after it is a separate, versioned revision.",
    shot: "workspace",
    alt: "Decision Hub workspace: the original brief on the left, structured requirements in the middle, decision inspector on the right",
  },
  {
    n: "02",
    title: "AI suggests fields — and has to show its source",
    body: "Each value carries the exact sentence it came from. A quote that isn't in the brief is downgraded to “verify”. Names, emails and phone numbers are replaced before anything reaches a model.",
    shot: "fields",
    alt: "Structured fields, each with a status chip and the quoted sentence from the brief highlighted",
  },
  {
    n: "03",
    title: "Rules price it. Not the model.",
    body: "A deterministic engine on a versioned rate card computes price, cost and margin, and flags discounts above 10%, margins under the floor, capacity gaps and off-catalog items.",
    shot: "pricing",
    alt: "Deterministic pricing panel with line items, net price, margin and review flags",
  },
  {
    n: "04",
    title: "A person decides, on one exact revision",
    body: "Operations confirms feasibility, a manager approves or rejects with a reason, and no one can approve their own request. Any later change voids the approval.",
    shot: "approvals",
    alt: "Approvals queue showing which role must act next on each quote",
  },
  {
    n: "05",
    title: "One final proposal, fully traceable",
    body: "Finalizing is exactly-once, the document is hashed, notifications retry safely, and the audit trail reconstructs who did what, when, and why.",
    shot: "overview",
    alt: "Overview dashboard computed from the audit trail: lead times, rejection rate, extraction cost and notifications",
  },
];

const NEVER = [
  ["Set or change a price", "Pricing is code on a versioned rate card. The model's output schema has no price field."],
  ["Approve anything", "Decisions are manager-only, tied to one revision, and blocked for the requester."],
  ["Invent a source", "Every “found” value must quote text that exists in the brief, checked twice."],
  ["See personal data", "Emails, phones, ID numbers and named contacts are redacted before any call."],
];

const EVIDENCE = [
  ["101", "backend tests, incl. a hand-checked pricing oracle"],
  ["5 / 5", "browser journeys passing, with WCAG 2.2 AA scans"],
  ["60", "labelled synthetic briefs in the evaluation set"],
  ["0.014 ms", "p95 pricing time over 1,000 quotes"],
  ["100", "Lighthouse accessibility, desktop and mobile"],
];

export default function Landing() {
  return (
    <div className="lp">
      <a className="skip-link" href="#lp-main">Skip to content</a>
      <header className="lp-nav">
        <a className="lp-brand" href="#top"><span className="brand-mark" aria-hidden />Decision Hub</a>
        <nav aria-label="Product">
          <a href="#how">How it works</a>
          <a href="#guardrails">Guardrails</a>
          <a href="#evidence">Evidence</a>
        </nav>
        <Link className="btn btn-primary btn-sm" to="/">Open the demo</Link>
      </header>

      <main id="lp-main">
        <section className="lp-hero" id="top" aria-labelledby="lp-title">
          <div className="lp-hero-media" aria-hidden>
            <img src="/landing/hero.webp" width="1672" height="941" alt="" fetchPriority="high" decoding="async" />
            <span className="lp-sweep" />
          </div>
          <div className="lp-hero-copy">
            <span className="lp-kicker">B2B Deal Desk · AI with human oversight</span>
            <h1 id="lp-title">From messy briefs to decisions you can defend.</h1>
            <p className="lp-lede">
              Decision Hub reads a campaign brief, proves where every field came from, prices it with
              rules instead of AI, and routes it to the right person — with an audit trail for every step.
            </p>
            <div className="lp-cta">
              <Link className="btn btn-primary" to="/">Open the live demo</Link>
              <a className="btn" href="#how">See how it works</a>
            </div>
            <p className="lp-note">Portfolio project on synthetic data. Pick any persona — no sign-up.</p>
          </div>
        </section>

        <section className="lp-flow" id="how" aria-labelledby="how-title">
          <div className="lp-section-head">
            <span className="lp-kicker">How it works</span>
            <h2 id="how-title">Five steps. Each one checkable.</h2>
          </div>
          <ol className="lp-steps">
            {STEPS.map((s, i) => (
              <li key={s.n} className={`lp-step ${i % 2 ? "is-flipped" : ""}`}>
                <div className="lp-step-copy">
                  <span className="lp-step-n">{s.n}</span>
                  <h3>{s.title}</h3>
                  <p>{s.body}</p>
                </div>
                <figure className="lp-shot">
                  <img src={`/landing/shots/${s.shot}.webp`} width="1600" height="1000" alt={s.alt} loading="lazy" decoding="async" />
                </figure>
              </li>
            ))}
          </ol>
        </section>

        <section className="lp-never" id="guardrails" aria-labelledby="never-title">
          <div className="lp-section-head">
            <span className="lp-kicker">Guardrails</span>
            <h2 id="never-title">What the AI is never allowed to do.</h2>
          </div>
          <ul className="lp-never-grid">
            {NEVER.map(([t, d]) => (
              <li key={t} className="lp-never-card">
                <span className="lp-never-x" aria-hidden>×</span>
                <h3>{t}</h3>
                <p>{d}</p>
              </li>
            ))}
          </ul>
        </section>

        <section className="lp-evidence" id="evidence" aria-labelledby="evidence-title">
          <div className="lp-section-head">
            <span className="lp-kicker">Evidence</span>
            <h2 id="evidence-title">Measured, not claimed.</h2>
            <p>Results from the repository's own tests and tools. No client, users or business impact is claimed.</p>
          </div>
          <dl className="lp-evidence-grid">
            {EVIDENCE.map(([v, l]) => (
              <div key={l}><dt>{l}</dt><dd>{v}</dd></div>
            ))}
          </dl>
        </section>

        <section className="lp-final" aria-labelledby="final-title">
          <h2 id="final-title">Try the whole journey in two minutes.</h2>
          <p>Submit a brief as Sales, extract and price it as an Analyst, approve it as a Manager.</p>
          <Link className="btn btn-primary" to="/">Open the live demo</Link>
        </section>
      </main>

      <footer className="lp-footer">
        <span>Decision Hub — portfolio project by Daffa Zahy Khairy</span>
        <span>Synthetic data · not affiliated with any company</span>
      </footer>
    </div>
  );
}
