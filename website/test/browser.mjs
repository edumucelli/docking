import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../dist");
const types = {
  ".html": "text/html", ".css": "text/css", ".js": "text/javascript",
  ".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif",
  ".webp": "image/webp", ".xml": "application/xml",
};
const server = http.createServer(async (request, response) => {
  const { pathname } = new URL(request.url, "http://localhost");
  const file = path.join(root, pathname.endsWith("/") ? `${pathname}index.html` : pathname);
  try {
    const data = await readFile(file);
    response.setHeader("Content-Type", types[path.extname(file)] || "text/plain");
    response.end(data);
  } catch {
    response.statusCode = 404;
    response.end("Not found");
  }
});
let browser;
let origin;

before(async () => {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ executablePath: process.env.CHROME_PATH, headless: true });
});
after(async () => {
  await browser?.close();
  server.close();
});

async function newContext(options) {
  const context = await browser.newContext(options);
  // Tests must not depend on fonts, release APIs, or any other remote service.
  await context.route("**/*", (route) => route.request().url().startsWith(origin)
    ? route.continue() : route.abort());
  return context;
}

for (const javaScriptEnabled of [true, false]) {
  for (const lang of ["en", "pt", "fr", "es"]) {
    test(`${lang}: desktop and mobile with JavaScript ${javaScriptEnabled ? "on" : "off"}`, async (t) => {
      const context = await newContext({ javaScriptEnabled, locale: lang, viewport: { width: 1365, height: 900 } });
      t.after(() => context.close());
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.goto(origin + (lang === "en" ? "/" : `/${lang}/`));
      assert.equal(await page.locator("html").getAttribute("lang"), lang);
      assert.equal(await page.locator("[data-applet-card]:visible").count(), 65);
      assert.equal(await page.locator("h1").evaluate((el) => getComputedStyle(el).opacity), "1");
      assert.equal(await page.locator('img[src="/hero.gif"]').evaluate((el) => new URL(el.currentSrc).pathname), "/hero.gif");
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      if (javaScriptEnabled) {
        const query = { en: "weather", pt: "relogio", fr: "meteo", es: "bateria" }[lang];
        await page.locator("#applet-search").fill(query);
        assert.equal(await page.locator("[data-applet-card]:visible").count(), 1);
        await page.locator("#applet-search").fill("no-matching-applet-xyz");
        assert.equal(await page.locator("[data-applet-card]:visible").count(), 0);
        assert.equal(await page.locator("#applet-empty-state").isVisible(), true);
        await page.locator("#applet-search").fill("");
        await page.locator('[data-applet-category="System & Power"]').click();
        const categories = await page.locator("[data-applet-card]:visible")
          .evaluateAll((elements) => elements.map((el) => el.dataset.category));
        assert.ok(categories.length > 0 && categories.every((value) => value === "System & Power"));
        await page.locator('[data-applet-category="All"]').click();
        await page.locator('[data-install-arch-option="arm64"]').click();
        assert.ok((await page.locator("[data-install-link]").first().getAttribute("href")).includes("aarch64"));
      } else {
        assert.equal(await page.locator("#applet-controls").isVisible(), false);
      }
      await page.setViewportSize({ width: 390, height: 844 });
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.equal(await page.locator("h1").isVisible(), true);
      if (javaScriptEnabled) {
        await page.locator("#mobile-dock-toggle").click();
        assert.equal(await page.locator("#mobile-dock-toggle").getAttribute("aria-expanded"), "true");
        assert.equal(await page.locator("#mobile-dock-menu").isVisible(), true);
        await page.locator("#mobile-dock-toggle").click();
      } else {
        assert.equal(await page.locator("#mobile-dock-menu").isVisible(), true);
      }
      assert.deepEqual(errors, []);
    });
  }
}

for (const locale of ["pt-BR", "fr-CA", "es-MX", "de-DE"]) {
  test(`${locale}: language suggestions respect URLs and explicit choices`, async (t) => {
    const context = await newContext({ locale });
    t.after(() => context.close());
    const page = await context.newPage();
    await page.goto(`${origin}/?test=1#features`);
    assert.equal(new URL(page.url()).pathname, "/", "browser language must not force a redirect");
    const language = locale.slice(0, 2);
    assert.equal(await page.locator("#language-suggestion").isVisible(), language !== "de");
    if (language !== "de") {
      assert.equal(await page.locator("#language-suggestion-link").getAttribute("href"), `/${language}/?test=1#features`);
      await page.locator("#language-suggestion-link").click();
      assert.equal(new URL(page.url()).pathname, `/${language}/`);
      assert.equal(await page.evaluate(() => localStorage.getItem("docking-language")), language);
      await page.goto(`${origin}/`);
      await page.locator("#language-suggestion-dismiss").click();
      await page.reload();
      assert.equal(await page.locator("#language-suggestion").isVisible(), false);
      await page.locator('[data-language-nav] a[hreflang="fr"]').click();
      assert.equal(new URL(page.url()).pathname, "/fr/");
      assert.equal(await page.evaluate(() => localStorage.getItem("docking-language")), "fr");
    }
  });
}

test("blocked storage and reduced motion preserve navigation and content", async (t) => {
  const context = await newContext({ locale: "fr-FR", reducedMotion: "reduce" });
  t.after(() => context.close());
  await context.addInitScript(() => Object.defineProperty(window, "localStorage", {
    get() { throw new DOMException("Storage blocked", "SecurityError"); },
  }));
  const page = await context.newPage();
  await page.goto(`${origin}/`);
  assert.equal(await page.locator("#language-suggestion").isVisible(), true);
  assert.equal(await page.locator('img[src="/hero.gif"]').evaluate((el) => new URL(el.currentSrc).pathname), "/hero-still.webp");
  await page.locator("#applet-search").fill("clock");
  assert.equal(await page.locator("[data-applet-card]:visible").count(), 1);
  assert.equal(await page.locator(".animate-marquee").evaluate((el) => getComputedStyle(el).animationName), "none");
});
