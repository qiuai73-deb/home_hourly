#!/usr/bin/env python3
"""
国内新闻聚合爬虫｜修复网页时间错乱BUG
- RSS源时间100%准确，网页无时间标记统一显示未知
- 关键词财经科技过滤
- 自动生成news.json + index.html静态页面
- 仅北京时间6:00~23:00运行
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

# ---------- 基础配置 ----------
DEFAULT_MAX = 5              # 普通媒体单源上限
SPECIAL_MAX = {"ths": 10}    # 同花顺单独上限10条
CUTOFF_DAYS = 7              # 只保留7天内新闻
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0 Safari/537.36"

# ---------- 财经科技关键词过滤 ----------
KEYWORDS = [
    "GDP", "降息", "加息", "LPR", "央行", "美元", "人民币", "金融", "消费", "券商","证券",
    "地产", "财经", "突发", "重大","独家", "黄金","CPI","路透","彭博","周期",
    "A股", "指数", "创业", "科创", "基金", "ETF", "回购", "增持", "IPO","资金","利率",
    "纳斯达克", "证监会", "私募", "公募", "标普", "龙头", "指数","涨停","概念","利空",
    "AI", "大模型", "芯片", "半导体", "华为", "鸿蒙", "新能源", "苹果",
    "科技", "deepseek", "比亚迪", "小米", "大疆", "字节", "腾讯", "阿里","知情人士",
    "微信", "英伟达", "谷歌", "抖音", "kimi", "豆包","年报","龙头",
    "银行", "高盛", "利润", "统计局", "大摩", "小摩", "汇率"
]
KEYWORD_PATTERN = re.compile('|'.join(KEYWORDS), re.IGNORECASE)
def is_relevant(text: str) -> bool:
    """标题/摘要命中关键词才保留"""
    return bool(KEYWORD_PATTERN.search(text))

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
        "url": "https://www.10jqka.com.cn/classic",
        "type": "web"
    },
    "eastmoney": {
        "name_cn": "东方财富",
        "url": "https://finance.eastmoney.com/a/cywjh.html",
        "type": "web"
    },
    "sina": {
        "name_cn": "新浪财经",
        "url": "https://finance.sina.com.cn/roll/#pageid=384&lid=2671&k=&num=50&page=1",
        "type": "web"
    },
    "cls": {
        "name_cn": "财联社",
        "url": "https://www.cls.cn",
        "type": "web"
    },
    "zaobao": {
        "name_cn": "联合早报",
        "url": "https://www.kuzaobao.com/plus/list.php?tid=1",
        "type": "web"
    }
}

# ---------- RSS专用请求（urllib） ----------
def fetch_rss(url, timeout=15):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", "replace")

# ---------- 网页请求（requests + bs4解析） ----------
def fetch_web(url, timeout=15):
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.encoding = resp.apparent_encoding or 'utf-8'
    return resp.text

# ---------- RSS XML解析 ----------
def parse_rss(xml_text):
    results = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return results
    # 标准RSS item
    for item in root.findall(".//item"):
        title = re.sub(r"<.+?>", "", item.findtext("title", "").strip())
        link = item.findtext("link", "").strip()
        summary = re.sub(r"<.+?>", "", item.findtext("description", "")[:300])
        pub = item.findtext("pubDate", "")
        if title and link:
            results.append({"title": title, "link": link, "summary": summary, "pub": pub})
    # Atom兼容
    ns = "{http://www.w3.org/2005/Atom}"
    for entry in root.findall(ns + "entry"):
        title = re.sub(r"<.+?>", "", entry.findtext(ns + "title", "").strip())
        link = ""
        for ln in entry.findall(ns + "link"):
            link = ln.get("href", "")
        summary = re.sub(r"<.+?>", "", entry.findtext(ns + "summary", "")[:300])
        pub = entry.findtext(ns + "published") or entry.findtext(ns + "updated", "")
        if title and link:
            results.append({"title": title, "link": link, "summary": summary, "pub": pub})
    return results

# ---------- 【修复版】网页时间提取函数（向上遍历20层父节点，扩充匹配规则） ----------
def extract_time_from_element(element):
    all_parents = []
    current = element
    # 向上遍历20层，扩大查找范围
    for _ in range(20):
        if current is None:
            break
        all_parents.append(current)
        current = current.parent
    # 逐层扫描时间信息
    for parent in all_parents:
        # 优先读取datetime属性
        dt_attr = parent.get("datetime")
        if dt_attr and len(dt_attr) > 6:
            return dt_attr
        # 遍历页面内时间标签
        for tag in parent.find_all(["time", "span", "div", "p", "em", "i"]):
            cls_text = " ".join(tag.get("class", [])).lower()
            time_keywords = ["time","date","pub","publish","发布","时间","日期","2024","2025","2026"]
            if any(k in cls_text for k in time_keywords):
                tag_text = tag.get_text(strip=True)
                if re.search(r"\d{4}|\d{1,2}月|\d{1,2}日|\d{1,2}:\d{2}", tag_text):
                    return tag_text
        # 全文正则匹配完整日期
        full_text = parent.get_text(" ", strip=True)
        time_reg = re.search(r"\d{4}[-/]?\d{1,2}[-/]?\d{1,2}\s?\d{0,2}:\d{0,2}", full_text)
        if time_reg:
            return time_reg.group()
    return ""

# ---------- 通用网页列表解析 ----------
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
        pub_time = extract_time_from_element(a)
        candidates.append({
            "title": title,
            "link": full_url,
            "summary": title,
            "pub": pub_time
        })
        if len(candidates) >= 200:
            break
    return candidates

# ---------- 【修复版】时间解析函数（无年份不自动填充今年，避免时间错乱） ----------
def parse_pubdate(date_str):
    if not date_str:
        return None
    date_str = date_str.strip()
    patterns = [
        (r'\w{3}, (\d{1,2}) (\w{3}) (\d{4}) (\d{2}):(\d{2}):(\d{2})', 6),
        (r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})', 5),
        (r'(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})', 5),
        (r'(\d{4})年(\d{1,2})月(\d{1,2})日 (\d{1,2}):(\d{2})', 5),
        (r'(\d{4})/(\d{1,2})/(\d{1,2}) (\d{1,2}):(\d{2})', 5),
        (r'(\d{4})年(\d{1,2})月(\d{1,2})', 3),
        (r'(\d{4})-(\d{1,2})-(\d{2})', 3),
    ]
    for pat, group_len in patterns:
        m = re.match(pat, date_str)
        if not m:
            continue
        groups = m.groups()
        try:
            if group_len == 6:
                month_map = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,"Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
                d, mon, y, h, mi, s = groups
                return datetime(int(y), month_map[mon], int(d), int(h), int(mi), int(s), tzinfo=timezone.utc)
            elif group_len >= 3:
                y, mon, d = int(groups[0]), int(groups[1]), int(groups[2])
                h = int(groups[3]) if len(groups)>=4 else 0
                mi = int(groups[4]) if len(groups)>=5 else 0
                return datetime(y, mon, d, h, mi, tzinfo=timezone.utc)
        except Exception:
            continue
    # 仅月/日/时分，缺少年份直接返回None，不补当年
    return None

# ---------- 全局存储容器 ----------
news_pool = []
source_counter = {}

# ---------- 入库函数（核心修复：无时间不再填充当前时间） ----------
def add_news(source_name, title, url, summary, pub_raw, max_limit):
    if not title:
        return False
    # 关键词过滤
    if not is_relevant(title + " " + summary):
        return False
    # 单源条数上限
    if source_counter.get(source_name, 0) >= max_limit:
        return False
    # 解析发布时间
    dt = parse_pubdate(pub_raw)
    # 有时间才判断7天时效，无时间不拦截
    if dt is not None and datetime.now(timezone.utc) - dt > timedelta(days=CUTOFF_DAYS):
        return False
    # 标题/链接去重
    for item in news_pool:
        if item["url"] == url or item["title"] == title:
            return False
    # 格式化北京时间
    if dt:
        bj_dt = dt + timedelta(hours=8)
        pub_beijing = bj_dt.strftime('%Y-%m-%d %H:%M:%S 北京时间')
    else:
        pub_beijing = "未知发布时间"
    news_pool.append({
        "source": source_name,
        "title": title,
        "url": url,
        "summary": summary,
        "pub_raw": pub_raw,
        "pub_dt": dt,
        "pub_beijing": pub_beijing
    })
    source_counter[source_name] = source_counter.get(source_name, 0) + 1
    return True

# ---------- 单个源抓取入口 ----------
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
            print(f"未知源类型：{stype}，跳过 {name}")
            return
    except Exception as e:
        print(f"❌ 抓取 {name} 失败: {e}")
        return
    print(f"📌 {name} 原始抓取 {len(items)} 条")
    # 先按原始时间倒序
    items.sort(key=lambda x: parse_pubdate(x["pub"]) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    count = 0
    for it in items:
        if add_news(name, it["title"], it["link"], it["summary"], it["pub"], max_limit):
            count += 1
        if count >= max_limit:
            break
    print(f"✅ {name} 过滤后保留 {count} 条 (上限 {max_limit})")

# ---------- 自动生成前端 index.html ----------
def generate_html():
    bj_now = datetime.now(timezone(timedelta(hours=8)))
    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>📰 国内财经新闻聚合</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background: #f5f7fa; padding:20px; color:#333; }}
.container {{ max-width:1200px; margin:0 auto; }}
.header {{ background: #1e3c72; color: white; padding:20px 30px; border-radius:12px; margin-bottom:25px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; }}
.header h1 {{ font-weight:400; font-size:24px; }}
.header .info {{ font-size:14px; opacity:0.9; }}
.news-list {{ display:grid; gap:16px; }}
.news-item {{ background:white; border-radius:10px; padding:18px 22px; box-shadow:0 2px 8px rgba(0,0,0.06); transition:0.2s; border-left:4px solid #1e3c72; }}
.news-item:hover {{ box-shadow:0 4px 16px rgba(0,0,0.10); }}
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
        <h1>📰 国内财经新闻聚合</h1>
        <div class="info">更新: {bj_now.strftime('%Y-%m-%d %H:%M:%S')} · 共 {len(news_pool)} 条</div>
    </div>
    <div class="news-list">
"""
    if not news_pool:
        html_content += "<p style='text-align:center;color:#999;padding:40px;font-size:16px;'>暂无匹配财经新闻，请等待下一轮抓取</p>"
    else:
        for item in news_pool:
            title = html.escape(item["title"])
            summary = html.escape(item["summary"])[:200]
            pub_display = item["pub_beijing"]
            html_content += f"""
        <div class="news-item">
            <div class="title"><a href="{item["url"]}" target="_blank">{title}</a></div>
            <div class="summary">{summary}…</div>
            <div class="meta">
                <span class="source">{item["source"]}</span>
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

# ---------- 主程序入口 ----------
def main():
    bj_now = datetime.now(timezone(timedelta(hours=8)))
    # 限制仅6~23点运行
    if not (6 <= bj_now.hour <= 23):
        print(f"⏰ 当前北京时间 {bj_now.hour}:{bj_now.minute}，不在6:00-23:00运行时段，直接退出")
        return
    global news_pool, source_counter
    news_pool = []
    source_counter = {}
    # 循环抓取所有媒体
    for key, cfg in SOURCES.items():
        process_source(key, cfg)
        time.sleep(0.5)
    # 全局按发布时间倒序
    news_pool.sort(key=lambda x: x["pub_dt"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    # 输出news.json
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
                "pub_beijing": n["pub_beijing"],
                "pub_time": n["pub_dt"].strftime('%Y-%m-%d %H:%M:%S') if n["pub_dt"] else "未知时间"
            }
            for n in news_pool
        ]
    }
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(output, ensure_ascii=False, indent=2)
    # 生成静态网页
    generate_html()
    print(f"\n🎉 全部抓取完成，有效财经新闻共 {len(news_pool)} 条")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ 程序全局异常: {str(e)}")
        err_data = {"error": str(e)}
        with open("news.json", "w", encoding="utf-8") as f:
            json.dump(err_data, ensure_ascii=False, indent=2)
