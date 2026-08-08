#!/usr/bin/env python3
"""
国内多源新闻聚合爬虫（精准版）
- 每个网页源使用专用解析函数
- 关键词过滤（标题或摘要包含任意关键词）
- 每个源最多 15 条
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

import requests
from bs4 import BeautifulSoup

# ---------- 配置 ----------
MAX_PER_SOURCE = 15         # 每源最多保留条数
CUTOFF_DAYS = 3             # 只保留最近3天
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
    """检查文本是否包含任一关键词"""
    return bool(KEYWORD_PATTERN.search(text))

# ---------- 新闻源配置 ----------
SOURCES = {
    # RSS 源
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
    # 网页源（每个源都有专属解析函数）
    "ths": {
        "name_cn": "同花顺",
        "url": "https://www.10jqka.com.cn",
        "type": "web",
        "parser": "parse_ths"
    },
    "phoenix": {
        "name_cn": "凤凰网",
        "url": "https://www.ifeng.com",
        "type": "web",
        "parser": "parse_phoenix"
    },
    "sina": {
        "name_cn": "新浪",
        "url": "https://finance.sina.com.cn/stock",
        "type": "web",
        "parser": "parse_sina"
    },
    "cctv": {
        "name_cn": "央视新闻",
        "url": "https://news.cctv.cn/china",
        "type": "web",
        "parser": "parse_cctv"
    },
    "zaobao": {
        "name_cn": "联合早报",
        "url": "https://www.kuzaobao.com/plus/list.php?tid=1",
        "type": "web",
        "parser": "parse_zaobao"
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

# ---------- 各网站专用解析函数 ----------
def parse_ths(html, base_url):
    """同花顺首页解析"""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    # 同花顺首页新闻通常位于 .news-item 或 .article-item 或 ul.news-list li
    containers = soup.select("div.news-item, div.article-item, ul.news-list li, div.list-item")
    for c in containers:
        a = c.find("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        link = a.get("href", "")
        if not title or len(title) < 5:
            continue
        if link.startswith("/"):
            link = urllib.parse.urljoin(base_url, link)
        elif not link.startswith("http"):
            continue
        # 提取时间
        time_tag = c.find("span", class_=re.compile(r"time|date")) or c.find("time")
        pub = time_tag.get_text(strip=True) if time_tag else ""
        items.append({"title": title, "link": link, "summary": title, "pub": pub})
    return items

def parse_phoenix(html, base_url):
    """凤凰网首页解析"""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    # 凤凰网新闻通常位于 .news-item, .article-item, .index-news-item
    containers = soup.select("div.news-item, div.article-item, div.index-news-item, div.focus-news-item, ul.list li")
    for c in containers:
        a = c.find("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        link = a.get("href", "")
        if not title or len(title) < 5:
            continue
        if link.startswith("/"):
            link = urllib.parse.urljoin(base_url, link)
        elif not link.startswith("http"):
            continue
        time_tag = c.find("span", class_=re.compile(r"time|date")) or c.find("time")
        pub = time_tag.get_text(strip=True) if time_tag else ""
        items.append({"title": title, "link": link, "summary": title, "pub": pub})
    return items

def parse_sina(html, base_url):
    """新浪财经解析"""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    # 新浪财经新闻列表常见结构
    containers = soup.select("ul.list li, div.list-item, div.article-item, div.news-item")
    for c in containers:
        a = c.find("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        link = a.get("href", "")
        if not title or len(title) < 5:
            continue
        if link.startswith("/"):
            link = urllib.parse.urljoin(base_url, link)
        elif not link.startswith("http"):
            continue
        time_tag = c.find("span", class_=re.compile(r"time|date")) or c.find("time")
        pub = time_tag.get_text(strip=True) if time_tag else ""
        items.append({"title": title, "link": link, "summary": title, "pub": pub})
    return items

def parse_cctv(html, base_url):
    """央视新闻解析"""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    # 央视新闻列表
    containers = soup.select("ul.list li, div.news-item, div.article-item, div.list-item")
    for c in containers:
        a = c.find("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        link = a.get("href", "")
        if not title or len(title) < 5:
            continue
        if link.startswith("/"):
            link = urllib.parse.urljoin(base_url, link)
        elif not link.startswith("http"):
            continue
        time_tag = c.find("span", class_=re.compile(r"time|date")) or c.find("time")
        pub = time_tag.get_text(strip=True) if time_tag else ""
        items.append({"title": title, "link": link, "summary": title, "pub": pub})
    return items

def parse_zaobao(html, base_url):
    """联合早报（镜像站）解析"""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    # 联合早报列表常见结构
    containers = soup.select("ul.list li, div.list-item, div.article-item, div.news-item")
    for c in containers:
        a = c.find("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        link = a.get("href", "")
        if not title or len(title) < 5:
            continue
        if link.startswith("/"):
            link = urllib.parse.urljoin(base_url, link)
        elif not link.startswith("http"):
            continue
        time_tag = c.find("span", class_=re.compile(r"time|date")) or c.find("time")
        pub = time_tag.get_text(strip=True) if time_tag else ""
        items.append({"title": title, "link": link, "summary": title, "pub": pub})
    return items

# ---------- 通用备用解析（增强） ----------
def parse_web_generic(html, base_url):
    """
    使用 fetch_news.py 的稳健策略：提取所有 a 标签，智能过滤
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    seen_urls = set()
    invalid_keywords = ["关于我们", "版权声明", "隐私政策", "登录", "注册", "首页", "下载App", "更多", "快讯", "实时"]

    for a in soup.find_all("a", href=True):
        title = a.get_text(strip=True)
        raw_url = a["href"]
        if not raw_url or raw_url.startswith('#') or raw_url.startswith('javascript:'):
            continue
        # 补全链接
        full_url = urljoin(base_url, raw_url)
        # 排除根域名本身
        if full_url.strip('/') == base_url.strip('/'):
            continue
        # 标题长度过滤（至少 5 个字符，避免导航）
        if len(title) < 5 or len(title) > 100:
            continue
        # 排除功能按钮
        if any(k in title for k in invalid_keywords):
            continue
        # 去重
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)
        # 尝试提取时间（若有）
        pub = ""
        time_tag = a.find_previous("time") or a.find_next("time")
        if time_tag:
            pub = time_tag.get("datetime") or time_tag.get_text(strip=True)
        # 若没有时间，留空（后续设为当前时间）
        candidates.append({
            "title": title,
            "link": full_url,
            "summary": title,
            "pub": pub
        })
        if len(candidates) >= 200:  # 限制候选数量，防止过多
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

