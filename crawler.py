#!/usr/bin/env python3
"""
国内多源新闻聚合爬虫（稳健版）
- 使用通用 a 标签遍历策略，兼容各类网站结构
- 关键词过滤（标题或摘要含关键词）
- 每个源最多 15 条
- 未知时间自动设为当前时间
- 生成 index.html 和 news.json
- 仅在 6:00~23:00（北京时间）执行
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
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ---------- 配置 ----------
MAX_PER_SOURCE = 15
CUTOFF_DAYS = 7             # 放宽到7天，避免旧新闻被丢弃
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"

# ---------- 关键词过滤 ----------
KEYWORDS = [
    "GDP", "降息", "加息", "LPR", "央行", "美元", "人民币", "金融", "消费", "券商","证券",
    "地产", "财经", "突发", "重大","独家", "重大", "黄金","CPI","路透","彭博","周期",
    "A股", "指数", "创业", "科创", "基金", "ETF", "回购", "增持", "IPO","资金","利率",
    "纳斯达克", "证监会", "私募", "公募", "标普", "龙头", "指数","涨停","概念","利空",
    "AI", "大模型", "芯片", "半导体", "华为", "鸿蒙", "新能源", "苹果",
    "科技", "deepseek", "比亚迪", "小米", "大疆", "字节", "腾讯", "阿里","知情人士",
    "微信", "英伟达", "谷歌", "抖音", "kimi", "豆包","年报","龙头",
    "银行", "高盛", "利润", "统计局", "大摩", "小摩", "汇率", "特朗普"
]
KEYWORD_PATTERN = re.compile('|'.join(KEYWORDS), re.IGNORECASE)

def is_relevant(text: str) -> bool:
    return bool(KEYWORD_PATTERN.search(text))

# ---------- 新闻源 ----------
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
        "type": "web"
    },
    "phoenix": {
        "name_cn": "凤凰网",
        "url": "https://www.ifeng.com",
        "type": "web"
    },
    "sina": {
        "name_cn": "新浪",
        "url": "https://finance.sina.com.cn/stock",
        "type": "web"
    },
    "cctv": {
        "name_cn": "央视新闻",
        "url": "https://news.cctv.cn/china",
        "type": "web"
    },
    "zaobao": {
        "name_cn": "联合早报",
        "url": "https://www.kuzaobao.com/plus/list.php?tid=1",
        "type": "web"
    }
}

# ---------- 网络请求 ----------
def fetch_rss(url, timeout=15):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", "replace")

def fetch_web(url, timeout=15):
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.encoding = resp.apparent_encoding or 'utf-8'
    return resp.text

# ---------- RSS 解析 ----------
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

# ---------- Web 解析（稳健策略：遍历所有 a 标签） ----------
def parse_web_generic(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    seen_urls = set()
    invalid_keywords = ["关于我们", "版权声明", "隐私政策", "登录", "注册", "首页", "下载App", "更多", "快讯", "实时"]

    for a in soup.find_all("a", href=True):
        title = a.get_text(strip=True)
        raw_url = a.get("href", "")
        if not raw_url or raw_url.startswith('#') or raw_url.startswith('javascript:'):
            continue
        # 补全链接
        full_url = urljoin(base_url, raw_url)
        # 排除根域名本身
        if full_url.strip('/') == base_url.strip('/'):
            continue
        # 标题长度过滤（至少5个字符）
        if len(title) < 5 or len(title) > 100:
            continue
        # 排除功能性文字
        if any(k in title for k in invalid_keywords):
            continue
        # 去重
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        # 尝试提取时间（如果有）
        pub = ""
        time_tag = a.find_previous("time") or a.find_next("time")
        if time_tag:
            pub = time_tag.get("datetime") or time_tag.get_text(strip=True)
        # 如果找不到时间，留空（后续处理为当前时间）
        candidates.append({
            "title": title,
            "link": full_url,
            "summary": title,
            "pub": pub
        })
        if len(candidates) >= 200:   # 防止过多
            break

    return candidates

# ---------- 时间解析 ----------
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
        r'(\d{1,2})月(\d{1,2})日',
    ]
    for pat in patterns:
        m = re.match(pat, date_str)
        if m:
            groups = m.groups()
            try:
                if len(groups) == 6:
                    month_map = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
                                 "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
                    d, mon, y, h, mi, s = groups
                    return datetime(int(y), month_map[mon], int(d), int(h), int(mi), int(s), tzinfo=timezone.utc)
                elif len(groups) == 5:
                    y, mon, d, h, mi = map(int, groups)
                    return datetime(y, mon, d, h, mi, tzinfo=timezone.utc)
                elif len(groups) == 3:
                    y, mon, d = map(int, groups)
                    return datetime(y, mon, d, tzinfo=timezone.utc)
                elif len(groups) == 2:
                    mon, d = map(int, groups)
                    now = datetime.now(timezone.utc)
                    y = now.year
                    dt = datetime(y, mon, d, tzinfo=timezone.utc)
                    if dt > now:
                        dt = datetime(y-1, mon, d, tzinfo=timezone.utc)
                    return dt
            except:
                continue
    # 尝试提取数字（如 "2026-08-07"）
    nums = re.findall(r'\d+', date_str)
    if len(nums) >= 3:
        try:
            y, mon, d = int(nums[0]), int(nums[1]), int(nums[2])
            if y > 2000 and 1 <= mon <= 12 and 1 <= d <= 31:
                return datetime(y, mon, d, tzinfo=timezone.utc)
        except:
            pass
    return None

# ---------- 全局存储 ----------
news_pool = []
source_counter = {}

def add_news(source_name, title, url, summary, pub_raw):
    if not title:
        return False
    # 关键词过滤
    if not is_relevant(title + " " + summary):
        return False
    if source_counter.get(source_name, 0) >= MAX_PER_SOURCE:
        return False
    dt = parse_pubdate(pub_raw)
    if dt is None:
        dt = datetime.now(timezone.utc)   # 未知时间使用当前时间
    # 时间过滤（放宽到7天）
    if datetime.now(timezone.utc) - dt > timedelta(days=CUTOFF_DAYS):
        return False
    # 去重
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
            html = fetch_web(url)
            items = parse_web_generic(html, url)
        else:
            print(f"未知类型 {stype}，跳过 {name}")
            return
    except Exception as e:
        print(f"❌ 抓取 {name} 失败: {e}")
        return

    print(f"📌 {name} 原始抓取 {len(items)} 条")

    # 按时间降序（未知时间排最后）
    items.sort(key=lambda x: parse_pubdate(x["pub"]) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    count = 0
    for it in items:
        if add_news(name, it["title"], it["link"], it["summary"], it["pub"]):
            count += 1
        if count >= MAX_PER_SOURCE:
            break
    print(f"✅ {name} 过滤后保留 {count} 条")

# ---------- 生成 HTML ----------
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
            if dt.year < 2000:
                time_str = "未知时间"
            else:
                time_str = dt.strftime('%Y-%m-%d %H:%M')
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

# ---------- 主函数 ----------
def main():
    bj_now = datetime.now(timezone(timedelta(hours=8)))
    if not (6 <= bj_now.hour <= 23):
        print(f"⏰ 当前北京时间 {bj_now.strftime('%H:%M')} 不在运行时段 (6:00-23:00)，退出。")
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
    print(f"🎉 采集完成，共 {len(news_pool)} 条新闻")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ 运行出错: {e}")
        err_data = {"error": str(e)}
        with open("news.json", "w", encoding="utf-8") as f:
            json.dump(err_data, f, ensure_ascii=False, indent=2)
