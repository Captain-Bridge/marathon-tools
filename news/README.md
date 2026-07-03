# Marathon News 抓取

这个目录包含用于抓取 Bungie Marathon News 结构化内容的脚本。

数据源不是页面 HTML，而是 Bungie 站点背后的公开 ContentStack GraphQL 内容源。当前按 `taxonomy_uid=game` 和 `term_uid=marathon` 做精确筛选，所以不会混入 Destiny 新闻。

## 用法

```bash
npm run fetch:marathon-news
npm run fetch:marathon-news:zh-chs
npm run generate:marathon-news:all
```

可选参数：

```bash
node src/fetchMarathonNews.js --limit 10
node src/fetchMarathonNews.js --limit 5 --no-body
node src/fetchMarathonNews.js --limit 5 --compact
node src/fetchMarathonNews.js --limit 10 --locale en-us
node src/fetchMarathonNewsZhChs.js --limit 10
node src/generateMarathonNewsArtifacts.js --limit 200
```

其中：

- `src/fetchMarathonNews.js` 是通用抓取脚本，可通过 `--locale` 指定语言。
- `src/fetchMarathonNewsZhChs.js` 固定抓取 `https://www.bungie.net/7/zh-chs/News/Marathon` 对应的中文内容。
- 中文脚本会将结果写入工作区根目录下的 `sample-marathon-news-zh-chs.json`。
- `src/generateMarathonNewsArtifacts.js` 会一键生成英文全量、中文全量、中英对照、翻译对齐四份 JSON。

## 一键生成

```bash
npm run generate:marathon-news:all
```

默认会输出以下文件：

- `sample-marathon-news-en-us.json`
- `sample-marathon-news-zh-chs-full.json`
- `sample-marathon-news-bilingual.json`
- `sample-marathon-news-translation-alignment.json`

可选参数：

```bash
node src/generateMarathonNewsArtifacts.js --limit 200
node src/generateMarathonNewsArtifacts.js --no-body
node src/generateMarathonNewsArtifacts.js --compact
```

## 输出字段

- `id`: Bungie 内容 ID
- `title`: 新闻标题
- `subtitle`: 副标题
- `author`: 作者
- `category`: 分类
- `publishedAt`: 发布时间
- `articlePath`: Bungie 内容路径 slug
- `articleUrl`: 可直接打开的 Bungie 详情页 URL
- `taxonomies`: 原始 taxonomy 信息
- `coverImages`: 封面图资源
- `mobileImages`: 移动端图资源
- `bannerImages`: 横幅图资源
- `inlineImageUrls`: 正文中的内联图片 URL
- `bodyHtml`: HTML 正文
- `bodyText`: 去标签后的纯文本正文

## 说明

- 详情页 URL 规则为 `https://www.bungie.net/7/<locale>/News/Article/<slug>`。
- 脚本默认会按发布时间倒序输出。
- 如果 Bungie 后面调整了 GraphQL 排序枚举，脚本会自动回退到无服务端排序，再在本地按发布时间排序。
