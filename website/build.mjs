import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { parse, serialize } from "parse5";

export const languages = { en: "", pt: "pt/", fr: "fr/", es: "es/" };
const origin = "https://docking.cc/";
const source = path.dirname(fileURLToPath(import.meta.url));
const translatedAttributes = new Set(["alt", "title", "aria-label", "placeholder", "data-count-template"]);
const ignoredTags = new Set(["script", "style", "noscript", "pre", "code"]);

export function walk(node, visit) {
  visit(node);
  for (const child of node.childNodes || []) walk(child, visit);
}

export function attribute(node, name) {
  return node.attrs?.find((attr) => attr.name === name);
}

function translateDocument(document, translate) {
  function visit(node, ignored = false) {
    ignored ||= ignoredTags.has(node.tagName);
    if (ignored) return;
    if (node.nodeName === "#text" && node.value.trim()) {
      node.value = node.value.replace(/\S[\s\S]*\S|\S/, (value) => translate(value));
    }
    for (const attr of node.attrs || []) {
      if (translatedAttributes.has(attr.name) && attr.value) attr.value = translate(attr.value);
      if (node.tagName === "meta" && attr.name === "content") {
        const key = attribute(node, "name")?.value || attribute(node, "property")?.value;
        if (["description", "og:title", "og:description", "og:image:alt", "twitter:title", "twitter:description", "twitter:image:alt"].includes(key)) {
          attr.value = translate(attr.value);
        }
      }
    }
    if (!attribute(node, "data-language-nav")) {
      for (const child of node.childNodes || []) visit(child, ignored);
    }
  }
  visit(document);
}

export function collectMessages(html) {
  const messages = new Set();
  translateDocument(parse(html), (text) => { messages.add(text); return text; });
  return [...messages];
}

export async function build() {
  const html = await readFile(path.join(source, "index.html"), "utf8");
  const messages = JSON.parse(await readFile(path.join(source, "translations.json"), "utf8"));
  const output = path.join(source, "dist");
  await rm(output, { recursive: true, force: true });
  await mkdir(output, { recursive: true });
  for (const [language, route] of Object.entries(languages)) {
    const translate = (text) => {
      if (language === "en" || !/[a-zA-Z]/.test(text)) return text;
      const value = messages[text]?.[language];
      if (!value) throw new Error(`Missing ${language} translation: ${text}`);
      return value;
    };
    const document = parse(html);
    translateDocument(document, translate);
    const url = origin + route;
    walk(document, (node) => {
      if (node.tagName === "html") attribute(node, "lang").value = language;
      if (attribute(node, "rel")?.value === "canonical") attribute(node, "href").value = url;
      if (attribute(node, "property")?.value === "og:url") attribute(node, "content").value = url;
      if (attribute(node, "property")?.value === "og:locale") {
        attribute(node, "content").value = { en: "en_US", pt: "pt_PT", fr: "fr_FR", es: "es_ES" }[language];
      }
      if (node.tagName === "a" && attribute(node, "hreflang")?.value === language) {
        node.attrs.push({ name: "aria-current", value: "page" });
      }
      if (node.tagName === "script" && attribute(node, "type")?.value === "application/ld+json") {
        const data = JSON.parse(node.childNodes[0].value);
        if (data["@graph"]) {
          const webpage = data["@graph"].find((item) => item["@type"] === "WebPage");
          webpage["@id"] = `${url}#webpage`;
          webpage.url = url;
          webpage.inLanguage = language;
          webpage.name = translate(webpage.name);
          const app = data["@graph"].find((item) => Array.isArray(item["@type"]));
          app.description = translate(app.description);
          app.featureList = app.featureList.map(translate);
        } else if (data["@type"] === "FAQPage") {
          data["@id"] = `${url}#faq`;
          data.inLanguage = language;
          for (const question of data.mainEntity) {
            question.name = translate(question.name);
            question.acceptedAnswer.text = translate(question.acceptedAnswer.text);
          }
        }
        node.childNodes[0].value = JSON.stringify(data).replaceAll("<", "\\u003c");
      }
    });
    const alternates = Object.entries(languages).map(([lang, page]) =>
      `<link rel="alternate" hreflang="${lang}" href="${origin}${page}">`
    ).join("\n") + `\n<link rel="alternate" hreflang="x-default" href="${origin}">`;
    const rendered = serialize(document).replace("</head>", `${alternates}\n</head>`);
    await mkdir(path.join(output, route), { recursive: true });
    await writeFile(path.join(output, route, "index.html"), rendered);
  }
  // Retain previously published image URLs for existing links and cached previews.
  for (const asset of ["app.js", "robots.txt", "sitemap.xml", "hero.gif", "hero.png", "hero-still.webp", "social-preview.png", "social-preview.jpg"]) {
    await cp(path.join(source, asset), path.join(output, asset));
  }
  await cp(path.join(source, "../docking/assets/icons/applets"), path.join(output, "icons"), { recursive: true });
  for (const [size, name] of [[48, "favicon"], [256, "logo"]]) {
    await cp(path.join(source, `../packaging/deb/icons/hicolor/${size}x${size}/apps/org.docking.Docking.png`), path.join(output, `${name}.png`));
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await build();
