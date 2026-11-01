// Detect preferences without redirecting or changing the language of an indexed URL.
const languageRoutes = { en: "/", pt: "/pt/", fr: "/fr/", es: "/es/" };
const languagePrompts = {
  en: "Read this page in English",
  pt: "Ler esta página em português",
  fr: "Lire cette page en français",
  es: "Leer esta página en español",
};
const preferenceKey = "docking-language";
let preferredLanguage;
try {
  preferredLanguage = localStorage.getItem(preferenceKey);
} catch {
  // Navigation and browser detection also work when storage is disabled.
}
if (!Object.hasOwn(languageRoutes, preferredLanguage)) {
  preferredLanguage = (navigator.languages || [navigator.language])
    .map((language) => language.toLowerCase().split("-")[0])
    .find((language) => Object.hasOwn(languageRoutes, language));
}
function rememberLanguage(language) {
  try { localStorage.setItem(preferenceKey, language); } catch { /* Storage is optional. */ }
}
const suggestion = document.getElementById("language-suggestion");
const suggestionLink = document.getElementById("language-suggestion-link");
if (preferredLanguage && preferredLanguage !== document.documentElement.lang) {
  suggestionLink.href = languageRoutes[preferredLanguage] + location.search + location.hash;
  suggestionLink.lang = preferredLanguage;
  suggestionLink.textContent = languagePrompts[preferredLanguage];
  suggestionLink.addEventListener("click", () => rememberLanguage(preferredLanguage));
  suggestion.hidden = false;
}
document.getElementById("language-suggestion-dismiss").addEventListener("click", () => {
  suggestion.hidden = true;
  rememberLanguage(document.documentElement.lang);
});
document.querySelectorAll("[data-language-nav] a").forEach((link) => {
  link.addEventListener("click", () => {
    rememberLanguage(link.hreflang);
    link.href = languageRoutes[link.hreflang] + location.search + location.hash;
  });
});

// The catalog is rendered at build time; JavaScript only filters existing cards.
const cards = [...document.querySelectorAll("[data-applet-card]")];
const search = document.getElementById("applet-search");
const filters = document.getElementById("applet-category-filters");
const resultCount = document.getElementById("applet-result-count");
let activeCategory = "All";
const normalize = (text) => text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
function filterApplets() {
  const query = normalize(search.value.trim());
  let visible = 0;
  cards.forEach((card) => {
    card.hidden = !((activeCategory === "All" || card.dataset.category === activeCategory)
      && normalize(card.textContent).includes(query));
    if (!card.hidden) visible++;
  });
  resultCount.textContent = resultCount.dataset.countTemplate
    .replace("{visible}", visible).replace("{total}", cards.length);
  document.getElementById("applet-empty-state").classList.toggle("hidden", visible > 0);
}
filters.addEventListener("click", (event) => {
  const button = event.target.closest("[data-applet-category]");
  if (!button) return;
  activeCategory = button.dataset.appletCategory;
  filters.querySelectorAll("button").forEach((candidate) => {
    const active = candidate === button;
    candidate.setAttribute("aria-pressed", String(active));
    candidate.className = `rounded-full border px-4 py-2 text-sm font-semibold transition ${active
      ? "border-amber-200/30 bg-ember/14 text-white"
      : "border-white/12 bg-white/[0.06] text-slate-300 hover:border-white/24 hover:text-white"}`;
  });
  filterApplets();
});
search.addEventListener("input", filterApplets);
document.getElementById("applet-controls").hidden = false;
filterApplets();

const mobileDockToggle = document.getElementById("mobile-dock-toggle");
const mobileDockMenu = document.getElementById("mobile-dock-menu");

if (mobileDockToggle && mobileDockMenu) {
  mobileDockToggle.addEventListener("click", () => {
    mobileDockMenu.classList.toggle("hidden");
    mobileDockToggle.setAttribute("aria-expanded", String(!mobileDockMenu.classList.contains("hidden")));
  });
}

