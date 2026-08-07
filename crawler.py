#!/usr/bin/env python3
"""国内新闻爬虫｜新增关键词过滤，仅输出匹配指定关键词资讯"""
import urllib.request, ssl, json, time, re, xml.etree.ElementTree as ET, sys
from datetime import datetime, timedelta, timezone

# 基础网络配置
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}

# ========== 核心配置区（可自行修改）==========
MAX_PER_SOURCE = 5  # 单媒体最多5条新闻
CUTOFF = datetime.now(timezone.utc) - timedelta(days=4)

# 【关键词过滤列表】匹配任意一条即保留新闻，按需增删
FILTER_KEYWORDS = [
    # 宏观经济
    GDP,降息,降准,LPR,央行,财政部,国债,财政,消费,地产,楼市,
    # 股市资本市场
    A股,上证指数,创业板,科创板,基金,ETF,回购,增持,IPO,退市,证监会,
    # 产业科技
    AI,大模型,芯片,半导体,光伏,储能,新能源,机器人,新质生产力,
    # 政策重大新闻
    政策,新规,国务院,统计局,进出口,外贸,汇率,人民币
]

# 新闻源配置（你提供的国内媒体）
SOURCES = {
    # RSS 抓取通道
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
    # 网页通道（当前仅实现RSS解析，网页源自动跳过）
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
        "name_cn": "新浪财经",
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
        "url": "https://www.kuzaobao/plus/list.php?tid=1",
        "type": "web"
    }     
}
# ==============================================

# 简易网络请求
def fetch(url: str, timeout=15):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", "replace")

# 解析标准RSS/Atom
def parse_rss(raw_xml: str):
    res = []
    root = ET.fromstring(raw_xml)
    # RSS item
    for item in root.findall(".//item"):
        title = re.sub(r"<.+?>", "", item.findtext("title", "").strip())
        link = item.findtext("link", "").strip()
        desc = re.sub(r"<.+?>", "", item.findtext("description", "")[:1000])
        pub = item.findtext("pubDate", "")
        if title and link:
            res.append({"title": title, "link": link, "desc": desc, "pub": pub})
    # Atom entry 兼容
    ns = "{http://www.w3.org/2005/Atom}"
    for entry in root.findall(ns + "entry"):
        title = re.sub(r"<.+?>", "", entry.findtext(ns + "title", "").strip())
        link = ""
        for ln in entry.findall(ns + "link"):
            link = ln.get("href", "")
        desc = re.sub(r"<.+?>", "", entry.findtext(ns + "summary", "")[:1000])
        pub = entry.findtext(ns + "published", "") or entry.findtext(ns + "updated", "")
        if title and link:
            res.append({"title": title, "link": link, "desc": desc, "pub": pub})
    return res

# 时间解析函数
def parse_pubdate(date_str: str):
    if not date_str:
        return datetime.now(timezone.utc)
    m1 = re.match(r'\w{3}, (\d{1,2}) (\w{3}) (\d{4}) (\d{2}):(\d{2}):(\d{2})', date_str.strip())
    if m1:
        month_map = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,"Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
        d, mon, y, h, mi, s = m1.groups()
        return datetime(int(y), month_map[mon], int(d), int(h), int(mi), int(s), tzinfo=timezone.utc)
    m2 = re.match(r'(\d{4})-(\d{2})T(\d{2}):(\d{2})', date_str)
    if m2:
        return datetime(*map(int, m2.groups()), tzinfo=timezone.utc)
    # 国内中文时间兜底
    try:
        return datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except:
        return datetime.now(timezone.utc)

def is_news_recent(pub_str: str):
    dt = parse_pubdate(pub_str)
    return dt >= CUTODE

# ========== 新增关键词过滤函数 ==========
def match_keyword(title: str, summary: str) -> bool:
    """标题或摘要包含任意关键词返回True，否则丢弃"""
    full_text = title + summary
    for kw in FILTER_KEYWORDS:
        if kw in full_text:
            return True
    return False
# ========================================

# 全局存储
news_pool = []
source_counter = {}

# 入库函数
def add_news(source_cn: str, title: str, url: str, summary: str, pub_raw: str):
    if source_counter.get(source_cn, 0) >= MAX_PER_SOURCE:
        return False
    if not is_news_recent(pub_raw):
        return False
    # 标题去重
    for item in news_pool:
        if item["title_cn"].strip() == title.strip():
            return False
    news_pool.append({
        "source": source_cn,
        "title_en": title,
        "title_cn": title,
        "url": url,
        "summary": summary,
        "pub_raw": pub_raw,
        "pub_sort_dt": parse_pubdate(pub_raw)
    })
    source_counter[source_cn] = source_counter.get(source_cn, 0) + 1
    return True

# 批量加载所有RSS源，网页源跳过
def load_all_sources():
    for key, info in SOURCES.items():
        src_name = info["name_cn"]
        src_type = info["type"]
        src_url = info["url"]
        if src_type != "rss":
            print(f"跳过网页源 {src_name}，暂不支持网页抓取", file=sys.stderr)
            continue
        try:
            time.sleep(0.8)
            xml = fetch(src_url)
            items = parse_rss(xml)
            for it in items:
                # 【核心过滤】不匹配关键词直接跳过这条新闻
                if not match_key(it["title"], it["desc"]):
                    continue
                add_news(src_name, it["title"], it["link"], it["desc"], it["pub"])
        except Exception as e:
            print(f"{src_name} 抓取失败: {str(e)}", file=sys.stderr)

def main():
    # 1. 加载并过滤所有新闻
    load_all_sources()

    # 2. 全局按发布时间倒序
    global news_pool
    news_pool = sorted(news_pool, key=lambda x: x["pub_sort_dt"], reverse=True)

    # 3. UTC转北京时间
    for item in news_pool:
        bj_dt = item["pub_sort_dt"] + timedelta(hours=8)
        item["pub_sort_dt"] = bj_dt.strftime("%Y-%m-%d %H:%M:%S")

    # 4. 输出标准news.json（前端完全兼容）
    output = {
        "update_cst": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S 北京时间"),
        "total_count": len(news_pool),
        "source_stat": source_counter,
        "news": news_pool
    }
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"过滤完成，匹配关键词新闻共{len(news_pool)}条，已生成news.json")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err_data = {"error": str(e)}
        with open("news.json", "w", encoding="utf-8") as f:
            json.dump(err_data, ensure_ascii=False, indent=2)
