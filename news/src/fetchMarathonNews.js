import path from "node:path";
import { fileURLToPath } from "node:url";

const CONTENTSTACK_URL =
  "https://graphql.contentstack.com/stacks/blte410e3b15535c144?environment=live";
const CONTENTSTACK_HEADERS = {
  access_token: "cs7929311353379d90697fc0b6",
  "Content-Type": "application/json",
  "User-Agent": "Mozilla/5.0",
};

const DEFAULT_LIMIT = 20;
const DEFAULT_LOCALE = "en-us";
const BASE_ARTICLE_URL = "https://www.bungie.net/7";

function parseArgs(argv) {
  const options = {
    limit: DEFAULT_LIMIT,
    locale: DEFAULT_LOCALE,
    includeBody: true,
    pretty: true,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];

    if (arg === "--limit" && argv[i + 1]) {
      options.limit = Number.parseInt(argv[i + 1], 10);
      i += 1;
      continue;
    }

    if (arg === "--locale" && argv[i + 1]) {
      options.locale = argv[i + 1];
      i += 1;
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

async function contentstackQuery(query, variables) {
  const response = await fetch(CONTENTSTACK_URL, {
    method: "POST",
    headers: CONTENTSTACK_HEADERS,
    body: JSON.stringify({ query, variables }),
  });

  if (!response.ok) {
    throw new Error(`ContentStack request failed with status ${response.status}`);
  }

  const payload = await response.json();

  if (payload.errors?.length) {
    const message = payload.errors.map((error) => error.message).join("; ");
    throw new Error(`ContentStack query failed: ${message}`);
  }

  return payload.data;
}

function buildArticleUrl(hostedUrl, locale) {
  const slug = (hostedUrl || "").replace(/^\/+/, "");
  return `${BASE_ARTICLE_URL}/${locale}/News/Article/${slug}`;
}

function stripHtml(html) {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/<style[\s\S]*?<\/style>/gi, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/\s+/g, " ")
    .trim();
}

function extractInlineImages(html) {
  const matches = [...html.matchAll(/<img\b[^>]*src="([^"]+)"[^>]*>/gi)];
  return matches.map((match) => match[1]).filter(Boolean);
}

function mapAssetConnection(connection) {
  const edges = connection?.edges || [];
  return edges
    .map((edge) => edge?.node)
    .filter(Boolean)
    .map((node) => ({
      title: node.title,
      url: node.url,
      permanentUrl: node.permanent_url,
      filename: node.filename,
      contentType: node.content_type,
      description: node.description,
      dimension: node.dimension,
    }));
}

async function fetchMarathonNews({ limit, locale, includeBody }) {
  const query = `
    query MarathonNews($limit: Int!, $locale: String!) {
      all_news_article(
        limit: $limit
        locale: $locale
        order_by: [updated_at_DESC]
        where: { taxonomies: { game: { term: "marathon" } } }
      ) {
        total
        items {
          title
          date
          subtitle
          author
          category
          imageConnection {
            edges {
              node {
                title
                url
                permanent_url
                filename
                content_type
                description
                dimension {
                  width
                  height
                }
              }
            }
          }
          mobile_imageConnection {
            edges {
              node {
                title
                url
                permanent_url
                filename
                content_type
                description
                dimension {
                  width
                  height
                }
              }
            }
          }
          banner_imageConnection {
            edges {
              node {
                title
                url
                permanent_url
                filename
                content_type
                description
                dimension {
                  width
                  height
                }
              }
            }
          }
          html_content
          url {
            hosted_url
          }
          system {
            uid
          }
          taxonomies {
            ... on Taxonomy {
              taxonomy_uid
              term_uid
            }
          }
        }
      }
    }
  `;

  let data;

  try {
    data = await contentstackQuery(query, { limit, locale });
  } catch (error) {
    if (!String(error.message).includes("order")) {
      throw error;
    }

    const fallbackQuery = `
      query MarathonNews($limit: Int!, $locale: String!) {
        all_news_article(
          limit: $limit
          locale: $locale
          where: { taxonomies: { game: { term: "marathon" } } }
        ) {
          total
          items {
            title
            date
            subtitle
            author
            category
            imageConnection {
              edges {
                node {
                  title
                  url
                  permanent_url
                  filename
                  content_type
                  description
                  dimension {
                    width
                    height
                  }
                }
              }
            }
            mobile_imageConnection {
              edges {
                node {
                  title
                  url
                  permanent_url
                  filename
                  content_type
                  description
                  dimension {
                    width
                    height
                  }
                }
              }
            }
            banner_imageConnection {
              edges {
                node {
                  title
                  url
                  permanent_url
                  filename
                  content_type
                  description
                  dimension {
                    width
                    height
                  }
                }
              }
            }
            html_content
            url {
              hosted_url
            }
            system {
              uid
            }
            taxonomies {
              ... on Taxonomy {
                taxonomy_uid
                term_uid
              }
            }
          }
        }
      }
    `;

    data = await contentstackQuery(fallbackQuery, { limit, locale });
  }

  const items = data.all_news_article.items
    .map((item) => {
      const bodyHtml = item.html_content || "";
      const coverImages = mapAssetConnection(item.imageConnection);
      const mobileImages = mapAssetConnection(item.mobile_imageConnection);
      const bannerImages = mapAssetConnection(item.banner_imageConnection);
      return {
        id: item.system.uid,
        title: item.title,
        subtitle: item.subtitle,
        author: item.author,
        category: item.category,
        publishedAt: item.date,
        articlePath: item.url.hosted_url,
        articleUrl: buildArticleUrl(item.url.hosted_url, locale),
        taxonomies: item.taxonomies,
        coverImages,
        mobileImages,
        bannerImages,
        inlineImageUrls: extractInlineImages(bodyHtml),
        bodyHtml: includeBody ? bodyHtml : undefined,
        bodyText: includeBody ? stripHtml(bodyHtml) : undefined,
      };
    })
    .sort((a, b) => new Date(b.publishedAt) - new Date(a.publishedAt));

  return {
    source: "bungie-contentstack",
    fetchedAt: new Date().toISOString(),
    locale,
    total: data.all_news_article.total,
    count: items.length,
    items,
  };
}

export { fetchMarathonNews, parseArgs };

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const result = await fetchMarathonNews(options);
  const spacing = options.pretty ? 2 : 0;
  process.stdout.write(`${JSON.stringify(result, null, spacing)}\n`);
}

const isDirectRun =
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isDirectRun) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