# ---------- 全局存储 ----------
news_pool = []
source_counter = {}

def add_news(...):
    dt = parse_pubdate(pub_raw)
    if dt is None:
        dt = datetime.now(timezone.utc)  # 未知时间则使用当前时间
    # 不要强制 CUTOFF_DAYS，只对明显旧新闻（如超过7天）丢弃，或直接不丢弃
    # 若仍需时间过滤，可放宽到 7 天
    if datetime.now(timezone.utc) - dt > timedelta(days=7):
        return False
    # 其余逻辑...

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
            parser_name = source_cfg.get("parser", "")
            if parser_name:
                parser_func = globals().get(parser_name)
                if parser_func:
                    items = parser_func(html_text, url)
                else:
                    print(f"未找到解析器 {parser_name}，使用通用解析")
                    items = parse_generic(html_text, url)
            else:
                items = parse_generic(html_text, url)
        else:
            print(f"未知类型 {stype}，跳过 {name}")
            return
    except Exception as e:
        print(f"抓取 {name} 失败: {e}")
        return

    # 按时间降序
    items.sort(key=lambda x: parse_pubdate(x["pub"]) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    count = 0
    for it in items:
        if add_news(name, it["title"], it["link"], it["summary"], it["pub"]):
            count += 1
        if count >= MAX_PER_SOURCE:
            break
    print(f"从 {name} 抓取到 {count} 条有效新闻（过滤后）")

# ---------- 生成 HTML（与之前相同） ----------
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
