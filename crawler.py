#!/usr/bin/env python3
"""
国内多源新闻聚合爬虫
- 支持 RSS 和普通网页（使用 BeautifulSoup）
- 每个源最多 5 条，按发布时间倒序排列
- 生成 index.html 和 news.json
- 只在 6:00~23:00（北京时间）执行
"""

import ssl
import json
import time
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
import html
import urllib.request
import urllib.parse

# 第三方库（需安装：pip install requests beautifulsoup4）
import requests
from bs4 import BeautifulSoup

# ---------- 新闻源配置（完全照搬您的文件） ----------
SOURCES = {
    # --- RSS 抓取通道 ---
    "caixin": {
        "name": "caixin",
        "name_cn": "财新",
        "url": "https://quanwenrss.com/caixin",
        "type": "rss"
    },
    "snowball": {
        "name": "snowball",
        "name_cn": "雪球",
        "url": "https://xueqiu.com/hots/topic/rss",
        "type": "rss"
    },
    # --- 普通网页抓取通道 ---
    "ths": {
        "name": "ths",
        "name_cn": "同花顺",
        "url": "https://www.10jqka.com.cn",
        "type": "web"
    },
    "phoenix": {
        "name": "phoenix",
        "name_cn": "凤凰网",
        "url": "https://www.ifeng.com",
        "type": "web"
    },
    "sina": {
        "name": "sina",
        "name_cn": "新浪",
        "url": "https://finance.sina.com.cn/stock",
        "type": "web"
    },
    "cctv": {
        "name": "cctv",
        "name_cn": "央视新闻",
        "url": "https://news.cctv.cn/china",
        "type": "web"
    },
    "zaobao": {
        "name": "zaobao",
        "name_cn": "联合早报",
        "url": "https://www.kuzaobao.com/plus/list.php?tid=1",
        "type": "web"
    }
}

MAX_PER_SOURCE = 5          # 每家媒体最多保留条数（您可改为 10）
CUTOFF_DAYS = 3             # 只保留最近 3 天的新闻
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"

