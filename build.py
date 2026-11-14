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
      <a href="{prefix}series/index.html">系列</a>
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
    if p["series"]:
        same = [x for x in posts if x["series"] == p["series"]]
        series_html = (
            f'<a class="tag tag-series" href="../../series/index.html#{slugify_tag(p["series"])}">'
            f'系列 · {esc(p["series"])}（{len(same)} 篇）</a>'
        )
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
    <div class="prose">
{p['content']}
    </div>
    <footer class="article-foot">
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
    blocks = []
    for s in sorted(groups, key=lambda x: (-len(groups[x]), x)):
        items = sorted(groups[s], key=lambda x: x["date"])
        lis = "".join(
            f'<li><a href="../../{post_url(p)}">{esc(p["title"])}</a> '
            f'<span style="color:var(--faint);font-size:13px">{fmt_date(p["date"])}</span></li>'
            for p in items
        )
        blocks.append(f"""    <section id="{slugify_tag(s)}" style="margin-bottom:36px">
      <p class="section-label">{esc(s)} · {len(items)} 篇</p>
      <ul class="prose" style="font-size:15px">{lis}</ul>
    </section>""")
    body = '  <section class="hero" style="padding-bottom:16px"><h1 style="font-size:24px">系列</h1></section>\n' + "\n".join(blocks)
    return page("系列", body, prefix="../../")


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


def main():
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
    main()
