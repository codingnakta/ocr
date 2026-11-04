import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/* Phone layout: camera button, upload, OCR, tabbed review (원본/인식 결과/페이지 목록). */

const FIX = path.join(__dirname, "fixtures");
const SHOTS = process.env.E2E_SHOTS ?? path.join(__dirname, "..", "test-results", "shots");
const MODE = process.env.E2E_OCR_MODE ?? "hybrid";

test("mobile: upload, OCR and tabbed review", async ({ page }) => {
  const shot = async (name: string) => {
    fs.mkdirSync(SHOTS, { recursive: true });
    await page.screenshot({ path: path.join(SHOTS, `m-${name}.png`) });
  };
  const files = fs.readdirSync(FIX).filter((f) => /\.(jpe?g|png)$/i.test(f)).sort();

  await page.goto("/");
  await expect(page.getByRole("button", { name: "카메라로 촬영" })).toBeVisible();
  // no horizontal scrolling on a phone
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await shot("01-home");

  await page.getByTestId("file-input").setInputFiles(files.map((f) => path.join(FIX, f)));
  await page.waitForURL(/\/doc\/\?id=/, { timeout: 120_000 });
  await expect(page.getByTestId("page-card")).toHaveCount(files.length);
  await shot("02-pages");

  await page.getByTestId("tab-ocr").click();
  await page.getByTestId(`mode-${MODE}`).click();
  await page.getByTestId("start-ocr").click();
  await expect(page.getByTestId("job-card")).toContainText(/OCR (완료|일부 실패)/, { timeout: 300_000 });
  await shot("03-ocr-done");

  await page.getByTestId("tab-review").click();
  await expect(page.getByRole("tab", { name: /원본/ })).toBeVisible();
  await expect(page.getByTestId("region-text").first()).toBeVisible();
  await shot("04-review-text");
  await page.getByRole("tab", { name: /원본/ }).click();
  await expect(page.getByTestId("page-image")).toBeVisible();
  await shot("05-review-image");
  await page.getByRole("tab", { name: /페이지 목록/ }).click();
  await shot("06-review-pages");
});
