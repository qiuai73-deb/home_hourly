#!/usr/bin/env python3
"""
国内多源新闻聚合爬虫（含历史管理与最新新闻标记）
- 保留历史新闻，定期清理3天前旧闻
- 顶部“最新新闻”仅显示本次新抓取的条目（按时间倒序）
- 每个源默认5条，同花顺10条
- 统一北京时间显示
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
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------- 配置 ----------
DEFAULT_MAX = 5
SPECIAL_MAX = {"ths": 10}
CUTOFF_DAYS = 3               # 只保留最近3天新闻
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
REQUEST_TIMEOUT = 10
NEWS_JSON_FILE = "news.json"

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
        "url": "https://www.10jqka.com.cn/classic",
        "type": "web"
    },
    "eastmoney": {
        "name_cn": "东财",
        "url": "https://finance.eastmoney.com",
        "type": "web"
    },
    "sina": {
        "name_cn": "新浪",
        "url": "https://finance.sina.com.cn/roll/#pageid=384&lid=2671&k=&num=50&page=1",
        "type": "web"
    },
    "cls": {
        "name_cn": "财联",
        "url": "https://www.cls.cn",
        "type": "web"
    },
    "zaobao": {
        "name_cn": "联合早报",
        "url": "https://www.kuzaobao.com/plus/list.php?tid=1",
        "type": "web"
    }
}

# ---------- 网络请求 ----------
def fetch_rss(url, timeout=REQUEST_TIMEOUT):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", "replace")

def fetch_web(url, timeout=REQUEST_TIMEOUT):
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

# ---------- 简化时间提取 ----------
def extract_time_from_element(element):
    for parent in [element] + list(element.parents)[:3]:
        if parent is None:
            continue
        for tag in parent.find_all(['time']):
            dt = tag.get('datetime')
            if dt:
                return dt
            text = tag.get_text(strip=True)
            if text:
                return text
        for tag in parent.find_all(['span', 'div']):
            class_str = ' '.join(tag.get('class', []))
            if any(k in class_str.lower() for k in ['time', 'date', 'pub', 'publish']):
                text = tag.get_text(strip=True)
                if text:
                    return text
    text = element.get_text(separator=' ', strip=True)
    patterns = [
        r'\d{4}-\d{1,2}-\d{1,2} \d{1,2}:\d{2}',
        r'\d{4}/\d{1,2}/\d{1,2} \d{1,2}:\d{2}',
        r'\d{1,2}月\d{1,2}日 \d{1,2}:\d{2}',
        r'\d{4}-\d{1,2}-\d{1,2}',
        r'\d{4}/\d{1,2}/\d{1,2}',
        r'\d{1,2}月\d{1,2}日',
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

# ---------- 时间解析 ----------
def parse_pubdate(date_str):
    if not date_str:
        return None
    date_str = date_str.strip()
    patterns = [
        r'\w{3}, (\d{1,2}) (\w{3}) (\d{4}) (\d{2}):(\d{2}):(\d{2})',
        r'(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})',
        r'(\d{4})年(\d{1,2})月(\d{1,2})日 (\d{1,2}):(\d{2})',
        r'(\d{1,2})月(\d{1,2})日 (\d{1,2}):(\d{2})',
        r'(\d{4})-(\d{2})-(\d{2})',
        r'(\d{4})年(\d{1,2})月(\d{1,2})日',
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
    return None

# ---------- 历史新闻管理 ----------
def load_history():
    """读取历史新闻文件"""
    if Path(NEWS_JSON_FILE).exists():
        try:
            with open(NEWS_JSON_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "news" in data:
                    return data["news"]
        except:
            pass
    return []

def save_news(news_list, update_time, source_stat):
    """保存完整新闻数据"""
    output = {
        "update_cst": update_time,
        "total_count": len(news_list),
        "source_stat": source_stat,
        "news": news_list
    }
    with open(NEWS_JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

# ---------- 主逻辑 ----------
def main():
    bj_now = datetime.now(timezone(timedelta(hours=8)))
    if not (6 <= bj_now.hour <= 23):
        print(f"⏰ 当前北京时间 {bj_now.strftime('%H:%M')} 不在运行时段 (6:00-23:00)，退出。")
        return

    # 1. 加载历史新闻
    history = load_history()
    print(f"📂 加载历史新闻 {len(history)} 条")

    # 2. 清理 3 天前的旧新闻（基于 pub_dt）
    cutoff = datetime.now(timezone.utc) - timedelta(days=CUTOFF_DAYS)
    valid_history = []
    for item in history:
        dt_str = item.get("pub_dt")  # 存储为 ISO 格式字符串
        if dt_str:
            try:
                dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
                if dt >= cutoff:
                    valid_history.append(item)
            except:
                # 如果无法解析时间，保留该条目
                valid_history.append(item)
        else:
            # 无时间字段，保留（可能为占位）
            valid_history.append(item)
    print(f"🗑️ 清理后保留 {len(valid_history)} 条（删除 {len(history)-len(valid_history)} 条）")

    # 3. 抓取新新闻
    global news_pool, source_counter
    news_pool = []          # 用于存放本次新抓取的有效新闻
    source_counter = {}

    for key, cfg in SOURCES.items():
        name = cfg["name_cn"]
        url = cfg["url"]
        stype = cfg["type"]
        max_limit = SPECIAL_MAX.get(key, DEFAULT_MAX)
        items = []
        try:
            if stype == "rss":
                xml = fetch_rss(url)
                items = parse_rss(xml)
            elif stype == "web":
                html = fetch_web(url)
                items = parse_web_generic(html, url)
            else:
                continue
        except Exception as e:
            print(f"❌ 抓取 {name} 失败: {e}")
            continue

        print(f"📌 {name} 原始抓取 {len(items)} 条")

        # 按时间降序排列并过滤关键词、去重（与历史合并去重）
        items.sort(key=lambda x: parse_pubdate(x["pub"]) or datetime(1970,1,1, tzinfo=timezone.utc), reverse=True)
        count = 0
        for it in items:
            # 构建临时新闻对象（用于去重）
            title = it["title"]
            url = it["link"]
            # 检查是否与历史重复
            duplicate = False
            for h in valid_history:
                if h.get("url") == url or h.get("title") == title:
                    duplicate = True
                    break
            if duplicate:
                continue
            # 关键词过滤
            if not is_relevant(title + " " + it["summary"]):
                continue
            # 时间解析
            dt = parse_pubdate(it["pub"])
            if dt is None:
                # 无时间则用当前时间（但标记为未知）
                dt = datetime(1970, 1, 1, tzinfo=timezone.utc)
            # 时间过滤（只保留3天内）
            if dt.year > 2000 and datetime.now(timezone.utc) - dt > timedelta(days=CUTOFF_DAYS):
                continue

            # 计算北京时间
            if dt.year > 2000:
                bj_dt = dt + timedelta(hours=8)
                pub_beijing = bj_dt.strftime('%Y-%m-%d %H:%M:%S 北京时间')
            else:
                pub_beijing = "未知时间"

            new_item = {
                "source": name,
                "title": title,
                "url": url,
                "summary": it["summary"],
                "pub_raw": it["pub"],
                "pub_dt": dt.isoformat(),
                "pub_beijing": pub_beijing
            }
            news_pool.append(new_item)
            count += 1
            if count >= max_limit:
                break
        print(f"✅ {name} 本次新增 {count} 条")

    # 4. 合并历史与本次新增
    all_news = valid_history + news_pool
    # 去重（按 url 和 title）
    seen_urls = set()
    seen_titles = set()
    merged = []
    for item in all_news:
        url = item.get("url")
        title = item.get("title")
        if url and url in seen_urls:
            continue
        if title and title in seen_titles:
            continue
        if url:
            seen_urls.add(url)
        if title:
            seen_titles.add(title)
        merged.append(item)

    # 5. 排序：按 pub_dt 降序（未知时间放最后）
    merged.sort(key=lambda x: datetime.fromisoformat(x["pub_dt"].replace('Z', '+00:00')) if x.get("pub_dt") and x["pub_dt"] != "1970-01-01T00:00:00+00:00" else datetime(1970,1,1, tzinfo=timezone.utc), reverse=True)

    # 6. 统计每个源的数量（只统计有效新闻，不区分新旧）
    source_stat = {}
    for item in merged:
        src = item.get("source", "未知")
        source_stat[src] = source_stat.get(src, 0) + 1

    # 7. 输出 news.json（包含全部新闻）
    save_news(merged, bj_now.strftime("%Y-%m-%d %H:%M:%S 北京时间"), source_stat)

    # 8. 生成 HTML（直接使用合并后的数据，但顶部“最新新闻”只显示本次新增的 news_pool）
    # 我们修改 generate_html 接收两个参数：全部新闻列表、本次新增列表
    generate_html(merged, news_pool, bj_now)

    print(f"🎉 采集完成，总新闻数 {len(merged)}，本次新增 {len(news_pool)} 条")

# ---------- 生成 HTML（支持标记最新） ----------
def generate_html(all_news, new_news, bj_now):
    # 顶部仅显示本次新增新闻（按时间倒序排列）
    top_news = sorted(new_news, key=lambda x: datetime.fromisoformat(x["pub_dt"].replace('Z', '+00:00')) if x.get("pub_dt") and x["pub_dt"] != "1970-01-01T00:00:00+00:00" else datetime(1970,1,1, tzinfo=timezone.utc), reverse=True)[:15]

    # 分媒体板块显示全部新闻
    grouped = {}
    for item in all_news:
        src = item.get("source", "未知")
        if src not in grouped:
            grouped[src] = []
        grouped[src].append(item)

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
.top-latest-block {{
    background:#0f2b59;
    border-radius:12px;
    padding:22px;
    margin-bottom:24px;
    color:#fff;
}}
.top-block-title {{
    font-size:20px;
    font-weight:600;
    margin-bottom:16px;
    border-left:5px solid #42a5f5;
    padding-left:12px;
}}
.top-news-item {{
    border-bottom:1px solid rgba(255,255,255,0.15);
    padding:12px 8px;
    cursor:pointer;
    transition:background 0.2s;
}}
.top-news-item:hover {{ background:rgba(255,255,255,0.08); }}
.top-cn-title {{ font-size:16px; font-weight:500; margin-bottom:4px; }}
.top-meta-row {{ display:flex; gap:16px; font-size:12px; opacity:0.85; flex-wrap:wrap; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>📰 国内新闻聚合</h1>
        <div class="info">更新: {bj_now.strftime('%Y-%m-%d %H:%M:%S')} 北京时间 · 共 {len(all_news)} 条</div>
        <div class="info">本次新增 {len(new_news)} 条</div>
    </div>
"""
    # 顶部最新区域（只显示新增）
    if top_news:
        html_content += f"""
    <div class="top-latest-block">
        <div class="top-block-title">🔥 最新新闻（本次新抓取）</div>
"""
        for item in top_news:
            title = html.escape(item.get("title", "无标题"))
            source = item.get("source", "未知")
            pub_display = item.get("pub_beijing") or item.get("pub_raw") or "未知时间"
            url = item.get("url", "#")
            html_content += f"""
        <div class="top-news-item" onclick="window.open('{url}', '_blank')">
            <div class="top-cn-title">{title}</div>
            <div class="top-meta-row">
                <span>来源：{source}</span>
                <span>{pub_display}</span>
            </div>
        </div>
"""
        html_content += "</div>\n"

    # 分媒体板块
    html_content += '<div class="news-list">\n'
    for media, items in grouped.items():
        html_content += f'<div class="news-item"><div class="title" style="font-weight:bold;color:#1a365d;">{media}（{len(items)}条）</div></div>'
        for item in items[:15]:  # 每个媒体最多显示15条（可根据需要调整）
            title = html.escape(item.get("title", "无标题"))
            summary = html.escape(item.get("summary", ""))[:200]
            pub_display = item.get("pub_beijing") or item.get("pub_raw") or "未知时间"
            url = item.get("url", "#")
            source = item.get("source", "未知")
            html_content += f"""
        <div class="news-item" onclick="window.open('{url}', '_blank')">
            <div class="title"><a href="{url}" target="_blank">{title}</a></div>
            <div class="summary">{summary}…</div>
            <div class="meta">
                <span class="source">{source}</span>
                <span>🕒 {pub_display}</span>
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

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ 运行出错: {e}")
        err_data = {"error": str(e)}
        with open("news.json", "w", encoding="utf-8") as f:
            json.dump(err_data, f, ensure_ascii=False, indent=2)
