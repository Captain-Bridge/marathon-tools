import path from "node:path";
import { writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { fetchMarathonNews } from "./fetchMarathonNews.js";
import { generateBilingualArtifacts } from "./buildBilingualMarathonNews.js";

const DEFAULT_LIMIT = 200;
const OUTPUT_FILES = {
  enUs: "sample-marathon-news-en-us.json",
  zhChs: "sample-marathon-news-zh-chs-full.json",
};

function parseArgs(argv) {
  const options = {
    limit: DEFAULT_LIMIT,
    includeBody: true,
    pretty: true,
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

    if (/^\d+$/.test(arg)) {
      options.limit = Number.parseInt(arg, 10);
    }
  }

  if (!Number.isInteger(options.limit) || options.limit <= 0) {
    throw new Error("--limit must be a positive integer");
  }

  return options;
}

async function writeJson(fileName, payload, pretty) {
  const outputPath = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "..",
    fileName,
  );
  const spacing = pretty ? 2 : 0;
  await writeFile(outputPath, `${JSON.stringify(payload, null, spacing)}\n`, "utf8");
  return outputPath;
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
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

  const englishPath = await writeJson(OUTPUT_FILES.enUs, english, options.pretty);
  const chinesePath = await writeJson(OUTPUT_FILES.zhChs, chinese, options.pretty);
  const { outputPath, alignmentOutputPath, summary } =
    await generateBilingualArtifacts();

  process.stdout.write(
    [
      englishPath,
      chinesePath,
      outputPath,
      alignmentOutputPath,
      JSON.stringify(summary),
    ].join("\n") + "\n",
  );
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
