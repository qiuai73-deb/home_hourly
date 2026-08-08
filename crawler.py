#!/usr/bin/env python3
"""
国内多源新闻聚合爬虫（稳定版 - 北京时间统一显示）
- 每个源默认 5 条，同花顺 10 条
- 时间优先显示北京时间（解析后 +8 小时）
- 若解析失败则显示原始字符串
- 排序基于解析时间，失败时排最后
- 生成 index.html 和 news.json
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
DEFAULT_MAX = 5
SPECIAL_MAX = {"ths": 10}
CUTOFF_DAYS = 7
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"

# ---------- 关键词 ----------
KEYWORDS = [
    "GDP", "降息", "加息", "LPR", "央行", "美元", "人民币", "金融", "消费", "券商","证券",
    "地产", "财经", "突发", "重大","独家", "重大", "黄金","CPI","路透","彭博","周期",
    "A股", "指数", "创业", "科创", "基金", "ETF", "回购", "增持", "IPO","资金","利率",
    "纳斯达克", "证监会", "私募", "公募", "标普", "龙头", "指数","涨停","概念","利空",
    "AI", "大模型", "芯片", "半导体", "华为", "鸿蒙", "新能源", "苹果",
    "科技", "deepseek", "比亚迪", "小米", "大疆", "字节", "腾讯", "阿里","知情人士",
    "微信", "英伟达", "谷歌", "抖音", "kimi", "豆包","年报","龙头",
    "银行", "高盛", "利润", "统计局", "大摩", "小摩", "汇率"
]
KEYWORD_PATTERN = re.compile('|'.join(KEYWORDS), re.IGNORECASE)

def is_relevant(text):
    return bool(KEYWORD_PATTERN.search(text))

# ---------- 新闻源 ----------
SOURCES = {
    "caixin": {"name_cn": "财新", "url": "https://quanwenrss.com/caixin", "type": "rss"},
    "snowball": {"name_cn": "雪球", "url": "https://xueqiu.com/hots/topic/rss", "type": "rss"},
    "ths": {"name_cn": "同花顺", "url": "https://www.10jqka.com.cn/classic", "type": "web"},
    "eastmoney": {"name_cn": "东财", "url": "https://finance.eastmoney.com/a/cywjh.html", "type": "web"},
    "sina": {"name_cn": "新浪", "url": "https://finance.sina.com.cn/roll/#pageid=384&lid=2671&k=&num=50&page=1", "type": "web"},
    "cls": {"name_cn": "财联", "url": "https://www.cls.cn", "type": "web"},
    "zaobao": {"name_cn": "联合早报", "url": "https://www.kuzaobao.com/plus/list.php?tid=1", "type": "web"}
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

# ---------- 时间提取辅助（增强） ----------
def extract_time_from_element(element):
    # 检查自身及父级
    for parent in [element] + list(element.parents)[:5]:
        if parent is None:
            continue
        # 1. 查找所有可能包含时间的标签
        for tag in parent.find_all(['time', 'span', 'div', 'p']):
            # 检查 class
            class_str = ' '.join(tag.get('class', []))
            if any(k in class_str.lower() for k in ['time', 'date', 'pub', 'publish', 'post', 'info', 'meta']):
                text = tag.get_text(strip=True)
                if text and len(text) >= 4:
                    return text
            dt = tag.get('datetime')
            if dt:
                return dt
            # 检查 data-time 等属性
            for attr in ['data-time', 'data-date', 'data-publish']:
                val = tag.get(attr)
                if val:
                    return val
        # 2. 在父级文本中搜索日期模式
        if parent.name and parent.name not in ['a', 'span', 'p']:
            text = parent.get_text(separator=' ', strip=True)
            patterns = [
                r'\d{4}-\d{1,2}-\d{1,2}',               # 2026-08-08
                r'\d{1,2}-\d{1,2} \d{2}:\d{2}',         # 08-08 10:30
                r'\d{4}/\d{1,2}/\d{1,2}',               # 2026/08/08
                r'\d{1,2}月\d{1,2}日',                  # 8月8日
                r'\d{4}年\d{1,2}月\d{1,2}日',           # 2026年8月8日
                r'\d{1,2}:\d{2}',                       # 10:30（可能不完整）
            ]
            for pat in patterns:
                m = re.search(pat, text)
                if m:
                    return m.group()
    return ""

# ---------- Web 解析 ----------
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
        full_url = urljoin(base_url, raw_url)
        if full_url.strip('/') == base_url.strip('/'):
            continue
        if len(title) < 5 or len(title) > 100:
            continue
        if any(k in title for k in invalid_keywords):
            continue
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        pub = extract_time_from_element(a)
        candidates.append({
            "title": title,
            "link": full_url,
            "summary": title,
            "pub": pub
        })
        if len(candidates) >= 200:
            break
    return candidates

# ---------- 时间解析（返回 datetime UTC） ----------
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
        r'(\d{1,2})-(\d{1,2}) (\d{2}):(\d{2})',  # 08-08 10:30
        r'(\d{1,2})/(\d{1,2}) (\d{2}):(\d{2})',  # 08/08 10:30
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
                elif len(groups) == 4:
                    mon, d, h, mi = map(int, groups)
                    now = datetime.now(timezone.utc)
                    y = now.year
                    dt = datetime(y, mon, d, h, mi, tzinfo=timezone.utc)
                    if dt > now:
                        dt = datetime(y-1, mon, d, h, mi, tzinfo=timezone.utc)
                    return dt
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

def add_news(source_name, title, url, summary, pub_raw, max_limit):
    if not title:
        return False
    if not is_relevant(title + " " + summary):
        return False
    if source_counter.get(source_name, 0) >= max_limit:
        return False
    dt = parse_pubdate(pub_raw)
    if dt is None:
        dt = datetime(1970, 1, 1, tzinfo=timezone.utc)  # 解析失败排最后
    # 时间过滤（只对解析成功且超过7天的丢弃）
    if dt.year > 1970 and datetime.now(timezone.utc) - dt > timedelta(days=CUTOFF_DAYS):
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
    max_limit = SPECIAL_MAX.get(source_key, DEFAULT_MAX)
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
    # 排序
    items.sort(key=lambda x: parse_pubdate(x["pub"]) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    count = 0
    for it in items:
        if add_news(name, it["title"], it["link"], it["summary"], it["pub"], max_limit):
            count += 1
        if count >= max_limit:
            break
    print(f"✅ {name} 过滤后保留 {count} 条 (上限 {max_limit})")

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
            # 显示北京时间：若pub_dt有效且非1970，则转为北京时间字符串
            if item["pub_dt"].year > 1970:
                beijing_time = item["pub_dt"] + timedelta(hours=8)
                time_display = beijing_time.strftime('%Y-%m-%d %H:%M') + " 北京时间"
            else:
                # 若解析失败，显示原始字符串或“未知”
                time_display = item["pub_raw"] if item["pub_raw"] else "未知时间"
            html_content += f"""
        <div class="news-item">
            <div class="title"><a href="{item["url"]}" target="_blank">{title}</a></div>
            <div class="summary">{summary}…</div>
            <div class="meta">
                <span class="source">{item["source"]}</span>
                <span>🕒 {time_display}</span>
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
                "pub_raw": n["pub_raw"],
                "pub_beijing": (n["pub_dt"] + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S') + " 北京时间" if n["pub_dt"].year > 1970 else None
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
