import path from "node:path";
import { promises as fs } from "node:fs";
import { execFile as execFileCallback } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

import { fetchMarathonNews } from "./fetchMarathonNews.js";
import { generateBilingualArtifacts } from "./buildBilingualMarathonNews.js";

const execFile = promisify(execFileCallback);

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const NEWS_ROOT = path.resolve(__dirname, "..");
const MYBLOG_ROOT = path.resolve(NEWS_ROOT, "..", "..", "myblog");
const MYBLOG_NEWS_ROOT = path.join(MYBLOG_ROOT, "source", "news");
const MYBLOG_DATA_DIR = path.join(MYBLOG_NEWS_ROOT, "data");
const MYBLOG_ARTICLES_DIR = path.join(MYBLOG_NEWS_ROOT, "articles");

const DEFAULT_LIMIT = 200;
const ITEMS_PER_PAGE = 8;
const FALLBACK_ICON = "/marathon-lore/assets/images/icons/marathon-brand-mark.webp";
const GENERATED_ZH_PATH = path.join(NEWS_ROOT, "sample-marathon-news-zh-generated.json");
const BILINGUAL_PATH = path.join(NEWS_ROOT, "sample-marathon-news-bilingual.json");
const ENGLISH_OUTPUT = path.join(NEWS_ROOT, "sample-marathon-news-en-us.json");
const CHINESE_OUTPUT = path.join(NEWS_ROOT, "sample-marathon-news-zh-chs-full.json");
const BLOG_DATA_OUTPUT = path.join(MYBLOG_DATA_DIR, "marathon-news.json");
const BLOG_SYNC_REPORT_OUTPUT = path.join(MYBLOG_DATA_DIR, "marathon-news.sync-report.json");
const TRANSLATOR_SCRIPT = path.join(NEWS_ROOT, "trans", "translate_news.py");

function parseArgs(argv) {
  const options = {
    limit: DEFAULT_LIMIT,
    includeBody: true,
    pretty: true,
    skipTranslation: false,
    useExistingGeneratedTranslation: false,
    allowEnglishOnly: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];

    if (arg === "--limit" && argv[index + 1]) {
      options.limit = Number.parseInt(argv[index + 1], 10);
      index += 1;
      continue;
    }

    if (arg === "--no-body") {
      options.includeBody = false;
      continue;
    }

    if (arg === "--compact") {
      options.pretty = false;
      continue;
    }

    if (arg === "--skip-translation") {
      options.skipTranslation = true;
      continue;
    }

    if (arg === "--use-existing-generated-translation") {
      options.useExistingGeneratedTranslation = true;
      continue;
    }

    if (arg === "--allow-english-only") {
      options.allowEnglishOnly = true;
      continue;
    }

    if (/^\d+$/.test(arg)) {
      options.limit = Number.parseInt(arg, 10);
    }
  }

  if (!Number.isInteger(options.limit) || options.limit <= 0) {
    throw new Error("--limit must be a positive integer");
  }

  return options;
}

function summarizeMissingChineseItems(items) {
  return items
    .filter((item) => !item.languages?.zh)
    .map((item) => ({
      id: item.id,
      slug: item.slug,
      title: getItemHeadline(item),
      articleUrl: item.articleUrl?.en || null,
    }));
}

async function writeJson(filePath, payload, pretty = true) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  const spacing = pretty ? 2 : 0;
  await fs.writeFile(filePath, `${JSON.stringify(payload, null, spacing)}\n`, "utf8");
}

async function readJsonIfExists(filePath) {
  try {
    const raw = await fs.readFile(filePath, "utf8");
    return JSON.parse(raw);
  } catch (error) {
    if (error?.code === "ENOENT") {
      return null;
    }
    throw error;
  }
}

function makeSlug(articlePath, id) {
  const base = String(articlePath || "")
    .replace(/^\/+/, "")
    .trim();
  return base || id;
}

