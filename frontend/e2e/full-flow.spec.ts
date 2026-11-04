import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/*
 * Full user flow in a real browser:
 * upload -> page order -> OCR job (progress) -> review/edit -> export PDF
 * -> PDF.js preview: search (Ctrl+F equivalent) and mouse drag-selection of Korean text.
 *
 * Verifies drag/select/search in PDF.js (the engine of Firefox's viewer) only.
 * Chrome's built-in viewer (PDFium) and Adobe Reader are checked manually
 * (docs/testing.md), because headless browsers do not expose them.
 */

const FIX = path.join(__dirname, "fixtures");
const SHOTS = process.env.E2E_SHOTS ?? path.join(__dirname, "..", "test-results", "shots");
const MODE = process.env.E2E_OCR_MODE ?? "hybrid";

async function shot(page: Page, name: string) {
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: false });
}

test("upload → OCR → edit → searchable PDF → select & search in PDF.js", async ({ page, request }) => {
  const files = fs.readdirSync(FIX).filter((f) => /\.(jpe?g|png|webp|pdf)$/i.test(f)).sort();
  expect(files.length).toBeGreaterThan(0);

  // ---- 1. upload
  await page.goto("/");
  await expect(page.getByText("필기를 검색 가능한 PDF로")).toBeVisible();
  await shot(page, "01-home");
  await page.getByPlaceholder(/문서 제목/).fill("E2E 테스트 필기");
  await page.getByTestId("file-input").setInputFiles(files.map((f) => path.join(FIX, f)));
  await page.waitForURL(/\/documents\/[0-9a-f]+/, { timeout: 120_000 });
  const docId = page.url().match(/documents\/([0-9a-f]+)/)![1];
  await expect(page.getByTestId("page-card")).toHaveCount(files.length);
  await shot(page, "02-pages");

  // ---- 2. order matches the (natural-sorted) selection order
  const doc = await (await request.get(`/api/documents/${docId}`)).json();
  expect(doc.pages.map((p: { label: string }) => p.label)).toEqual(files);

  // ---- 3. OCR
  await page.getByTestId("tab-ocr").click();
  await page.getByTestId(`mode-${MODE}`).click();
  await expect(page.getByTestId("estimate")).toContainText(MODE === "local" ? "$0" : "$");
  await shot(page, "03-ocr-settings");
  await page.getByTestId("start-ocr").click();
  await expect(page.getByTestId("job-card")).toBeVisible();
  await page.waitForTimeout(4000);
  await shot(page, "04-ocr-progress");
  await expect(page.getByTestId("job-card")).toContainText(/OCR (완료|일부 실패)/, { timeout: 300_000 });
  await shot(page, "05-ocr-done");

  // ---- OCR accuracy guard for pages with a known ground truth (*.gt.txt next to the image)
  const squashT = (s: string) => s.replace(/\s+/g, "");
  const docNow = await (await request.get(`/api/documents/${docId}`)).json();
  for (const p of docNow.pages as { id: string; label: string }[]) {
    const gtFile = path.join(FIX, p.label.replace(/\.[^.]+$/, ".gt.txt"));
    if (!fs.existsSync(gtFile) || MODE === "local") continue;
    const ocr = await (await request.get(`/api/documents/${docId}/pages/${p.id}/ocr`)).json();
    const got = squashT(ocr.regions.map((r: { text: string }) => r.text).join(""));
    const want = squashT(fs.readFileSync(gtFile, "utf8"));
    let same = 0;
    for (const ch of want) if (got.includes(ch)) same++;
    // every ground-truth line must be present (catches rotated pages / invented text)
    for (const line of fs.readFileSync(gtFile, "utf8").split("\n").filter(Boolean)) {
      expect(got, `missing line: ${line}`).toContain(squashT(line).slice(0, 6));
    }
    expect(same / want.length).toBeGreaterThan(0.95);
  }

  // ---- 4. review & edit (edit is autosaved and must reach the PDF)
  await page.getByTestId("tab-review").click();
  const items = page.getByTestId("region-text");
  await expect(items.first()).toBeVisible();
  await shot(page, "06-review");
  const lastIdx = (await items.count()) - 1;
  const target = items.nth(lastIdx);
  await target.click();
  await target.fill("E2E 수정 문장 검색용");
  await page.waitForTimeout(1500); // debounce autosave
  await shot(page, "07-review-edited");

  // ---- 5. export PDF
  await page.getByTestId("tab-export").click();
  await page.getByTestId("format-pdf").click();
  await page.getByTestId("create-export").click();
  await expect(page.getByTestId("download-link").first()).toBeVisible({ timeout: 120_000 });
  await shot(page, "08-export");
  const dl = page.waitForEvent("download");
  await page.getByTestId("download-link").first().click();
  const file = await dl;
  expect(file.suggestedFilename()).toMatch(/\.pdf$/);

  // ---- 6. PDF.js preview: search + drag-select
  await page.getByRole("link", { name: "미리보기" }).first().click();
  await expect(page.getByTestId("pdf-pages")).toHaveAttribute("data-state", "ready", { timeout: 60_000 });
  const spans = page.locator(".textLayer span");
  await expect(spans.first()).toBeAttached();

  // search for the edited sentence and for text recognized by OCR
  await page.getByTestId("pdf-search").fill("E2E 수정 문장");
  await page.getByRole("button", { name: "검색" }).click();
  await expect(page.getByTestId("pdf-hits")).toHaveText(/^[1-9]\d*건$/);
  await shot(page, "09-pdfjs-search");

  // drag across EVERY line like a person: along the line's slope (the text layer is
  // rotated with the handwriting), from the first glyph to the last one
  await page.waitForTimeout(1000); // let the search's smooth scroll finish
  const squash = (s: string) => s.replace(/\s+/g, "");
  const dragPoints = (el: Element) => {
    const tn = el.firstChild as Text;
    const txt = tn.textContent ?? "";
    const r = document.createRange();
    let a = 0;
    while (a < txt.length && /\s/.test(txt[a])) a++;
    let z = txt.length - 1;
    while (z > 0 && /\s/.test(txt[z])) z--;
    r.setStart(tn, a);
    r.setEnd(tn, a + 1);
    const f = r.getBoundingClientRect();
    r.setStart(tn, z);
    r.setEnd(tn, z + 1);
    const l = r.getBoundingClientRect();
    const fx = f.left + f.width / 2, fy = f.top + f.height / 2;
    const lx = l.left + l.width / 2, ly = l.top + l.height / 2;
    const len = Math.hypot(lx - fx, ly - fy);
    if (len < 2) return [f.left + 1, fy, f.right - 1, fy];
    const ux = (lx - fx) / len, uy = (ly - fy) / len;
    const k0 = Math.min(f.width, f.height) * 0.2, k1 = Math.min(l.width, l.height) * 0.3;
    return [fx - ux * k0, fy - uy * k0, lx + ux * k1, ly + uy * k1];
  };
  const drag = async (from: number[], to: number[]) => {
    await page.mouse.move(from[0], from[1]);
    await page.mouse.down();
    await page.mouse.move(to[0], to[1], { steps: 12 });
    await page.mouse.up();
    return page.evaluate(() => window.getSelection()?.toString() ?? "");
  };
  const n = await spans.count();
  const results: string[] = [];
  let exact = 0;
  let total = 0;
  let hangulOk = false;
  for (let i = 0; i < n; i++) {
    const t = ((await spans.nth(i).textContent()) ?? "").trim();
    if (!t) continue;
    await spans.nth(i).evaluate((el) => el.scrollIntoView({ block: "center" }));
    await page.waitForTimeout(100);
    const q = await spans.nth(i).evaluate(dragPoints);
    const sel = await drag([q[0], q[1]], [q[2], q[3]]);
    total++;
    const ok = squash(sel) === squash(t);
    if (ok) exact++;
    if (ok && /[가-힣]{3,}/.test(t)) hangulOk = true;
    results.push(`${ok ? "OK " : "BAD"} ${t}  ->  ${sel.replace(/\n/g, "⏎")}`);
  }
  expect(hangulOk).toBe(true);
  expect(exact / total).toBeGreaterThanOrEqual(0.9);
  await shot(page, "10-pdfjs-drag-select");

  // multi-line drag keeps reading order and line breaks
  const first = spans.nth(1);
  const last = spans.nth(Math.min(4, n - 1));
  await first.evaluate((el) => el.scrollIntoView({ block: "center" }));
  await page.waitForTimeout(150);
  const a = await first.evaluate(dragPoints);
  const z = await last.evaluate(dragPoints);
  const multi = await drag([a[0], a[1]], [z[2], z[3]]);
  const expectedMulti: string[] = [];
  for (let i = 1; i <= Math.min(4, n - 1); i++) expectedMulti.push(((await spans.nth(i).textContent()) ?? "").trim());
  expect(squash(multi)).toBe(squash(expectedMulti.join("")));
  expect(multi.split("\n").length).toBeGreaterThanOrEqual(2);
  await expect(page.getByTestId("pdf-selection")).toBeVisible();
  await shot(page, "11-pdfjs-multiline-select");
  fs.writeFileSync(
    path.join(SHOTS, "selection.txt"),
    `single-line exact: ${exact}/${total}\n${results.join("\n")}\n\nmulti-line:\n${multi}\n`,
  );
});