(function () {
  const toggle = document.querySelector("[data-install-arch-toggle]");
  if (!toggle) return;

  const buttons = toggle.querySelectorAll("[data-install-arch-option]");
  const links = document.querySelectorAll("[data-install-link]");
  const storePath = document.querySelector("[data-install-store-path]");
  const inactiveClasses = ["text-slate-300", "hover:text-white"];
  const paletteByArch = {
    x64: {
      buttonBg: "rgba(253, 186, 116, 0.12)",
      buttonBorder: "rgba(254, 215, 170, 0.18)",
      linkBorder: "rgba(253, 186, 116, 0.20)",
      linkBg: "rgba(253, 186, 116, 0.04)",
      linkHoverBorder: "rgba(253, 186, 116, 0.30)",
      linkHoverBg: "rgba(253, 186, 116, 0.08)",
    },
    arm64: {
      buttonBg: "rgba(254, 240, 138, 0.12)",
      buttonBorder: "rgba(254, 249, 195, 0.18)",
      linkBorder: "rgba(254, 240, 138, 0.22)",
      linkBg: "rgba(254, 240, 138, 0.05)",
      linkHoverBorder: "rgba(254, 240, 138, 0.32)",
      linkHoverBg: "rgba(254, 240, 138, 0.09)",
    },
  };

  links.forEach((link) => {
    if (link.dataset.installHoverBound === "true") return;
    link.dataset.installHoverBound = "true";
    link.addEventListener("mouseenter", () => {
      const palette = paletteByArch[toggle.dataset.selectedArch || "x64"];
      link.style.borderColor = palette.linkHoverBorder;
      link.style.backgroundColor = palette.linkHoverBg;
    });
    link.addEventListener("mouseleave", () => {
      const palette = paletteByArch[toggle.dataset.selectedArch || "x64"];
      link.style.borderColor = palette.linkBorder;
      link.style.backgroundColor = palette.linkBg;
    });
  });

  function setArch(arch) {
    const palette = paletteByArch[arch];
    toggle.dataset.selectedArch = arch;

    buttons.forEach((button) => {
      const isActive = button.dataset.installArchOption === arch;
      button.setAttribute("aria-pressed", isActive ? "true" : "false");
      inactiveClasses.forEach((className) => button.classList.toggle(className, !isActive));
      if (isActive) {
        button.classList.add("text-white");
        button.style.backgroundColor = palette.buttonBg;
        button.style.border = `1px solid ${palette.buttonBorder}`;
        button.style.boxShadow = "0 8px 24px rgba(15, 23, 42, 0.22)";
      } else {
        button.classList.remove("text-white");
        button.style.backgroundColor = "transparent";
        button.style.border = "1px solid transparent";
        button.style.boxShadow = "none";
      }
    });

    links.forEach((link) => {
      link.href = arch === "arm64" ? link.dataset.hrefArm64 : link.dataset.hrefX64;
      link.style.borderColor = palette.linkBorder;
      link.style.backgroundImage = "none";
      link.style.backgroundColor = palette.linkBg;
    });

    if (storePath) {
      storePath.href = arch === "arm64" ? storePath.dataset.hrefArm64 : storePath.dataset.hrefX64;
    }
  }

  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      setArch(button.dataset.installArchOption || "x64");
    });
  });

  setArch("x64");
})();

(function () {
  const section = document.getElementById("bsd-install");
  let requested = false;

  section.addEventListener("toggle", async () => {
    if (!section.open || requested) return;
    requested = true;
    try {
      const response = await fetch("https://api.github.com/repos/edumucelli/docking/releases/latest");
      if (!response.ok) return;
      const release = await response.json();
      if (!Array.isArray(release.assets) || typeof release.tag_name !== "string") return;
      const assets = new Set(release.assets.filter((asset) => asset.state === "uploaded").map((asset) => asset.name));
      if (!assets.has("SHA256SUMS-bsd")) return;
      section.querySelectorAll("[data-bsd-asset]").forEach((link) => {
        const filename = link.dataset.bsdAsset;
        if (!assets.has(filename) || !assets.has(`${filename}.json`)) return;
        link.href = `https://github.com/edumucelli/docking/releases/download/${encodeURIComponent(release.tag_name)}/${encodeURIComponent(filename)}`;
        link.hidden = false;
        link.classList.add("inline-flex");
      });
    } catch {
      // The installation guide and release listing also work without the API.
    } finally {
      requested = [...section.querySelectorAll("[data-bsd-asset]")]
        .every((link) => !link.hidden);
    }
  });
})();
