#!/usr/bin/env python3
"""
国内多源新闻聚合爬虫（增强版 - 改进网页解析）
"""

import ssl
import json
import time
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
import html
import urllib.request
import urllib.parse

import requests
from bs4 import BeautifulSoup

# ---------- 新闻源配置 ----------
SOURCES = {
    "caixin": {
        "name_cn": "财新",
        "url": "https://quanwenrss.com/caixin",
        "type": "rss"
    },
    "snowball": {
        "name_cn": "雪球",
        "url": "https://xueqiu.com/hots/topic/rss",
        "type": "rss"
    },
    "ths": {
        "name_cn": "同花顺",
        "url": "https://www.10jqka.com.cn",
        "type": "web",
        "list_selector": "div.news-item, div.article-item, ul.news-list li, div.list-content li, div.news-box li"
    },
    "phoenix": {
        "name_cn": "凤凰网",
        "url": "https://www.ifeng.com",
        "type": "web",
        "list_selector": "div.news-item, div.article-item, div.index-news-item, div.focus-news-item, ul.list li, div.list-item"
    },
    "sina": {
        "name_cn": "新浪",
        "url": "https://finance.sina.com.cn/stock",
        "type": "web",
        "list_selector": "div.list-item, ul.list li, div.article-item, div.news-item, li.item"
    },
    "cctv": {
        "name_cn": "央视新闻",
        "url": "https://news.cctv.cn/china",
        "type": "web",
        "list_selector": "div.news-list li, ul.list li, div.article-item, div.item, li.news-item"
    },
    "zaobao": {
        "name_cn": "联合早报",
        "url": "https://www.kuzaobao.com/plus/list.php?tid=1",
        "type": "web",
        "list_selector": "ul.list li, div.list-item, div.article-item, div.item, li.news"
    }
}

MAX_PER_SOURCE = 5
CUTOFF_DAYS = 3
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"

# ---------- 工具函数 ----------
def fetch_rss(url, timeout=15):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", "replace")

def parse_rss(xml_text):
    results = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return results
    for item in root.findall(".//item"):
        title = re.sub(r"<.+?>", "", item.findtext("title", "").strip())
        link = item.findtext("link", "").strip()
        summary = re.sub(r"<.+?>", "", item.findtext("description", "")[:300])
        pub = item.findtext("pubDate", "")
        if title and link:
            results.append({"title": title, "link": link, "summary": summary, "pub": pub})
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
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.encoding = resp.apparent_encoding or 'utf-8'
    return resp.text

def parse_web_with_selectors(html_text, base_url, list_selector, title_selector="", time_selector=""):
    """增强版网页解析：多级选择器回退，过滤非新闻链接"""
    soup = BeautifulSoup(html_text, "html.parser")
    items = []
    
    # 构建选择器层级
    selectors_to_try = []
    if list_selector:
        # 如果 list_selector 包含逗号，拆分为多个
        for s in list_selector.split(','):
            s = s.strip()
            if s:
                selectors_to_try.append(s)
    # 添加通用备选
    selectors_to_try.extend([
        "ul.list li, div.news-item, div.article-item, div.list-item, div.item, div.news-list li",
        "div[class*='news'] li, div[class*='article'] li, ul[class*='list'] li",
        "a[href*='/']"  # 最后保底
    ])
    
    containers = []
    for sel in selectors_to_try:
        found = soup.select(sel)
        if found:
            containers = found
            break
    if not containers:
        # 极端情况：取所有 a
        containers = soup.find_all("a", href=True)
    
    seen_links = set()
    for container in containers:
        if container.name == "a":
            a = container
        else:
            a = container.select_one(title_selector) if title_selector else None
            if not a:
                a = container.find("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        link = a.get("href", "")
        if not title or len(title) < 4 or len(title) > 120:
            continue
        low = title.lower()
        if any(kw in low for kw in ["首页", "返回", "登录", "注册", "广告", "招聘", "关于", "隐私", "帮助", "搜索", "投稿", "收藏", "评论"]):
            continue
        if link.startswith("javascript:") or link.startswith("#") or link.startswith("mailto:"):
            continue
        if link.startswith("/"):
            link = urllib.parse.urljoin(base_url, link)
        elif not link.startswith("http"):
            continue
        if link in seen_links:
            continue
        seen_links.add(link)
        
        # 提取时间
        pub = ""
        if time_selector:
            time_tag = container.select_one(time_selector)
        else:
            time_tag = container.find("time") or container.find("span", class_=re.compile(r"time|date"))
        if not time_tag:
            parent = container.parent
            for _ in range(3):
                if parent:
                    time_tag = parent.find("time") or parent.find("span", class_=re.compile(r"time|date"))
                    if time_tag:
                        break
                    parent = parent.parent
        if time_tag:
            pub = time_tag.get("datetime") or time_tag.get_text(strip=True)
        
        items.append({"title": title, "link": link, "summary": title, "pub": pub})
    
    return items

def parse_pubdate(date_str):
    if not date_str:
        return None
    date_str = date_str.strip()
    patterns = [
        r'\w{3}, (\d{1,2}) (\w{3}) (\d{4}) (\d{2}):(\d{2}):(\d{2})',
        r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})',
        r'(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})',
        r'(\d{4})年(\d{1,2})月(\d{1,2})日',
        r'(\d{4})/(\d{1,2})/(\d{1,2})',
    ]
    for pat in patterns:
        m = re.match(pat, date_str)
        if m:
            groups = m.groups()
            if len(groups) == 6:
                month_map = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
                             "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
                d, mon, y, h, mi, s = groups
                try:
                    return datetime(int(y), month_map[mon], int(d), int(h), int(mi), int(s), tzinfo=timezone.utc)
                except:
                    continue
            elif len(groups) == 5:
                try:
                    y, mon, d, h, mi = map(int, groups)
                    return datetime(y, mon, d, h, mi, tzinfo=timezone.utc)
                except:
                    continue
            elif len(groups) == 3:
                try:
                    y, mon, d = map(int, groups)
                    if y > 2000 and 1 <= mon <= 12 and 1 <= d <= 31:
                        return datetime(y, mon, d, tzinfo=timezone.utc)
                except:
                    continue
    # 尝试提取数字
    nums = re.findall(r'\d+', date_str)
    if len(nums) >= 3:
        try:
            y, mon, d = int(nums[0]), int(nums[1]), int(nums[2])
            if y > 2000 and 1 <= mon <= 12 and 1 <= d <= 31:
                return datetime(y, mon, d, tzinfo=timezone.utc)
        except:
            pass
    return None

