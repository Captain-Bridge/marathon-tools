import path from "node:path";
import { writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { fetchMarathonNews, parseArgs } from "./fetchMarathonNews.js";

const OUTPUT_FILE = "sample-marathon-news-zh-chs.json";

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const result = await fetchMarathonNews({
    ...options,
    locale: "zh-chs",
  });

  const outputPath = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "..",
    OUTPUT_FILE,
  );

  const spacing = options.pretty ? 2 : 0;
  await writeFile(outputPath, `${JSON.stringify(result, null, spacing)}\n`, "utf8");
  process.stdout.write(`${outputPath}\n`);
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
