#!/usr/bin/env python3
"""精简国内新闻爬虫：适配财新/雪球/央视/新浪/凤凰等国内媒体，无英文翻译"""
import urllib.request, ssl, json, time, re, xml.etree.ElementTree as ET, sys
from datetime import datetime, timedelta, timezone

# 基础网络配置
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}

# 全局配置
MAX_PER_SOURCE = 5  # 每家媒体最多5条
CUTOFF = datetime.now(timezone.utc) - timedelta(days=4)

# 你提供的国内新闻源配置
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
    # 网页通道（当前版本仅实现RSS解析，网页抓取预留接口，后续可扩展）
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
        "url": "https://www.kuzaobao.com/plus/list.php?tid=1",
        "type": "web"
    }     
}

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
        return datetime(int(y), month_map[mon], int(h), int(mi), int(s), tzinfo=timezone.utc)
    m2 = re.match(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})', date_str)
    if m2:
        return datetime(*map(int, m2.groups()), tzinfo=timezone.utc)
    # 适配国内中文时间格式兜底
    try:
        import datetime
        return datetime.datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except:
        return datetime.now(timezone.utc)

def is_news_recent(pub_str: str):
    dt = parse_pubdate(pub_str)
    return dt >= CUTOFF

# 全局存储
news_pool = []
source_counter = {}

# 新增新闻入库（无英文标题，title_en复用中文标题）
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
        "title_en": title,    # 国内新闻无英文，复用标题兼容前端
        "title_cn": title,
        "url": url,
        "summary": summary,
        "pub_raw": pub_raw,
        "pub_sort_dt": parse_pubdate(pub_raw)
    })
    source_counter[source_cn] = source_counter.get(source_cn, 0) + 1
    return True

# 批量加载所有RSS源，web网页源暂跳过（无通用解析逻辑）
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
                add_news(src_name, it["title"], it["link"], it["desc"], it["pub"])
        except Exception as e:
            print(f"{src_name} 抓取失败: {str(e)}", file=sys.stderr)

def main():
    # 1. 加载全部RSS新闻源
    load_all_sources()

    # 2. 全局按发布时间倒序
    global news_pool
    news_pool = sorted(news_pool, key=lambda x: x["pub_sort_dt"], reverse=True)

    # 3. UTC时间转为北京时间输出，无需翻译
    for item in news_pool:
        bj_dt = item["pub_sort_dt"] + timedelta(hours=8)
        item["pub_sort_dt"] = bj_dt.strftime("%Y-%m-%d %H:%M:%S")

    # 4. 输出和原格式完全一致的news.json，前端直接复用
    output = {
        "update_cst": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d 北京时间"),
        "total_count": len(news_pool),
        "source_stat": source_counter,
        "news": news_pool
    }
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"国内新闻采集完成，共{len(news_pool)}条，已生成news.json")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err_data = {"error": str(e)}
        with open("news.json", "w", encoding="utf-8") as f:
            json.dump(err_data, ensure_ascii=False, indent=2)