# ---------- 抓取逻辑 ----------
news_pool = []
source_counter = {}

def add_news(source_name, title, url, summary, pub_raw):
    if source_counter.get(source_name, 0) >= MAX_PER_SOURCE:
        return False
    dt = parse_pubdate(pub_raw)
    if dt is None:
        dt = datetime.now(timezone.utc)
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
            list_sel = source_cfg.get("list_selector", "")
            title_sel = source_cfg.get("title_selector", "")
            time_sel = source_cfg.get("time_selector", "")
            items = parse_web_with_selectors(html_text, url, list_sel, title_sel, time_sel)
        else:
            print(f"未知类型 {stype}，跳过 {name}")
            return
    except Exception as e:
        print(f"抓取 {name} 失败: {e}")
        return

    # 按时间排序（未知时间排后）
    items.sort(key=lambda x: parse_pubdate(x["pub"]) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    count = 0
    for it in items[:MAX_PER_SOURCE]:
        if add_news(name, it["title"], it["link"], it["summary"], it["pub"]):
            count += 1
    print(f"从 {name} 抓取到 {count} 条有效新闻")

# ---------- 生成HTML和JSON ----------
def generate_html():
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
            title = html.escape(item["title"]) if item["title"] else "（无标题）"
            summary = html.escape(item["summary"])[:200] if item["summary"] else ""
            dt = item["pub_dt"] + timedelta(hours=8)
            time_str = dt.strftime('%Y-%m-%d %H:%M') if dt.year > 2000 else "未知时间"
            html_content += f"""
        <div class="news-item">
            <div class="title"><a href="{item["url"]}" target="_blank">{title}</a></div>
            <div class="summary">{summary}…</div>
            <div class="meta">
                <span class="source">{item["source"]}</span>
                <span>🕒 {time_str}</span>
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

def main():
    bj_now = datetime.now(timezone(timedelta(hours=8)))
    if not (6 <= bj_now.hour <= 23):
        print(f"当前北京时间 {bj_now.strftime('%H:%M')} 不在运行时段 (6:00-23:00)，退出。")
        return

    global news_pool, source_counter
    news_pool = []
    source_counter = {}

    for key, cfg in SOURCES.items():
        process_source(key, cfg)
        time.sleep(0.5)

    news_pool.sort(key=lambda x: x["pub_dt"], reverse=True)

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
                "pub_time": (n["pub_dt"] + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S') if n["pub_dt"].year > 2000 else "未知时间"
            }
            for n in news_pool
        ]
    }
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    generate_html()
    print(f"✅ 采集完成，共 {len(news_pool)} 条新闻")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ 运行出错: {e}")
        err_data = {"error": str(e)}
        with open("news.json", "w", encoding="utf-8") as f:
            json.dump(err_data, f, ensure_ascii=False, indent=2)
