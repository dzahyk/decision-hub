import { expect, test, type APIRequestContext, type Browser, type Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

// Builds a realistic state through the real API, then captures the real UI.
// Images are encoded to WebP inside Chromium (no image tools needed on the machine).

const OUT = join(process.cwd(), "shots");
const LANDING = join(process.cwd(), "public", "landing", "shots");
mkdirSync(OUT, { recursive: true });
mkdirSync(LANDING, { recursive: true });

const SAMPLES = {
  en: "Client is a mid-market SaaS company running a demand gen program. Target audience: IT directors and CISOs at 500-5,000 employee companies. Regions: North America and EMEA. We need 1,500 content syndication leads and 300 MQLs. Budget is $120k. Campaign runs 2026-12-01 to 2027-02-28.",
  id: "Klien fintech di Indonesia ingin lead gen. Sasaran: manajer keuangan perusahaan menengah. Butuh 2.000 content syndication leads dan 40 janji temu. Anggaran USD 95,000. Selesai akhir Q1 2027.",
  videos: "Quick one from the call today: the cybersecurity client wants around 800 webinar registrations for their launch event. They also asked whether we can produce 2 explainer videos.",
  telco: "The client is a telecom provider running a brand awareness push. Target audience: heads of procurement. Regions: EMEA. We need 1,200 content syndication leads. Budget: USD 40,000. Campaign runs 2027-01-05 to 2027-03-28.",
};

async function as(api: APIRequestContext, user: string) {
  expect((await api.post("/v1/auth/demo-login", { data: { user_id: user } })).ok()).toBeTruthy();
}

async function brief(api: APIRequestContext, title: string, text: string) {
  await as(api, "ana.sales");
  const r = await api.post("/v1/briefs", { data: { title, raw_text: text } });
  expect(r.ok()).toBeTruthy();
  const id = (await r.json()).id as string;
  await as(api, "budi.analyst");
  await api.post(`/v1/briefs/${id}/extract`);
  return id;
}

async function quote(api: APIRequestContext, id: string, discount = "0") {
  await as(api, "budi.analyst");
  const r = await api.post(`/v1/briefs/${id}/quotes`, { data: { discount_pct: discount } });
  expect(r.ok()).toBeTruthy();
  return r.json();
}

async function webp(browser: Browser, png: Buffer, file: string, w: number, h: number) {
  const page = await browser.newPage();
  const data = await page.evaluate(async ({ b64, w, h }) => {
    const img = new Image();
    img.src = `data:image/png;base64,${b64}`;
    await img.decode();
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const ctx = c.getContext("2d")!;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(img, 0, 0, w, h);
    return c.toDataURL("image/webp", 0.86).split(",")[1];
  }, { b64: png.toString("base64"), w, h });
  writeFileSync(file, Buffer.from(data, "base64"));
  await page.close();
}

async function shot(page: Page, browser: Browser, name: string, landing = false) {
  await page.waitForLoadState("networkidle");
  await page.mouse.move(1, 1);
  const png = await page.screenshot();
  await webp(browser, png, join(OUT, `decision-hub-${name}.webp`), 1600, 1000);
  if (landing) await webp(browser, png, join(LANDING, `${name}.webp`), 1600, 1000);
}

test("capture real UI", async ({ browser }) => {
  test.setTimeout(180_000);
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  const api = page.request;
  await page.goto("/v1/health");

  // --- state
  const a = await brief(api, "Northwind Cloud — Q1 demand gen", SAMPLES.en);
  const qa = await quote(api, a);
  await as(api, "citra.manager");
  await api.post(`/v1/quotes/${qa.id}/decision`, { data: { decision: "approve", reason: null, expected_revision: qa.revision } });
  await as(api, "budi.analyst");
  await api.post(`/v1/quotes/${qa.id}/finalize`, { headers: { "Idempotency-Key": "capture-final-a" } });

  const b = await brief(api, "Klien fintech — lead gen APAC", SAMPLES.id);
  await quote(api, b, "12");

  const c = await brief(api, "Cybersecurity webinar push", SAMPLES.videos);
  await api.patch(`/v1/briefs/${c}/requirements`, { data: { fields: {
    audience: "Security architects at enterprise SaaS firms", geo: ["NA", "EMEA"],
    budget: { amount: "60000", currency: "USD" }, timeline: { start_date: "2026-12-01", end_date: "2027-01-31" },
  } } });
  const qc = await quote(api, c);
  await as(api, "dewi.ops");
  await api.post(`/v1/quotes/${qc.id}/feasibility`, { data: { feasible: true, note: "Partner studio can deliver both explainer videos in January." } });

  const d = await brief(api, "Halo Telco — EMEA awareness", SAMPLES.telco);
  const qd = await quote(api, d, "15");
  await as(api, "citra.manager");
  await api.post(`/v1/quotes/${qd.id}/decision`, { data: { decision: "reject", reason: "15% on a new logo is too deep; resubmit at 8%.", expected_revision: qd.revision } });

  // --- analyst: workspace with a highlighted source quote
  await as(api, "budi.analyst");
  await page.goto(`/briefs/${b}`);
  await page.getByText("Deliverables", { exact: true }).hover();
  await page.waitForTimeout(250);
  const png = await page.screenshot();
  await webp(browser, png, join(OUT, "decision-hub-workspace.webp"), 1600, 1000);
  await webp(browser, png, join(LANDING, "workspace.webp"), 1600, 1000);

  // close-up of source + fields (16:10 crop)
  const ws = await page.locator(".workspace").boundingBox();
  const insp = await page.locator(".workspace > aside").boundingBox();
  if (ws && insp) {
    const width = insp.x - ws.x - 16;
    const crop = await page.screenshot({ clip: { x: ws.x, y: ws.y, width, height: Math.min(width * 0.625, 1000 - ws.y) } });
    await webp(browser, crop, join(LANDING, "fields.webp"), 1600, 1000);
    await webp(browser, crop, join(OUT, "decision-hub-fields.webp"), 1600, 1000);
  }

  // --- manager: decision on the flagged, ops-cleared quote
  await as(api, "citra.manager");
  await page.goto(`/briefs/${c}`);
  await shot(page, browser, "pricing", true);

  await page.goto("/approvals");
  await shot(page, browser, "approvals", true);

  await page.goto("/overview");
  await shot(page, browser, "overview", true);

  // --- final document
  await page.goto(`/v1/quotes/${qa.id}/proposal`);
  await shot(page, browser, "proposal");

  // --- phone
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  const p2 = await phone.newPage();
  await p2.goto("/v1/health");
  await as(p2.request, "budi.analyst");
  await p2.goto(`/briefs/${a}`);
  await p2.waitForLoadState("networkidle");
  await webp(browser, await p2.screenshot(), join(OUT, "decision-hub-mobile.webp"), 647, 1400);

  // --- landing hero (portfolio cover)
  await page.emulateMedia({ reducedMotion: "reduce" }); // still frame = poster
  await page.goto("/welcome");
  await shot(page, browser, "cover");
});
