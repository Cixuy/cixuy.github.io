# 技术博客

一个零构建依赖的静态博客：文章写 Markdown，跑一条命令生成 `docs/`，推到 GitHub 就上线。

- 纯静态 HTML，不需要 Node / 不需要 CI
- 自带深色模式、RSS、标签页、404
- 中文排版按阅读优化（行高 1.85、正文 16.5px）
- 唯一的依赖是 Python 的 `markdown` 库

## 本地跑起来

```bash
pip install markdown
python3 build.py --serve
```

打开 http://127.0.0.1:8088

改完文章重新跑 `python3 build.py` 就行，`--serve` 只是顺便起个预览服务。

推之前先跑一次体检，不用等构建完才发现文章头写错：

```bash
python3 build.py --check
```

它会校验 front-matter 必填字段（title / date / summary / tags）、`date` 是否 `YYYY-MM-DD`、
同一系列里 chapter 有没有撞号、`./posts/xxx.md` 这类站内链接是否指向真实文章。
有错返回 1，可以直接串进 pre-commit 或 CI：

```
$ python3 build.py --check
检查 18 篇文章
  · redis-outage-fallback.md: 系列《缓存与并发》没有 chapter（单篇系列可忽略）
  ✗ _tmp_bad.md: 缺少字段 date
$ echo $?
1
```

「系列没有 chapter」只提示不报错 —— 单篇自成系列是合法的；真正会让归档乱掉的是**同系列章节号重复**
和**日期格式错**（后者会让文章在归档里排到 9999 年或者永远不发布）。

`--check` 只看 Markdown 源，管不到模板拼出来的链接（上一篇/下一篇、专栏目录、标签锚点）。
改过 slug 或专栏名之后，再扫一遍产物：

```bash
python3 build.py --check-links
# 扫描 37 个页面 / 871 条站内链接
#   全部通过：站内链接与锚点都能落到真实页面
```

它按浏览器的解析规则（相对 `..` 在站点根处被吃掉）核对每个 `href`/`src` 的真实文件，
连 `tags/index.html#go-服务端` 这类锚点也一起验；有死链返回 1。

RSS 的每一条都带 `pubDate`（取自 front-matter 的 `date`），格式固定为英文 RFC 822：
`build.py` 不调 `strftime("%a/%b")`，因为中文 locale 下会输出「周四 / 十一月」，那是非法日期，
阅读器会把时间丢掉。`date` 写得解析不了时只是少一个字段，整条 feed 不会因此坏掉。

## 写一篇新文章

在 `posts/` 下新建 `你的-slug.md`，**文件名就是 URL**（比如 `redis-lock.md` → `/posts/redis-lock/`）。

头部固定这几行：

```markdown
---
title: 文章标题
date: 2026-11-20
summary: 一句话摘要，显示在列表页和 RSS
tags: Redis, 并发
draft: false
---

正文从这里开始。
```

- `draft: true` 的文章不会被生成，方便存草稿
- `posts/_模板.md` 是一份可以直接抄的结构，别删（它是 draft 状态，不会输出）
- 支持代码块、表格、引用、图片、列表（Markdown 标准语法 + fenced code）

## 三个可选字段

除了 `title` / `date` / `summary` / `tags`，还支持：

```markdown
series: 缓存与并发        # 专栏名，会显示成徽章，并自动汇总到「专栏」页
chapter: 2               # 在专栏里的章序号；不写就按日期排
series_desc: 一句话简介   # 专栏简介，同一专栏写在一处即可（取序号最靠前的那条）
updated: 2026-11-20      # 最后更新日期，和 date 不同时才显示
draft: false             # true 则不生成
```

## 专栏

同一 `series` 的文章会自动组成一个专栏：

- `/series/` 汇总所有专栏，每个专栏显示章数与最近更新时间
- `/series/<专栏名>/` 是该专栏的独立页面，按 `chapter` 排序，带简介和「从第一章开始读」
- 文章页顶部有专栏目录，当前章高亮；正文末尾自动生成上一章 / 下一章

`chapter` 不写也没关系，会按日期排并自动编号。

## 定时发布

`date` 写**未来的日期**，文章在构建时会被跳过，日期到了才出现。

```bash
python3 build.py        # 正常构建，未到期的文章不会生成
python3 build.py --all  # 本地预览，把所有未到期的文章也生成出来
```

配合 `.github/workflows/build.yml`，GitHub 每天北京时间 09:00 自动构建一次，到期的文章自己就上线了，不用管。

## 发布到 GitHub Pages

1. 在 GitHub 建一个**空仓库**，名字用 `<用户名>.github.io`（这样访问地址就是 `https://<用户名>.github.io`，没有子路径）。**README、.gitignore、License 三个勾都不要勾**，否则会和本地历史冲突
2. 仓库建好后，在本目录执行两条命令：

```bash
git remote add origin https://github.com/cixuy/cixuy.github.io.git
git push -u origin main
```

第一次 push 会弹浏览器让你授权，点一下就行 —— 不用手动生成 token，也不用输密码。

3. 仓库 → Settings → Pages → Source 选 **Deploy from a branch**，Branch 选 `main`，目录选 **`/docs`**，保存。

等一两分钟，`https://cixuy.github.io` 就能访问了。

> **注意**：`.nojekyll` 文件已经生成在 `docs/` 里了，别删。它会关掉 GitHub 的 Jekyll 处理，否则下划线开头的文件会被忽略。

如果仓库名不是 `<用户名>.github.io`（比如叫 `blog`），访问地址会变成 `https://<用户名>.github.io/blog/`。站点内部的链接全是相对路径，不用改配置 —— 但 `build.py` 里 `SITE["url"]` 要同步改，否则 RSS 里的链接是错的。

## 上线前必改

`build.py` 顶部的 `SITE` 字典（现在填的是 `cixuy`，换账号的话记得同步）：

```python
SITE = {
    "name": "Cixuy",
    "url": "https://cixuy.github.io",
    "github": "https://github.com/cixuy",
    ...
}
```

同时把 `pages/about.md` 里的 GitHub 链接换掉。

## 目录结构

```
博客/
├── build.py          生成脚本
├── posts/            文章源文件（Markdown）
│   └── _模板.md
├── pages/            独立页面（关于等）
├── assets/           样式和脚本
└── docs/             生成产物，这个目录才是要部署的
```

**只改 `posts/`、`pages/`、`assets/` 和 `build.py`，不要手改 `docs/`** —— 每次构建都会整个删掉重建。