# ---------- 工具函数 ----------
def fetch_rss(url, timeout=15):
    """获取 RSS XML（使用 urllib，忽略 SSL 证书）"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", "replace")

def parse_rss(xml_text):
    """解析 RSS/Atom，返回列表 [{title, link, pub, summary}]"""
    results = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return results
    # RSS item
    for item in root.findall(".//item"):
        title = re.sub(r"<.+?>", "", item.findtext("title", "").strip())
        link = item.findtext("link", "").strip()
        summary = re.sub(r"<.+?>", "", item.findtext("description", "")[:300])
        pub = item.findtext("pubDate", "")
        if title and link:
            results.append({"title": title, "link": link, "summary": summary, "pub": pub})
    # Atom entry
    ns = "{http://www.w3.org/2005/Atom}"
    for entry in root.findall(ns + "entry"):
        title = re.sub(r"<.+?>", "", entry.findtext(ns + "title", "").strip())
        link = ""
        for ln in entry.findall(ns + "link"):
            link = ln.get("href", "")
        summary = re.sub(r"<.+?>", "", entry.findtext(ns + "summary", "")[:300])
        pub = entry.findtext(ns + "published", "") or entry.findtext(ns + "updated", "")
        if title and link:
            results.append({"title": title, "link": link, "summary": summary, "pub": pub})
    return results

def fetch_web(url, timeout=15):
    """使用 requests 获取网页 HTML"""
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.encoding = resp.apparent_encoding or 'utf-8'
    return resp.text

def parse_web_generic(html_text, base_url):
    """
    通用网页解析：提取所有 a 标签，根据文本长度和 href 特征筛选新闻标题
    返回列表 [{title, link, summary, pub}]
    """
    soup = BeautifulSoup(html_text, "html.parser")
    items = []
    # 找出所有 a 标签，并过滤掉无意义链接
    for a in soup.find_all("a", href=True):
        title = a.get_text(strip=True)
        link = a["href"]
        # 跳过空标题、过短或过长的标题（可能是导航）
        if not title or len(title) < 5 or len(title) > 80:
            continue
        # 补全相对链接
        if link.startswith("/"):
            link = urllib.parse.urljoin(base_url, link)
        elif not link.startswith("http"):
            continue
        # 尽量提取发布时间（如果页面中有时间标签）
        pub = ""
        # 尝试在 a 的父级中查找时间
        parent = a.parent
        time_tag = parent.find("time") if parent else None
        if time_tag:
            pub = time_tag.get("datetime") or time_tag.get_text(strip=True)
        # 如果没有时间，就留空，后续用当前时间替代
        items.append({
            "title": title,
            "link": link,
            "summary": title,  # 暂用标题作为摘要
            "pub": pub
        })
    # 去重（按链接去重）
    seen = set()
    unique = []
    for it in items:
        if it["link"] not in seen:
            seen.add(it["link"])
            unique.append(it)
    return unique

def parse_pubdate(date_str):
    """尝试多种时间格式，返回 datetime（UTC）"""
    if not date_str:
        return datetime.now(timezone.utc)
    date_str = date_str.strip()
    # RSS 常用: "Mon, 07 Aug 2026 10:30:00 GMT"
    m = re.match(r'\w{3}, (\d{1,2}) (\w{3}) (\d{4}) (\d{2}):(\d{2}):(\d{2})', date_str)
    if m:
        month_map = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
                     "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
        d, mon, y, h, mi, s = m.groups()
        return datetime(int(y), month_map[mon], int(d), int(h), int(mi), int(s), tzinfo=timezone.utc)
    # ISO 8601: "2026-08-07T10:30:00"
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})', date_str)
    if m:
        return datetime(*map(int, m.groups()), tzinfo=timezone.utc)
    # "2026-08-07 10:30:00"
    m = re.match(r'(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})', date_str)
    if m:
        return datetime(*map(int, m.groups()), tzinfo=timezone.utc)
    # 中文日期（如"2026年08月07日"）——简化处理
    m = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日', date_str)
    if m:
        y, mon, d = map(int, m.groups())
        return datetime(y, mon, d, tzinfo=timezone.utc)
    # 无法解析则返回当前
    return datetime.now(timezone.utc)

# ---------- 抓取主逻辑 ----------
news_pool = []
source_counter = {}

def add_news(source_name, title, url, summary, pub_raw):
    """添加单条新闻（去重、限数、时间过滤）"""
    if source_counter.get(source_name, 0) >= MAX_PER_SOURCE:
        return False
    dt = parse_pubdate(pub_raw)
    if datetime.now(timezone.utc) - dt > timedelta(days=CUTOFF_DAYS):
        return False
    for item in news_pool:
        if item["url"] == url or item["title"] == title:
            return False
    news_pool.append({
        "source": source_name,
        "title": title,
        "url": url,
        "summary": summary,
        "pub_raw": pub_raw,
        "pub_dt": dt
    })
    source_counter[source_name] = source_counter.get(source_name, 0) + 1
    return True

def process_source(source_key, source_cfg):
    """处理单个新闻源（RSS 或 Web）"""
    name = source_cfg["name_cn"]
    url = source_cfg["url"]
    stype = source_cfg["type"]
    items = []
    try:
        if stype == "rss":
            xml = fetch_rss(url)
            items = parse_rss(xml)
        elif stype == "web":
            html_text = fetch_web(url)
            items = parse_web_generic(html_text, url)
        else:
            print(f"未知类型 {stype}，跳过 {name}")
            return
    except Exception as e:
        print(f"抓取 {name} 失败: {e}")
        return

    # 按发布时间降序排序（没有时间的排在最后）
    items.sort(key=lambda x: parse_pubdate(x["pub"]), reverse=True)
    count = 0
    for it in items[:MAX_PER_SOURCE]:
        if add_news(name, it["title"], it["link"], it["summary"], it["pub"]):
            count += 1
    print(f"从 {name} 抓取到 {count} 条有效新闻")

# ---------- 生成 HTML ----------
def generate_html():
    """生成响应式静态页面"""
    bj_now = datetime.now(timezone(timedelta(hours=8)))
    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>📰 国内新闻聚合</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background: #f5f7fa; padding:20px; color:#333; }}
.container {{ max-width:1200px; margin:0 auto; }}
.header {{ background: #1e3c72; color: white; padding:20px 30px; border-radius:12px; margin-bottom:25px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; }}
.header h1 {{ font-weight:400; font-size:24px; }}
.header .info {{ font-size:14px; opacity:0.9; }}
.news-list {{ display:grid; gap:16px; }}
.news-item {{ background:white; border-radius:10px; padding:18px 22px; box-shadow:0 2px 8px rgba(0,0,0,0.06); transition:0.2s; border-left:4px solid #1e3c72; }}
.news-item:hover {{ box-shadow:0 4px 16px rgba(0,0,0,0.10); }}
.news-item .title {{ font-size:18px; font-weight:500; line-height:1.5; margin-bottom:6px; }}
.news-item .title a {{ color:#1e3c72; text-decoration:none; }}
.news-item .title a:hover {{ text-decoration:underline; }}
.news-item .meta {{ font-size:14px; color:#888; display:flex; flex-wrap:wrap; gap:15px; margin-top:8px; }}
.news-item .meta .source {{ background:#eef2f7; padding:2px 12px; border-radius:20px; color:#1e3c72; }}
.news-item .summary {{ color:#666; font-size:15px; margin-top:8px; line-height:1.6; }}
@media (max-width:600px) {{
    .header {{ flex-direction:column; align-items:flex-start; gap:10px; }}
    .news-item .title {{ font-size:16px; }}
}}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>📰 国内新闻聚合</h1>
        <div class="info">更新: {bj_now.strftime('%Y-%m-%d %H:%M:%S')} 北京时间 · 共 {len(news_pool)} 条</div>
    </div>
    <div class="news-list">
"""
    if not news_pool:
        html_content += "<p style='text-align:center;color:#999;'>暂无新闻，请稍后查看。</p>"
    else:
        for item in news_pool:
            title = html.escape(item["title"])
            summary = html.escape(item["summary"])[:200]
            bj_time = (item["pub_dt"] + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M')
            html_content += f"""
        <div class="news-item">
            <div class="title"><a href="{item["url"]}" target="_blank">{title}</a></div>
            <div class="summary">{summary}…</div>
            <div class="meta">
                <span class="source">{item["source"]}</span>
                <span>🕒 {bj_time}</span>
            </div>
        </div>
"""
    html_content += """
    </div>
</div>
</body>
</html>
"""
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_content)