function makePathSlug(slug) {
  return String(slug || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function pickPrimaryImage(item) {
  return (
    item?.coverImages?.[0] ||
    item?.bannerImages?.[0] ||
    item?.mobileImages?.[0] ||
    null
  );
}

function createExcerpt(content) {
  const subtitle = String(content?.subtitle || "").trim();
  if (subtitle) {
    return subtitle;
  }

  const body = String(content?.bodyText || "").trim().replace(/\s+/g, " ");
  if (!body) {
    return "";
  }

  return body.length > 220 ? `${body.slice(0, 217)}...` : body;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function escapeFrontMatterString(value) {
  return JSON.stringify(String(value ?? ""));
}

function formatDate(value, locale = "en-US") {
  if (!value) {
    return "Unknown";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  }).format(date);
}

function normalizeLanguageContent(entry) {
  if (!entry) {
    return null;
  }

  return {
    title: String(entry.title || ""),
    subtitle: String(entry.subtitle || ""),
    bodyHtml: String(entry.bodyHtml || ""),
    bodyText: String(entry.bodyText || ""),
  };
}

function deriveChineseContent(item, generatedMap) {
  if (item.zhChs) {
    return {
      source: "official",
      content: normalizeLanguageContent(item.zhChs),
    };
  }

  const generated = generatedMap.get(item.articlePath);
  if (generated?.zhChs) {
    return {
      source: "generated",
      content: normalizeLanguageContent(generated.zhChs),
    };
  }

  return null;
}

function normalizeBlogItem(item, generatedMap) {
  const english = normalizeLanguageContent(item.enUs);
  const chinese = deriveChineseContent(item, generatedMap);
  const primary = item.enUs || item.zhChs || {};
  const bodyText = english?.bodyText || "";
  const wordCount = bodyText ? bodyText.split(/\s+/).length : 0;
  const readMinutes = Math.max(1, Math.round(wordCount / 220));
  const slug = makeSlug(item.articlePath, item.contentId);
  const pathSlug = makePathSlug(slug);
  const localPath = `/news/articles/${pathSlug}/`;

  const content = {
    en: {
      ...english,
      excerpt: createExcerpt(english),
    },
  };

  if (chinese?.content) {
    content.zh = {
      ...chinese.content,
      excerpt: createExcerpt(chinese.content),
      source: chinese.source,
    };
  }

  return {
    id: item.contentId || item.enUs?.id || item.zhChs?.id || pathSlug,
    slug,
    pathSlug,
    category: primary.category || "community",
    author: primary.author || "Bungie",
    publishedAt: item.publishedAt || primary.publishedAt || null,
    articlePath: item.articlePath,
    articleUrl: {
      en: item.articleUrl?.enUs || item.enUs?.articleUrl || null,
      zh: item.articleUrl?.zhChs || item.zhChs?.articleUrl || null,
    },
    localPath,
    matchType: item.matchType,
    languages: {
      en: true,
      zh: Boolean(chinese?.content),
    },
    taxonomies: primary.taxonomies || [],
    images: {
      primary: pickPrimaryImage(primary),
      cover: primary.coverImages || [],
      mobile: primary.mobileImages || [],
      banner: primary.bannerImages || [],
      inline: primary.inlineImageUrls || [],
    },
    metrics: {
      wordCount,
      readMinutes,
    },
    content,
  };
}

function makeArticleDescription(item) {
  const excerpt = String(item.content?.en?.excerpt || "").replace(/\s+/g, " ").trim();
  if (!excerpt) {
    return "English source content archived from Bungie Marathon News.";
  }

  return excerpt.length > 160 ? `${excerpt.slice(0, 157)}...` : excerpt;
}

function getItemHeadline(item) {
  return item?.content?.en?.title || item?.slug || item?.id || "Untitled";
}

function collectChanges(previousData, nextItems) {
  const previousItems = Array.isArray(previousData?.items) ? previousData.items : [];
  const previousById = new Map(previousItems.map((item) => [item.id, item]));
  const nextById = new Map(nextItems.map((item) => [item.id, item]));

  const added = [];
  const updated = [];
  const removed = [];

  for (const item of nextItems) {
    const previous = previousById.get(item.id);
    if (!previous) {
      added.push(item);
      continue;
    }

    const previousSignature = JSON.stringify({
      publishedAt: previous.publishedAt,
      articleUrl: previous.articleUrl,
      enBodyHtml: previous.content?.en?.bodyHtml,
      zhBodyHtml: previous.content?.zh?.bodyHtml || "",
      zhEnabled: previous.languages?.zh || false,
      primaryImage: previous.images?.primary?.url || "",
    });
    const nextSignature = JSON.stringify({
      publishedAt: item.publishedAt,
      articleUrl: item.articleUrl,
      enBodyHtml: item.content?.en?.bodyHtml,
      zhBodyHtml: item.content?.zh?.bodyHtml || "",
      zhEnabled: item.languages?.zh || false,
      primaryImage: item.images?.primary?.url || "",
    });

    if (previousSignature !== nextSignature) {
      updated.push(item);
    }
  }

  for (const item of previousItems) {
    if (!nextById.has(item.id)) {
      removed.push(item);
    }
  }

  return { added, updated, removed };
}

function buildSyncReport({
  fetchedAt,
  items,
  changes,
  bilingualSummary,
  generatedCount,
  translationAttempted,
  translationAvailable,
}) {
  return {
    generatedAt: fetchedAt,
    source: "bungie-contentstack",
    totalFetched: items.length,
    itemsPerPage: ITEMS_PER_PAGE,
    bilingual: {
      officialBilingualCount: bilingualSummary?.bilingualCount || 0,
      englishOnlyCount: bilingualSummary?.englishOnlyCount || 0,
      generatedChineseCount: generatedCount,
      translationAttempted,
      translationAvailable,
    },
    changes: {
      addedCount: changes.added.length,
      updatedCount: changes.updated.length,
      removedCount: changes.removed.length,
      added: changes.added.map((item) => ({
        id: item.id,
        title: getItemHeadline(item),
        localPath: item.localPath,
        articleUrl: item.articleUrl?.en,
        publishedAt: item.publishedAt,
        hasChinese: item.languages?.zh || false,
      })),
      updated: changes.updated.map((item) => ({
        id: item.id,
        title: getItemHeadline(item),
        localPath: item.localPath,
        articleUrl: item.articleUrl?.en,
        publishedAt: item.publishedAt,
        hasChinese: item.languages?.zh || false,
      })),
      removed: changes.removed.map((item) => ({
        id: item.id,
        title: getItemHeadline(item),
        localPath: item.localPath,
        articleUrl: item.articleUrl?.en,
        publishedAt: item.publishedAt,
      })),
    },
  };
}

function buildLanguageSwitch(item, defaultLang = "en") {
  const zhDisabled = !item.languages?.zh;
  const enActive = defaultLang !== "zh";
  const zhActive = defaultLang === "zh" && !zhDisabled;

  return `
        <div class="story-language-switch" data-language-switch>
          <button class="language-button${enActive ? " is-active" : ""}" type="button" data-lang="en" aria-pressed="${enActive ? "true" : "false"}">EN</button>
          <button class="language-button${zhDisabled ? " is-disabled" : ""}" type="button" data-lang="zh" aria-pressed="${zhActive ? "true" : "false"}"${zhDisabled ? " disabled" : ""}>&#20013;&#25991;</button>
        </div>
  `;
}

function buildArticlePage(item, previousItem, nextItem) {
  const title = item.content.en.title || item.slug;
  const subtitle = item.content.en.subtitle || item.content.en.excerpt || "";
  const zhTitle = item.content?.zh?.title || title;
  const zhSubtitle = item.content?.zh?.subtitle || item.content?.zh?.excerpt || subtitle;
  const defaultLang = item.languages?.zh ? "zh" : "en";
  const description = makeArticleDescription(item);
  const primaryImage = item.images.primary?.url || FALLBACK_ICON;
  const publishedDisplay = formatDate(item.publishedAt);
  const category = item.category || "community";
  const author = item.author || "Bungie";
  const wordCount = item.metrics.wordCount || 0;
  const readMinutes = item.metrics.readMinutes || 1;
  const tagMarkup = [category, `${wordCount} words`, `${readMinutes} min read`]
    .filter(Boolean)
    .map((tag) => `<span>${escapeHtml(tag)}</span>`)
    .join("");

  const navLinks = [
    '<a href="/news/">返回新闻列表</a>',
    previousItem
      ? `<a href="${escapeHtml(previousItem.localPath)}">上一篇: ${escapeHtml(previousItem.content.en.title)}</a>`
      : "",
    nextItem
      ? `<a href="${escapeHtml(nextItem.localPath)}">下一篇: ${escapeHtml(nextItem.content.en.title)}</a>`
      : "",
  ]
    .filter(Boolean)
    .join("\n        ");

  const zhBodyHtml = item.content?.zh?.bodyHtml || "";

  return `---
title: ${escapeFrontMatterString(title)}
layout: false
---
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(title)} / 最新消息</title>
  <meta name="description" content="${escapeHtml(description)}">
  <link rel="icon" href="/marathon-lore/assets/images/icons/marathon-icon.jpg">
  <link rel="stylesheet" href="/news/news.css">
</head>
<body>
  <video class="page-bg-video" autoplay muted loop playsinline preload="metadata" poster="/images/marathon-home-full-poster.jpg" aria-hidden="true">
    <source src="/images/marathon-home-full-bg.mp4" type="video/mp4">
  </video>

  <main class="news-shell">
    <aside class="news-sidebar">
      <section class="brand">
        <div class="brand-mark">
          <img src="/marathon-lore/assets/images/icons/marathon-brand-mark.webp" alt="Marathon">
        </div>
        <p class="brand-title">ARTICLE</p>
      </section>

      <nav class="sidebar-nav" aria-label="站内导航">
        <a class="nav-link" href="/">返回首页</a>
        <a class="nav-link active" href="/news/">返回新闻列表</a>
        <a class="nav-link" href="/marathon-lore/">百科</a>
        <a class="nav-link" href="/about/">关于</a>
      </nav>

      <section class="meta-box">
        <span class="meta-label">原始标题</span>
        <span class="meta-value">${escapeHtml(title)}</span>
        <span class="meta-label">来源</span>
        <span class="meta-value">Bungie Marathon News</span>
        <span class="meta-label">发布时间</span>
        <span class="meta-value">${escapeHtml(publishedDisplay)} UTC</span>
      </section>

      <section class="diag-box">
        <p>[ENTRY] ${escapeHtml(item.slug)}</p>
        <p>分类: ${escapeHtml(category)}</p>
        <p>作者: ${escapeHtml(author)}</p>
      </section>
    </aside>

    <section class="news-main">
      <header class="topbar">
        <div class="topbar-group">
          <a class="chip" href="/news/">&#36820;&#22238;&#21015;&#34920;</a>
        </div>
        <div class="topbar-group topbar-actions">
${buildLanguageSwitch(item, defaultLang)}
          <a class="chip" href="${escapeHtml(item.articleUrl.en || "#")}" target="_blank" rel="noreferrer">&#26597;&#30475; Bungie &#21407;&#25991;</a>
        </div>
      </header>

      <section class="story-header">
        <span class="hero-kicker">${escapeHtml(category)}</span>
        <h1 class="story-title" data-lang-title-en="${escapeHtml(title)}" data-lang-title-zh="${escapeHtml(zhTitle)}">${escapeHtml(title)}</h1>
        <p class="story-subtitle" data-lang-subtitle-en="${escapeHtml(subtitle)}" data-lang-subtitle-zh="${escapeHtml(zhSubtitle)}">${escapeHtml(subtitle)}</p>
        <div class="story-meta">
          <span>${escapeHtml(author)}</span>
          <span>${escapeHtml(publishedDisplay)}</span>
        </div>
        <div class="story-tags">
          ${tagMarkup}
        </div>
      </section>

      <div class="story-grid">
        <section class="story-primary">
          <figure class="story-figure">
            <img src="${escapeHtml(primaryImage)}" alt="${escapeHtml(title)}">
          </figure>

          <article class="story-content">

            <div class="story-rendered"${defaultLang === "zh" ? " hidden" : ""} data-lang-panel="en">
${item.content.en.bodyHtml}
            </div>

            <div class="story-rendered"${item.languages?.zh ? "" : " hidden"} data-lang-panel="zh"${defaultLang === "zh" ? "" : item.languages?.zh ? " hidden" : ""}>
${zhBodyHtml}
            </div>
          </article>
        </section>

        <aside class="story-secondary">
          <section class="story-panel">
            <h3>文章信息</h3>
            <ul>
              <li>分类: ${escapeHtml(category)}</li>
              <li>作者: ${escapeHtml(author)}</li>
              <li>字数: ${escapeHtml(String(wordCount))}</li>
              <li>预计阅读: ${escapeHtml(String(readMinutes))} 分钟</li>
            </ul>
          </section>

          <section class="story-panel">
            <h3>正文来源</h3>
            <ul>
              <li>英文正文: Bungie 官方原文</li>
              <li>中文正文: ${item.content?.zh?.source === "official" ? "Bungie 官方简中" : item.content?.zh?.source === "generated" ? "本站翻译生成" : "当前不可用"}</li>
            </ul>
          </section>

          <section class="story-disclaimer">
            <strong>来源说明</strong>
            <p>本页内容来自 Bungie 官方 Marathon News。若存在中文正文，则优先使用 Bungie 官方简中；若 Bungie 未提供中文，则可由站内补强翻译生成。</p>
          </section>
        </aside>
      </div>

      <nav class="story-nav" aria-label="文章导航">
        ${navLinks}
      </nav>
    </section>
  </main>

  <script>
    (() => {
      const buttons = Array.from(document.querySelectorAll("[data-lang]"));
      const panels = {
        en: document.querySelector('[data-lang-panel="en"]'),
        zh: document.querySelector('[data-lang-panel="zh"]'),
      };
      const titleNode = document.querySelector("[data-lang-title-en]");
      const subtitleNode = document.querySelector("[data-lang-subtitle-en]");
      const pageTitleSuffix = " / 鏈€鏂版秷鎭?";

      function setLanguage(lang) {
        if (!panels[lang]) return;
        if (lang === "zh" && !buttons.find((button) => button.dataset.lang === "zh" && !button.disabled)) {
          return;
        }

        panels.en.hidden = lang !== "en";
        if (panels.zh) {
          panels.zh.hidden = lang !== "zh";
        }

        if (titleNode) {
          const nextTitle = lang === "zh" ? titleNode.dataset.langTitleZh : titleNode.dataset.langTitleEn;
          if (nextTitle) {
            titleNode.textContent = nextTitle;
            document.title = nextTitle + pageTitleSuffix;
          }
        }

        if (subtitleNode) {
          const nextSubtitle = lang === "zh" ? subtitleNode.dataset.langSubtitleZh : subtitleNode.dataset.langSubtitleEn;
          subtitleNode.textContent = nextSubtitle || "";
        }

        for (const button of buttons) {
          const active = button.dataset.lang === lang;
          button.classList.toggle("is-active", active);
          button.setAttribute("aria-pressed", active ? "true" : "false");
        }
      }

      for (const button of buttons) {
        button.addEventListener("click", () => {
          if (button.disabled) return;
          setLanguage(button.dataset.lang);
        });
      }

      setLanguage(${JSON.stringify(defaultLang)});
    })();
  </script>
</body>
</html>
`;
}

async function generateArticlePages(items) {
  await fs.rm(MYBLOG_ARTICLES_DIR, { recursive: true, force: true });
  await fs.mkdir(MYBLOG_ARTICLES_DIR, { recursive: true });

  const writes = items.map(async (item, index) => {
    const previousItem = index > 0 ? items[index - 1] : null;
    const nextItem = index < items.length - 1 ? items[index + 1] : null;
    const articleDir = path.join(MYBLOG_ARTICLES_DIR, item.pathSlug);
    const articleFile = path.join(articleDir, "index.html");

    await fs.mkdir(articleDir, { recursive: true });
    await fs.writeFile(
      articleFile,
      buildArticlePage(item, previousItem, nextItem),
      "utf8",
    );
  });

  await Promise.all(writes);
}

async function generateTranslatedChineseIfPossible({
  skipTranslation,
  useExistingGeneratedTranslation,
}) {
  if (skipTranslation) {
    return {
      attempted: false,
      available: false,
      generated: useExistingGeneratedTranslation
        ? await readJsonIfExists(GENERATED_ZH_PATH)
        : null,
    };
  }

  if (!process.env.OPENAI_API_KEY) {
    try {
      await execFile(
        "python",
        [TRANSLATOR_SCRIPT, "translate-bilingual-json"],
        {
          cwd: path.join(NEWS_ROOT, "trans"),
          encoding: "utf8",
          maxBuffer: 20 * 1024 * 1024,
          env: process.env,
        },
      );

      return {
        attempted: true,
        available: true,
        generated: await readJsonIfExists(GENERATED_ZH_PATH),
      };
    } catch (error) {
      const detail = String(error?.stderr || error?.stdout || error?.message || error);
      return {
        attempted: true,
        available: false,
        generated: useExistingGeneratedTranslation
          ? await readJsonIfExists(GENERATED_ZH_PATH)
          : null,
        error: detail,
      };
    }
  }

  await execFile(
    "python",
    [TRANSLATOR_SCRIPT, "translate-bilingual-json"],
    {
      cwd: path.join(NEWS_ROOT, "trans"),
      encoding: "utf8",
      maxBuffer: 20 * 1024 * 1024,
      env: process.env,
    },
  );

  return {
    attempted: true,
    available: true,
    generated: await readJsonIfExists(GENERATED_ZH_PATH),
  };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const previousData = await readJsonIfExists(BLOG_DATA_OUTPUT);

  const [english, chinese] = await Promise.all([
    fetchMarathonNews({
      limit: options.limit,
      locale: "en-us",
      includeBody: options.includeBody,
    }),
    fetchMarathonNews({
      limit: options.limit,
      locale: "zh-chs",
      includeBody: options.includeBody,
    }),
  ]);

  await writeJson(ENGLISH_OUTPUT, english, options.pretty);
  await writeJson(CHINESE_OUTPUT, chinese, options.pretty);

  const bilingualResult = await generateBilingualArtifacts();
  const bilingual = await readJsonIfExists(BILINGUAL_PATH);
  const translation = await generateTranslatedChineseIfPossible({
    skipTranslation: options.skipTranslation,
    useExistingGeneratedTranslation: options.useExistingGeneratedTranslation,
  });

  const generatedItems = Array.isArray(translation.generated?.items)
    ? translation.generated.items
    : [];
  const generatedMap = new Map(generatedItems.map((item) => [item.articlePath, item]));

  const items = Array.isArray(bilingual?.items)
    ? bilingual.items.map((item) => normalizeBlogItem(item, generatedMap))
    : [];
  const missingChineseItems = summarizeMissingChineseItems(items);

  if (missingChineseItems.length > 0 && !options.allowEnglishOnly) {
    const exampleItems = missingChineseItems
      .slice(0, 5)
      .map((item) => `${item.slug} (${item.title})`)
      .join(", ");
    const translationHint = options.skipTranslation
      ? "Translation was skipped explicitly via --skip-translation."
      : options.useExistingGeneratedTranslation
        ? "Existing generated translation artifacts did not cover every English-only article."
        : `No usable generated translation was available for every English-only article.${translation.error ? ` Translator detail: ${translation.error}` : ""}`
    const authHint = process.env.OPENAI_API_KEY
      ? "Check the translator run output and generated JSON coverage."
      : "Set OPENAI_API_KEY and OPENAI_MODEL, then rerun `npm run generate:myblog-news`."

    throw new Error(
      [
        `Bilingual coverage is incomplete: ${missingChineseItems.length} article(s) still have no Chinese body.`,
        translationHint,
        authHint,
        `Examples: ${exampleItems}`,
        "If you intentionally want English-only output for debugging, rerun with --allow-english-only.",
      ].join(" "),
    );
  }

  const blogData = {
    generatedAt: new Date().toISOString(),
    locale: "multi",
    primaryLanguage: "en",
    availableLanguages: ["en", "zh"],
    source: "bungie-contentstack",
    total: items.length,
    count: items.length,
    itemsPerPage: ITEMS_PER_PAGE,
    bilingualSummary: bilingual?.summary || null,
    translation: {
      attempted: translation.attempted,
      available: translation.available,
      generatedCount: generatedItems.length,
    },
    items,
  };

  const changes = collectChanges(previousData, items);
  const syncReport = buildSyncReport({
    fetchedAt: blogData.generatedAt,
    items,
    changes,
    bilingualSummary: bilingual?.summary,
    generatedCount: generatedItems.length,
    translationAttempted: translation.attempted,
    translationAvailable: translation.available,
  });

  await generateArticlePages(items);
  await writeJson(BLOG_DATA_OUTPUT, blogData, true);
  await writeJson(BLOG_SYNC_REPORT_OUTPUT, syncReport, true);

  const lines = [];
  lines.push(
    `Myblog news artifacts complete: ${items.length} total, `
      + `${bilingualResult.summary.bilingualCount} official bilingual, `
      + `${generatedItems.length} generated Chinese, `
      + `${changes.added.length} added, ${changes.updated.length} updated, ${changes.removed.length} removed.`,
  );
  lines.push(`Data file: ${BLOG_DATA_OUTPUT}`);
  lines.push(`Articles dir: ${MYBLOG_ARTICLES_DIR}`);
  lines.push(`Sync report: ${BLOG_SYNC_REPORT_OUTPUT}`);
  if (!translation.available && generatedItems.length === 0) {
    lines.push("Translation API unavailable in this run. Full bilingual output requires OPENAI_API_KEY and OPENAI_MODEL, unless reusable generated Chinese artifacts already exist.");
  }
  process.stdout.write(`${lines.join("\n")}\n`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
