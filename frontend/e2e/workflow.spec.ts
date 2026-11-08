import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// The whole MVP journey through the real UI, persona by persona (spec DoD: invalid brief ->
// correction -> pricing -> approval -> export -> audit).

async function as(page: Page, persona: string) {
  await page.getByLabel("Demo persona (signed session)").selectOption(persona);
  await expect(page.getByLabel("Demo persona (signed session)")).toHaveValue(persona);
}

async function noSeriousA11yIssues(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `${v.id}: ${v.nodes.length} node(s)`)).toEqual([]);
}

test.describe.configure({ mode: "serial" });

test("happy path: brief -> extraction -> quote -> approval -> final proposal", async ({ page }) => {
  await page.goto("/");
  await as(page, "ana.sales");

  // Sales submits a brief
  await page.getByRole("button", { name: "New brief" }).first().click();
  await page.getByRole("button", { name: "Complete brief (EN)" }).click();
  await page.getByRole("button", { name: "Create request" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Northwind Cloud — Q1 demand gen");
  const briefUrl = page.url();

  // Analyst extracts and prices
  await as(page, "budi.analyst");
  await page.goto(briefUrl);
  await page.getByRole("button", { name: "Run extraction" }).click();
  await expect(page.getByText("All required fields valid")).toBeVisible();
  await expect(page.getByText("mock rules (not an LLM)").first()).toBeVisible();
  await page.getByRole("button", { name: "Calculate quote" }).click();
  await expect(page.getByText("$102,300.00").first()).toBeVisible(); // hand-checked oracle value
  await expect(page.getByText("Awaiting approval").first()).toBeVisible();
  await noSeriousA11yIssues(page);

  // Manager approves from the queue
  await as(page, "citra.manager");
  await page.getByRole("link", { name: "Approvals" }).click();
  await page.getByRole("link", { name: "Northwind Cloud — Q1 demand gen" }).click();
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText(/Approved by citra\.manager/)).toBeVisible();

  // Analyst finalizes; document is FINAL and notifications go out
  await as(page, "budi.analyst");
  await page.goto(briefUrl);
  await page.getByRole("button", { name: "Finalize proposal" }).click();
  await expect(page.getByText(/Final proposal issued by budi\.analyst/)).toBeVisible();
  await expect(page.getByText("Locked — final proposal sent")).toBeVisible();
  await expect(page.getByText("Email to sales · 1 attempt(s)")).toBeVisible();
  await expect(page.getByText(/Final proposal issued \(sha256/)).toBeVisible(); // audit trail

  const href = await page.getByRole("link", { name: /Open final proposal/ }).getAttribute("href");
  const doc = await page.request.get(href as string);
  expect(doc.status()).toBe(200);
  expect(await doc.text()).toContain("FINAL");
});

test("incomplete brief is blocked from pricing and explains what is missing", async ({ page }) => {
  await page.goto("/");
  await as(page, "budi.analyst");
  await page.getByRole("button", { name: "New brief" }).first().click();
  await page.getByRole("button", { name: "Incomplete brief" }).click();
  await page.getByRole("button", { name: "Create request" }).click();
  await page.getByRole("button", { name: "Run extraction" }).click();
  await expect(page.getByText("Needs clarification").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Calculate quote" })).toBeDisabled();
  // shown inline at the field and listed in the inspector
  await expect(page.getByRole("note").filter({ hasText: /budget is required/i })).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: /Budget — budget is required/ })).toBeVisible();
  await noSeriousA11yIssues(page);
});

test("roles: sales cannot see approvals or metrics", async ({ page }) => {
  await page.goto("/");
  await as(page, "ana.sales");
  await expect(page.getByRole("link", { name: "Approvals" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Overview" })).toHaveCount(0);
  const r = await page.request.get("/v1/metrics/overview");
  expect(r.status()).toBe(403);
});

test("overview dashboard reflects the finalized proposal", async ({ page }) => {
  await page.goto("/");
  await as(page, "citra.manager");
  await page.getByRole("link", { name: "Overview" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.locator(".tile", { hasText: "Requests" }).locator(".tile-value")).toHaveText("2");
  await expect(page.locator(".tile", { hasText: "Notifications" }).locator(".tile-value")).toHaveText("2 sent");
  await page.getByRole("button", { name: "Show table" }).first().click();
  await expect(page.getByRole("table").first()).toBeVisible();
  await noSeriousA11yIssues(page);
});