# ---------- 主函数 ----------
def main():
    # 检查北京时间是否在 6:00~23:00
    bj_now = datetime.now(timezone(timedelta(hours=8)))
    if not (6 <= bj_now.hour <= 23):
        print(f"当前北京时间 {bj_now.strftime('%H:%M')} 不在运行时段 (6:00-23:00)，退出。")
        return

    global news_pool, source_counter
    news_pool = []
    source_counter = {}

    # 遍历所有源
    for key, cfg in SOURCES.items():
        process_source(key, cfg)
        time.sleep(0.5)  # 礼貌间隔

    # 全局按时间倒序
    news_pool.sort(key=lambda x: x["pub_dt"], reverse=True)

    # 输出 JSON
    output = {
        "update_cst": bj_now.strftime("%Y-%m-%d %H:%M:%S 北京时间"),
        "total_count": len(news_pool),
        "source_stat": source_counter,
        "news": [
            {
                "source": n["source"],
                "title": n["title"],
                "url": n["url"],
                "summary": n["summary"],
                "pub_time": (n["pub_dt"] + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
            }
            for n in news_pool
        ]
    }
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    generate_html()
    print(f"✅ 采集完成，共 {len(news_pool)} 条新闻，已生成 index.html 和 news.json")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ 运行出错: {e}")
        err_data = {"error": str(e)}
        with open("news.json", "w", encoding="utf-8") as f:
            json.dump(err_data, f, ensure_ascii=False, indent=2)
