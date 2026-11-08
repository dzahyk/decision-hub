import { expect, test } from "@playwright/test";

// Phone width: no horizontal scrolling on the main screens (spec p.18, viewport < 768px).
test("no horizontal overflow on phone width", async ({ page }) => {
  for (const path of ["/welcome", "/", "/approvals", "/overview"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, path).toBeLessThanOrEqual(0);
  }
});
