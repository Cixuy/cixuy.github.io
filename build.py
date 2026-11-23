#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
极简静态博客生成器（零外部依赖，仅需 pip install markdown）

用法:
    python3 build.py          # 生成到 docs/
    python3 build.py --serve  # 生成后起本地预览 http://127.0.0.1:8088

文章写在 posts/*.md，文件名即 URL slug。
"""

import os
import re
import sys
import html
import shutil
import datetime
from pathlib import Path

import markdown

ROOT = Path(__file__).parent
POSTS_DIR = ROOT / "posts"
ASSETS_DIR = ROOT / "assets"
PAGES_DIR = ROOT / "pages"
OUT = ROOT / "docs"

SITE = {
    "name": "Cixuy",
    "title": "Cixuy",
    "author": "Cixuy",
    "desc": "写那些藏在底下的东西。Java 后端、缓存与并发、存储选型，以及 AI 应用落地。",
    "url": "https://cixuy.github.io",
    "github": "https://github.com/cixuy",
    "email": "2515889741@qq.com",
    "school": "Java 后端 · 2027 届",
}

SHOW_FUTURE = "--all" in sys.argv   # 本地预览未到期的定时文章

MD = markdown.Markdown(extensions=["fenced_code", "tables", "sane_lists", "attr_list"])


# ---------- 工具 ----------

def parse_front_matter(text):
    meta, body = {}, text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            raw, body = parts[1], parts[2]
            for line in raw.strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, body.lstrip("\n")


def reading_time(body):
    cn = len(re.findall(r"[\u4e00-\u9fff]", body))
    en = len(re.findall(r"\b[a-zA-Z]{2,}\b", body))
    minutes = max(1, round((cn + en * 1.6) / 380))
    return minutes


def fmt_date(s):
    try:
        return datetime.date.fromisoformat(s).strftime("%Y 年 %-m 月 %-d 日")
    except Exception:
        try:
            d = datetime.date.fromisoformat(s)
            return f"{d.year} 年 {d.month} 月 {d.day} 日"
        except Exception:
            return s


def slugify_tag(t):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", t.lower()).strip("-") or "tag"


def esc(s):
    return html.escape(s, quote=True)


# ---------- 模板 ----------

def page(title, body, prefix="", extra_head=""):
    site_title = SITE["title"] if title == SITE["title"] else f"{title} · {SITE['title']}"
    return f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(site_title)}</title>
<meta name="description" content="{esc(SITE['desc'])}">
<meta property="og:title" content="{esc(site_title)}">
<meta property="og:description" content="{esc(SITE['desc'])}">
<meta property="og:type" content="website">
<link rel="stylesheet" href="{prefix}assets/style.css">
<link rel="alternate" type="application/rss+xml" title="{esc(SITE['title'])}" href="{prefix}feed.xml">
<script>
(function() {{
  try {{
    var t = localStorage.getItem('theme');
    if (t) document.documentElement.setAttribute('data-theme', t);
    else if (window.matchMedia('(prefers-color-scheme: dark)').matches)
      document.documentElement.setAttribute('data-theme', 'dark');
  }} catch (e) {{}}
}})();
</script>
{extra_head}</head>
<body>
<header class="site-header">
  <div class="wrap">
    <a class="brand" href="{prefix}index.html">{esc(SITE['name'])}</a>
    <nav class="nav">
      <a href="{prefix}index.html">文章</a>
      <a href="{prefix}tags/index.html">标签</a>
      <a href="{prefix}series/index.html">专栏</a>
      <a href="{prefix}about/index.html">关于</a>
      <a href="{prefix}feed.xml">RSS</a>
      <button class="theme-toggle" id="themeToggle" aria-label="切换深色模式" title="切换深色模式">◐</button>
    </nav>
  </div>
</header>
<main class="wrap">
{body}
</main>
<footer class="site-footer">
  <div class="wrap">
    <span>© {datetime.date.today().year} {esc(SITE['author'])}</span>
    <span><a href="{prefix}feed.xml">RSS</a> · 用 Markdown 写作，一条命令生成</span>
  </div>
</footer>
<script src="{prefix}assets/app.js"></script>
</body>
</html>
"""


def post_url(p):
    return f"posts/{p['slug']}/index.html"


def series_slug(name):
    return slugify_tag(name)


def series_chapters(posts, name):
    """同一专栏的章节，先按 chapter 序号，没写序号的按日期排到最后"""
    same = [p for p in posts if p["series"] == name]
    return sorted(same, key=lambda x: (x["chapter"] or 999, x["date"]))


def series_desc(posts, name):
    for p in series_chapters(posts, name):
        if p.get("series_desc"):
            return p["series_desc"]
    return ""


# ---------- 生成 ----------

def load_posts():
    posts = []
    if not POSTS_DIR.exists():
        return posts
    today = "9999-12-31" if SHOW_FUTURE else datetime.date.today().isoformat()
    for f in sorted(POSTS_DIR.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        meta, body = parse_front_matter(text)
        if meta.get("draft", "").lower() in ("true", "yes", "1"):
            continue
        date = meta.get("date", "2026-01-01")
        if date > today:          # 定时发布：日期未到的文章暂不生成
            continue
        MD.reset()
        content = MD.convert(body)
        tags = [t.strip() for t in re.split(r"[,，]", meta.get("tags", "")) if t.strip()]
        posts.append({
            "slug": f.stem,
            "title": meta.get("title", f.stem),
            "date": date,
            "updated": meta.get("updated", "") or date,
            "series": meta.get("series", ""),
            "chapter": int(meta.get("chapter") or 0),
            "series_desc": meta.get("series_desc", ""),
            "summary": meta.get("summary", ""),
            "tags": tags,
            "content": content,
            "minutes": reading_time(body),
        })
    posts.sort(key=lambda x: x["date"], reverse=True)
    return posts


def build_index(posts):
    if posts:
        items = []
        for p in posts:
            tags_html = "".join(
                f'<a class="tag" href="tags/index.html#{slugify_tag(t)}">{esc(t)}</a>' for t in p["tags"]
            )
            meta_extra = ""
            if p["updated"] != p["date"]:
                meta_extra = f'<div class="post-meta">更新于 {fmt_date(p["updated"])}</div>'
            items.append(f"""    <li class="post-item">
      <time datetime="{p['date']}">{fmt_date(p['date'])}</time>
      <h2><a href="{post_url(p)}">{esc(p['title'])}</a></h2>
      <p>{esc(p['summary'])}</p>
      {meta_extra}
      <div class="tags">{tags_html}</div>
    </li>""")
        list_html = '<ul class="post-list">\n' + "\n".join(items) + "\n    </ul>"
    else:
        list_html = '<p class="empty">还没有文章，在 posts/ 下新建一个 .md 就开始写。</p>'

    body = f"""  <section class="hero">
    <h1>{esc(SITE['name'])}</h1>
    <p class="lede">{esc(SITE['desc'])}</p>
    <div class="meta-line">
      <span>{esc(SITE['author'])}</span>
      <span>{esc(SITE['school'])}</span>
      <span><a href="{SITE['github']}">GitHub</a></span>
      <span><a href="mailto:{SITE['email']}">{esc(SITE['email'])}</a></span>
    </div>
  </section>
  <section>
    <p class="section-label">文章 · {len(posts)} 篇</p>
{list_html}
  </section>"""
    return page(SITE["title"], body)


def build_post(p, posts):
    idx = posts.index(p)
    prev_p = posts[idx + 1] if idx + 1 < len(posts) else None
    next_p = posts[idx - 1] if idx - 1 >= 0 else None
    links = []
    if prev_p:
        links.append(f'<a href="../../{post_url(prev_p)}">← {esc(prev_p["title"])}</a>')
    if next_p:
        links.append(f'<a href="../../{post_url(next_p)}">下一篇：{esc(next_p["title"])} →</a>')

    tags_html = "".join(
        f'<a class="tag" href="../../tags/index.html#{slugify_tag(t)}">{esc(t)}</a>' for t in p["tags"]
    )
    series_html = ""
    series_box = ""
    chapter_nav = ""
    if p["series"]:
        chapters = series_chapters(posts, p["series"])
        home = f'../../series/{series_slug(p["series"])}/index.html'
        series_html = (
            f'<a class="tag tag-series" href="{home}">'
            f'专栏 · {esc(p["series"])}（{len(chapters)} 章）</a>'
        )

        # 目录：当前章加粗，未发布的章节不会出现在 posts 里，所以这里永远是真实可点的
        items = []
        for c in chapters:
            cur = ' class="cur"' if c["slug"] == p["slug"] else ""
            num = f'<span class="ch-num">{c["chapter"] or chapters.index(c) + 1:02d}</span>'
            items.append(f'<li{cur}>{num}<a href="../../{post_url(c)}">{esc(c["title"])}</a></li>')

        idx_c = chapters.index(p)
        prev_c = chapters[idx_c - 1] if idx_c > 0 else None
        next_c = chapters[idx_c + 1] if idx_c + 1 < len(chapters) else None
        nav = []
        if prev_c:
            nav.append(f'<a class="ch-prev" href="../../{post_url(prev_c)}">'
                       f'<span>上一章</span>{esc(prev_c["title"])}</a>')
        if next_c:
            nav.append(f'<a class="ch-next" href="../../{post_url(next_c)}">'
                       f'<span>下一章</span>{esc(next_c["title"])}</a>')

        series_box = f"""    <nav class="series-box">
      <p class="series-box-head"><a href="{home}">{esc(p["series"])}</a>
        <span>第 {idx_c + 1} / {len(chapters)} 章</span></p>
      <ol class="chapter-list">{"".join(items)}</ol>
    </nav>"""
        if nav:
            chapter_nav = f'    <div class="chapter-nav">{"".join(nav)}</div>\n'


    body = f"""  <article class="article">
    <header class="article-head">
      <h1>{esc(p['title'])}</h1>
      <div class="meta-line">
        <span>创建 {fmt_date(p['date'])}</span>
        <span>更新 {fmt_date(p['updated'])}</span>
        <span>约 {p['minutes']} 分钟</span>
        {series_html}
        <span class="tags">{tags_html}</span>
      </div>
    </header>
{series_box}
    <div class="prose">
{p['content']}
    </div>
{chapter_nav}    <footer class="article-foot">
      <div>{" ".join(links)}</div>
      <div><a href="../../index.html">返回文章列表</a></div>
    </footer>
  </article>"""
    return page(p["title"], body, prefix="../../")


def build_tags(posts):
    groups = {}
    for p in posts:
        for t in p["tags"]:
            groups.setdefault(t, []).append(p)
    if not groups:
        return page("标签", '  <p class="empty">还没有标签。</p>')
    blocks = []
    for t in sorted(groups, key=lambda x: (-len(groups[x]), x)):
        lis = "".join(
            f'<li><a href="../../{post_url(p)}">{esc(p["title"])}</a> '
            f'<span style="color:var(--faint);font-size:13px">{fmt_date(p["date"])}</span></li>'
            for p in groups[t]
        )
        blocks.append(f"""    <section id="{slugify_tag(t)}" style="margin-bottom:36px">
      <p class="section-label">{esc(t)} · {len(groups[t])} 篇</p>
      <ul class="prose" style="font-size:15px">{lis}</ul>
    </section>""")
    body = '  <section class="hero" style="padding-bottom:16px"><h1 style="font-size:24px">标签</h1></section>\n' + "\n".join(blocks)
    return page("标签", body, prefix="../../")


def build_series(posts):
    groups = {}
    for p in posts:
        if p["series"]:
            groups.setdefault(p["series"], []).append(p)
    if not groups:
        return page("系列", '  <p class="empty">还没有系列。</p>', prefix="../../")

    cards = []
    for s in sorted(groups, key=lambda x: (-len(groups[x]), x)):
        chapters = series_chapters(posts, s)
        desc = series_desc(posts, s)
        last = chapters[-1]["date"] if chapters else ""
        items = "".join(
            f'<li><span class="ch-num">{c["chapter"] or i:02d}</span>'
            f'<a href="../../{post_url(c)}">{esc(c["title"])}</a></li>'
            for i, c in enumerate(chapters[:4], 1)
        )
        more = f'<p class="col-more">还有 {len(chapters) - 4} 章</p>' if len(chapters) > 4 else ""
        cards.append(f"""    <section class="column-card" id="{series_slug(s)}">
      <p class="section-label">专栏 · {len(chapters)} 章{(" · 最近更新 " + fmt_date(last)) if last else ""}</p>
      <h2><a href="../../series/{series_slug(s)}/index.html">{esc(s)}</a></h2>
      {f'<p class="col-desc">{esc(desc)}</p>' if desc else ''}
      <ol class="chapter-list">{items}</ol>
      {more}
      <p class="col-enter"><a href="../../series/{series_slug(s)}/index.html">进入专栏 →</a></p>
    </section>""")

    body = ('  <section class="hero" style="padding-bottom:16px">'
            '<h1 style="font-size:24px">专栏</h1>'
            '<p class="lede">按主题成组写的长系列，一章一个问题，按顺序读。</p></section>\n'
            + "\n".join(cards))
    return page("专栏", body, prefix="../../")


def build_series_detail(posts, name):
    chapters = series_chapters(posts, name)
    desc = series_desc(posts, name)
    first = chapters[0] if chapters else None

    lis = []
    for i, c in enumerate(chapters, 1):
        lis.append(f"""      <li class="col-item">
        <div class="col-item-head">
          <span class="ch-num">{c["chapter"] or i:02d}</span>
          <h3><a href="../../{post_url(c)}">{esc(c["title"])}</a></h3>
          <span class="col-date">{fmt_date(c["date"])}</span>
        </div>
        <p class="col-sum">{esc(c["summary"])}</p>
        <p class="col-meta">约 {c["minutes"]} 分钟</p>
      </li>""")

    body = f"""  <article class="article column">
    <header class="article-head">
      <p class="section-label">专栏 · {len(chapters)} 章</p>
      <h1>{esc(name)}</h1>
      {f'<p class="lede">{esc(desc)}</p>' if desc else ''}
      {f'<p class="col-start"><a href="../../{post_url(first)}">从第一章开始读 →</a></p>' if first else ''}
    </header>
    <ol class="col-list">
{"".join(lis)}
    </ol>
    <footer class="article-foot">
      <div><a href="../../series/index.html">所有专栏</a></div>
    </footer>
  </article>"""
    return page(name, body, prefix="../../")


def build_page(md_file):
    text = md_file.read_text(encoding="utf-8")
    meta, body_md = parse_front_matter(text)
    MD.reset()
    body = f"""  <article class="article">
    <header class="article-head">
      <h1>{esc(meta.get('title', md_file.stem))}</h1>
    </header>
    <div class="prose">
{MD.convert(body_md)}
    </div>
  </article>"""
    return page(meta.get("title", md_file.stem), body, prefix="../../")


def build_feed(posts):
    now = datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")
    items = []
    for p in posts:
        items.append(f"""    <item>
      <title>{esc(p['title'])}</title>
      <link>{SITE['url']}/{post_url(p)}</link>
      <guid>{SITE['url']}/{post_url(p)}</guid>
      <description>{esc(p['summary'])}</description>
    </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>{esc(SITE['title'])}</title>
    <link>{SITE['url']}/</link>
    <description>{esc(SITE['desc'])}</description>
    <language>zh-CN</language>
    <lastBuildDate>{now}</lastBuildDate>
{"".join(items)}
  </channel>
</rss>
"""


def build_404(prefix=""):
    body = """  <section class="hero">
    <h1>页面走丢了</h1>
    <p class="lede">这个地址没有对应内容，可能是链接过期或者手滑打错。</p>
    <p><a href="index.html">回到文章列表</a></p>
  </section>"""
    return page("404", body)


def check_posts():
    """只做体检、不生成站点：校验 front-matter 必填项、日期格式、系列章节唯一性、站内链接。

    写作时最容易犯的错是复制上一篇的 front-matter 忘了改 date/chapter，
    生成出来的站点看着没问题，但归档顺序和系列目录就乱了。跑一次 --check 立刻暴露。
    """
    problems, warns = [], []
    if not POSTS_DIR.exists():
        print(f"找不到文章目录 {POSTS_DIR}")
        return 1
    files = sorted(POSTS_DIR.glob("*.md"))
    seen_date_slug = {}
    series_ch = {}
    required = ("title", "date", "summary", "tags")
    for f in files:
        meta, body = parse_front_matter(f.read_text(encoding="utf-8"))
        if not meta:
            problems.append(f"{f.name}: 缺少 front-matter（开头要有 --- 包裹的头部）")
            continue
        for k in required:
            if not meta.get(k):
                problems.append(f"{f.name}: 缺少字段 {k}")
        date = meta.get("date", "")
        if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            problems.append(f"{f.name}: date 格式应为 YYYY-MM-DD，当前 {date!r}")
        key = (date, f.stem)
        if key in seen_date_slug:
            problems.append(f"{f.name}: 与 {seen_date_slug[key]} 同日期同 slug")
        seen_date_slug[key] = f.name
        series, ch = meta.get("series", ""), meta.get("chapter", "")
        if series:
            if not ch:
                # 单篇成系列是允许的，只提示不报错
                warns.append(f"{f.name}: 系列《{series}》没有 chapter（单篇系列可忽略）")
            elif not str(ch).isdigit():
                problems.append(f"{f.name}: chapter 必须是数字，当前 {ch!r}")
            else:
                k2 = (series, int(ch))
                if k2 in series_ch:
                    problems.append(f"{f.name}: 系列《{series}》第 {ch} 章与 {series_ch[k2]} 重复")
                series_ch[k2] = f.name
        # 站内链接：md 里指向 ./xxx 或 posts/xxx 的目标应存在
        for m in re.finditer(r"\]\((\.?\.?/?posts/[^)#]+\.md)", body):
            target = POSTS_DIR / Path(m.group(1)).name
            if not target.exists():
                problems.append(f"{f.name}: 站内链接指向不存在的文章 {m.group(1)}")

    print(f"检查 {len(files)} 篇文章")
    for w in warns:
        print("  · " + w)
    if problems:
        for x in problems:
            print("  ✗ " + x)
        return 1
    print("  全部通过：必填字段、日期格式、系列章节、站内链接")
    return 0


def check_site_links():
    """扫 docs/ 里已生成的 HTML，校验每个站内 href/src 的文件与 #锚点 是否真实存在。

    --check 只看 Markdown 源，管不到模板拼出来的链接（上一篇/下一篇、专栏目录、
    标签锚点）。改过 slug 或专栏名之后，残留的死链只有扫成品才看得见。
    """
    if not OUT.exists():
        print("还没有生成站点，先跑一次 python build.py")
        return 1
    files = sorted(OUT.rglob("*.html"))
    cache, problems, checked = {}, [], 0

    def resolve(base_dir, path):
        """按浏览器的规则解析相对路径：多余的 .. 在站点根处被吃掉，不会跑到 docs/ 外面"""
        parts = list(base_dir)
        for seg in path.split("/"):
            if seg in ("", "."):
                continue
            if seg == "..":
                if parts:
                    parts.pop()
                continue
            parts.append(seg)
        return OUT.joinpath(*parts)

    for f in files:
        html_text = f.read_text(encoding="utf-8")
        base = f.relative_to(OUT).parts[:-1]
        for url in re.findall(r'(?:href|src)="([^"]+)"', html_text):
            if "://" in url or url.startswith(("mailto:", "data:", "#", "//")):
                continue
            path, _, frag = url.partition("#")
            checked += 1
            if path.endswith("/"):
                path += "index.html"
            target = f if not path else resolve(base, path)
            if not target.exists():
                problems.append(f"{f.relative_to(OUT)}: 链接目标不存在 {url}")
                continue
            if frag and target.suffix == ".html":
                if target not in cache:
                    cache[target] = target.read_text(encoding="utf-8")
                if f'id="{frag}"' not in cache[target]:
                    problems.append(f"{f.relative_to(OUT)}: 锚点不存在 {url}")
    print(f"扫描 {len(files)} 个页面 / {checked} 条站内链接")
    if problems:
        for x in problems:
            print("  ✗ " + x)
        return 1
    print("  全部通过：站内链接与锚点都能落到真实页面")
    return 0


def main():
    if "--check" in sys.argv:
        return check_posts()
    if "--check-links" in sys.argv:
        return check_site_links()

    posts = load_posts()

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "assets").mkdir(parents=True, exist_ok=True)

    # 清掉已经不存在对应源文件的旧文章目录（Windows 下文件可能被占用，失败不影响本次构建）
    slug_set = {p["slug"] for p in posts}
    posts_out = OUT / "posts"
    if posts_out.exists():
        for d in posts_out.iterdir():
            if d.is_dir() and d.name not in slug_set:
                try:
                    shutil.rmtree(d)
                except OSError:
                    pass

    for f in ASSETS_DIR.iterdir():
        if f.is_file():
            shutil.copy2(f, OUT / "assets" / f.name)

    (OUT / "index.html").write_text(build_index(posts), encoding="utf-8")

    for p in posts:
        d = OUT / "posts" / p["slug"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(build_post(p, posts), encoding="utf-8")

    (OUT / "tags").mkdir(exist_ok=True)
    (OUT / "tags" / "index.html").write_text(build_tags(posts), encoding="utf-8")

    (OUT / "series").mkdir(exist_ok=True)
    (OUT / "series" / "index.html").write_text(build_series(posts), encoding="utf-8")

    # 每个专栏一个独立页面；顺手清掉已经改了名字的旧专栏目录
    names = {p["series"] for p in posts if p["series"]}
    for name in names:
        d = OUT / "series" / series_slug(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(build_series_detail(posts, name), encoding="utf-8")
    for d in (OUT / "series").iterdir():
        if d.is_dir() and d.name not in {series_slug(n) for n in names}:
            try:
                shutil.rmtree(d)
            except OSError:
                pass

    if PAGES_DIR.exists():
        for f in PAGES_DIR.glob("*.md"):
            d = OUT / f.stem
            d.mkdir(parents=True, exist_ok=True)
            (d / "index.html").write_text(build_page(f), encoding="utf-8")

    (OUT / "feed.xml").write_text(build_feed(posts), encoding="utf-8")
    (OUT / "404.html").write_text(build_404(), encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")

    print(f"构建完成：{len(posts)} 篇文章 -> {OUT}")

    if "--serve" in sys.argv:
        import http.server
        import socketserver
        os.chdir(OUT)
        with socketserver.TCPServer(("127.0.0.1", 8088), http.server.SimpleHTTPRequestHandler) as httpd:
            print("预览地址 http://127.0.0.1:8088  (Ctrl+C 退出)")
            httpd.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
