import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { parse } from "parse5";
import { attribute, collectMessages, languages, walk } from "../build.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(root, "dist");
const source = await readFile(path.join(root, "index.html"), "utf8");
const catalog = JSON.parse(await readFile(path.join(root, "translations.json"), "utf8"));
const text = (node) => node.nodeName === "#text" ? node.value : (node.childNodes || []).map(text).join("");
function find(document, predicate) {
  const nodes = [];
  walk(document, (node) => { if (predicate(node)) nodes.push(node); });
  return nodes;
}
const byTag = (document, tag) => find(document, (node) => node.tagName === tag);
const byAttr = (document, name, value) => find(document, (node) => attribute(node, name)?.value === value);
const sourceCards = byAttr(parse(source), "data-applet-card", "").length;

test("translations cover all visible copy and accessible labels", () => {
  for (const message of collectMessages(source).filter((value) => /[a-zA-Z]/.test(value))) {
    for (const lang of ["pt", "fr", "es"]) assert.ok(catalog[message]?.[lang], `${lang}: ${message}`);
  }
});

for (const [lang, route] of Object.entries(languages)) {
  test(`${lang}: static pages expose localized metadata, content and reciprocal alternates`, async () => {
    const errors = [];
    const html = await readFile(path.join(dist, route, "index.html"), "utf8");
    const document = parse(html, { onParseError: (error) => errors.push(error.code) });
    assert.deepEqual(errors, []);
    assert.equal(attribute(byTag(document, "html")[0], "lang").value, lang);
    assert.equal(byTag(document, "h1").length, 1);
    assert.equal(byTag(document, "title").length, 1);
    const title = text(byTag(document, "title")[0]);
    const expectedTitle = "Docking - Free Desktop Dock for Linux & BSD";
    assert.equal(title, lang === "en" ? expectedTitle : catalog[expectedTitle][lang]);
    const url = `https://docking.cc/${route}`;
    assert.equal(byAttr(document, "rel", "canonical").length, 1);
    assert.equal(attribute(byAttr(document, "rel", "canonical")[0], "href").value, url);
    assert.equal(attribute(byAttr(document, "property", "og:url")[0], "content").value, url);
    assert.equal(attribute(byAttr(document, "property", "og:title")[0], "content").value, title);
    assert.equal(attribute(byAttr(document, "name", "twitter:title")[0], "content").value, title);
    const description = attribute(byAttr(document, "name", "description")[0], "content").value;
    for (const [name, key] of [["property", "og:description"], ["name", "twitter:description"]]) {
      assert.equal(attribute(byAttr(document, name, key)[0], "content").value, description);
    }
    const alternates = byAttr(document, "rel", "alternate");
    assert.equal(alternates.length, 5);
    for (const [alternate, target] of Object.entries({ ...languages, "x-default": "" })) {
      const link = alternates.find((node) => attribute(node, "hreflang").value === alternate);
      assert.equal(attribute(link, "href").value, `https://docking.cc/${target}`);
    }
    assert.ok(sourceCards >= 65);
    const cards = byAttr(document, "data-applet-card", "");
    assert.equal(cards.length, sourceCards);
    assert.ok(cards.every((card) => !attribute(card, "hidden")));
    const englishCommands = byTag(parse(source), "pre").map(text);
    assert.deepEqual(byTag(document, "pre").map(text), englishCommands, "installation commands must remain unchanged");
    assert.ok(!html.includes("cdn.tailwindcss.com"));
    const image = byTag(document, "img").find((node) => attribute(node, "src")?.value === "/hero.gif");
    assert.equal(attribute(image, "fetchpriority").value, "high");
    assert.equal(attribute(image, "width").value, "960");
    assert.equal(attribute(image, "height").value, "315");
    const picture = byTag(document, "picture")[0];
    assert.ok(byTag(picture, "source").every((node) => attribute(node, "media")?.value === "(prefers-reduced-motion: reduce)"));
  });

  test(`${lang}: structured data matches the visible page and FAQ`, async () => {
    const document = parse(await readFile(path.join(dist, route, "index.html"), "utf8"));
    const data = byAttr(document, "type", "application/ld+json").map((node) => JSON.parse(text(node)));
    const graph = data.find((item) => item["@graph"])["@graph"];
    assert.ok(graph.some((item) => item["@type"] === "WebSite" && item.name === "Docking"));
    const page = graph.find((item) => item["@type"] === "WebPage");
    assert.equal(page.inLanguage, lang);
    assert.equal(page.url, `https://docking.cc/${route}`);
    const app = graph.find((item) => item["@type"].includes("SoftwareApplication"));
    assert.equal(app.description, attribute(byAttr(document, "name", "description")[0], "content").value);
    assert.equal(app.applicationCategory, "UtilitiesApplication");
    assert.equal(app.offers.price, "0");
    assert.ok(!app.aggregateRating && !app.review, "do not invent ratings for rich results");
    const faq = data.find((item) => item["@type"] === "FAQPage");
    const visible = byTag(byAttr(document, "id", "faq")[0], "details");
    assert.equal(faq.mainEntity.length, visible.length);
    faq.mainEntity.forEach((question, index) => {
      assert.equal(question.name, text(byTag(visible[index], "summary")[0]).trim());
      assert.equal(question.acceptedAnswer.text, text(byTag(visible[index], "p")[0]).trim());
    });
  });

  test(`${lang}: local links, fragments and image assets resolve`, async () => {
    const document = parse(await readFile(path.join(dist, route, "index.html"), "utf8"));
    const ids = find(document, (node) => attribute(node, "id")).map((node) => attribute(node, "id").value);
    assert.equal(new Set(ids).size, ids.length, "duplicate element IDs");
    for (const node of find(document, (node) => node.attrs)) {
      for (const name of ["href", "src", "srcset"]) {
        const value = attribute(node, name)?.value;
        if (!value) continue;
        if (value.startsWith("#")) assert.ok(ids.includes(value.slice(1)), `missing anchor ${value}`);
        if (value.startsWith("/") || value.startsWith("https://docking.cc/")) {
          const pathname = new URL(value, "https://docking.cc").pathname;
          const filename = pathname.endsWith("/") ? `${pathname}index.html` : pathname;
          assert.ok((await stat(path.join(dist, filename))).isFile(), filename);
        }
      }
      if (node.tagName === "img") {
        assert.ok(attribute(node, "alt"));
        assert.ok(Number(attribute(node, "width")?.value) > 0);
        assert.ok(Number(attribute(node, "height")?.value) > 0);
      }
    }
  });
}

test("sitemap includes every canonical language URL and robots allows crawling", async () => {
  const xml = await readFile(path.join(dist, "sitemap.xml"), "utf8");
  assert.ok(xml.includes('xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'));
  assert.deepEqual([...xml.matchAll(/<loc>(.*?)<\/loc>/g)].map((match) => match[1]).sort(),
    Object.values(languages).map((route) => `https://docking.cc/${route}`).sort());
  const robots = await readFile(path.join(dist, "robots.txt"), "utf8");
  assert.ok(robots.includes("Sitemap: https://docking.cc/sitemap.xml"));
  assert.ok(!/^Disallow:\s*\/$/m.test(robots));
  assert.deepEqual(await readFile(path.join(dist, "hero.gif")), await readFile(path.join(root, "hero.gif")));
});
