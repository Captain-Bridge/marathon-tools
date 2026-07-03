import path from "node:path";
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const ENGLISH_FILE = "sample-marathon-news-en-us.json";
const CHINESE_FILE = "sample-marathon-news-zh-chs-full.json";
const OUTPUT_FILE = "sample-marathon-news-bilingual.json";
const ALIGNMENT_OUTPUT_FILE = "sample-marathon-news-translation-alignment.json";

async function readJson(fileName) {
  const baseDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const filePath = path.join(baseDir, fileName);
  const buffer = await readFile(filePath);
  let content;

  if (buffer.length >= 2 && buffer[0] === 0xff && buffer[1] === 0xfe) {
    content = buffer.toString("utf16le");
  } else if (buffer.length >= 2 && buffer[0] === 0xfe && buffer[1] === 0xff) {
    const swapped = Buffer.from(buffer);
    for (let index = 0; index + 1 < swapped.length; index += 2) {
      const left = swapped[index];
      swapped[index] = swapped[index + 1];
      swapped[index + 1] = left;
    }
    content = swapped.toString("utf16le");
  } else {
    content = buffer.toString("utf8");
  }

  if (content.charCodeAt(0) === 0xfeff) {
    content = content.slice(1);
  }

  return JSON.parse(content);
}

function byPublishedAtDesc(left, right) {
  const leftTime = new Date(left.publishedAt || 0).getTime();
  const rightTime = new Date(right.publishedAt || 0).getTime();
  return rightTime - leftTime;
}

function buildMergedItems(englishItems, chineseItems) {
  const chineseByPath = new Map(chineseItems.map((item) => [item.articlePath, item]));
  const chineseById = new Map(chineseItems.map((item) => [item.id, item]));
  const usedChinesePaths = new Set();
  const mergedItems = [];

  for (const enUs of englishItems) {
    let zhChs = chineseByPath.get(enUs.articlePath) || null;
    let matchedBy = "articlePath";

    if (!zhChs && enUs.id && chineseById.has(enUs.id)) {
      zhChs = chineseById.get(enUs.id);
      matchedBy = "id";
    }

    if (zhChs) {
      usedChinesePaths.add(zhChs.articlePath);
    }

    const primary = zhChs || enUs;
    mergedItems.push({
      contentId: enUs.id || zhChs?.id || null,
      articlePath: enUs.articlePath,
      paths: {
        enUs: enUs.articlePath,
        zhChs: zhChs?.articlePath || null,
      },
      articleUrl: {
        enUs: enUs.articleUrl,
        zhChs: zhChs?.articleUrl || null,
      },
      publishedAt: primary?.publishedAt || null,
      category: primary?.category || null,
      matchType: zhChs ? "bilingual" : "en-only",
      matchedBy: zhChs ? matchedBy : null,
      enUs,
      zhChs,
    });
  }

  for (const zhChs of chineseItems) {
    if (usedChinesePaths.has(zhChs.articlePath)) {
      continue;
    }

    mergedItems.push({
      contentId: zhChs.id || null,
      articlePath: zhChs.articlePath,
      paths: {
        enUs: null,
        zhChs: zhChs.articlePath,
      },
      articleUrl: {
        enUs: null,
        zhChs: zhChs.articleUrl,
      },
      publishedAt: zhChs.publishedAt || null,
      category: zhChs.category || null,
      matchType: "zh-only",
      matchedBy: null,
      enUs: null,
      zhChs,
    });
  }

  return mergedItems.sort(byPublishedAtDesc);
}

function summarize(items) {
  const bilingualCount = items.filter((item) => item.matchType === "bilingual").length;
  const englishOnly = items
    .filter((item) => item.matchType === "en-only")
    .map((item) => ({
      articlePath: item.articlePath,
      title: item.enUs?.title || null,
      publishedAt: item.publishedAt,
      articleUrl: item.articleUrl.enUs,
    }));
  const chineseOnly = items
    .filter((item) => item.matchType === "zh-only")
    .map((item) => ({
      articlePath: item.articlePath,
      title: item.zhChs?.title || null,
      publishedAt: item.publishedAt,
      articleUrl: item.articleUrl.zhChs,
    }));

  return {
    bilingualCount,
    englishOnlyCount: englishOnly.length,
    chineseOnlyCount: chineseOnly.length,
    englishOnly,
    chineseOnly,
  };
}

function buildAlignmentPairs(items) {
  return items
    .filter((item) => item.enUs && item.zhChs)
    .map((item) => ({
      contentId: item.contentId,
      matchedBy: item.matchedBy,
      publishedAt: item.publishedAt,
      category: item.category,
      paths: item.paths,
      articleUrl: item.articleUrl,
      title: {
        enUs: item.enUs.title || "",
        zhChs: item.zhChs.title || "",
      },
      subtitle: {
        enUs: item.enUs.subtitle || "",
        zhChs: item.zhChs.subtitle || "",
      },
      bodyText: {
        enUs: item.enUs.bodyText || "",
        zhChs: item.zhChs.bodyText || "",
      },
      bodyHtml: {
        enUs: item.enUs.bodyHtml || "",
        zhChs: item.zhChs.bodyHtml || "",
      },
    }));
}

async function generateBilingualArtifacts() {
  const english = await readJson(ENGLISH_FILE);
  const chinese = await readJson(CHINESE_FILE);
  const items = buildMergedItems(english.items || [], chinese.items || []);
  const summary = summarize(items);
  const alignmentPairs = buildAlignmentPairs(items);
  const output = {
    source: "bungie-contentstack-bilingual",
    generatedAt: new Date().toISOString(),
    locales: ["en-us", "zh-chs"],
    input: {
      english: {
        file: ENGLISH_FILE,
        total: english.total,
        count: english.count,
        fetchedAt: english.fetchedAt,
      },
      chinese: {
        file: CHINESE_FILE,
        total: chinese.total,
        count: chinese.count,
        fetchedAt: chinese.fetchedAt,
      },
    },
    summary,
    items,
  };
  const alignmentOutput = {
    source: "bungie-contentstack-translation-alignment",
    generatedAt: output.generatedAt,
    locales: output.locales,
    bilingualCount: alignmentPairs.length,
    pairs: alignmentPairs,
  };

  const baseOutputDir = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "..",
  );
  const outputPath = path.join(baseOutputDir, OUTPUT_FILE);
  const alignmentOutputPath = path.join(baseOutputDir, ALIGNMENT_OUTPUT_FILE);

  await writeFile(outputPath, `${JSON.stringify(output, null, 2)}\n`, "utf8");
  await writeFile(
    alignmentOutputPath,
    `${JSON.stringify(alignmentOutput, null, 2)}\n`,
    "utf8",
  );

  return {
    outputPath,
    alignmentOutputPath,
    summary,
    bilingualCount: alignmentPairs.length,
  };
}

export { generateBilingualArtifacts };

const isDirectRun =
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isDirectRun) {
  generateBilingualArtifacts()
    .then(({ outputPath, alignmentOutputPath }) => {
      process.stdout.write(`${outputPath}\n${alignmentOutputPath}\n`);
    })
    .catch((error) => {
      console.error(error.message);
      process.exitCode = 1;
    });
}
