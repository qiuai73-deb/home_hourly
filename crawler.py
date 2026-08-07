#!/usr/bin/env python3
"""精简外媒涉华新闻爬虫：仅输出新闻清单，无正文抓取、无灾害API"""
import urllib.request, ssl, json, time, re, xml.etree.ElementTree as ET, sys, html as html_mod
from datetime import datetime, timedelta, timezone
from translator import batch_translate

# 基础配置
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}
MAX_PER_SOURCE = 5  # 每家媒体最多5条
CUTOFF = datetime.now(timezone.utc) - timedelta(days=4)

# 涉华过滤关键词
CKW = ["china","chinese","beijing","xi jinping","li qiang","wang yi",
       "taiwan","hong kong","xinjiang","tibet","south china sea",
       "belt and road","huawei","tencent","alibaba","tiktok","shein",
       "temu","cpec","renminbi","yuan","pboc","deepseek","baidu",
       "xiaomi","chinese economy","chinese market","chinese official",
       "sino-","brics","shanghai","shenzhen","guangzhou",
       "people's liberation army","chinese military","chinese army",
       "ccp","communist party of china","pla navy","pla air force","taiwan strait"]
PLA_RE = re.compile(r"\bpla\b", re.I)

def is_cn(text: str) -> bool:
    tl = (text or "").lower()
    if PLA_RE.search(tl):
        return True
    for kw in CKW:
        if kw in tl:
            return True
    return False

# 简易网络请求（去掉多次重试）
def fetch(url: str, timeout=15):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", "replace")

# 解析RSS/Atom通用
def parse_rss(raw_xml: str):
    res = []
    root = ET.fromstring(raw_xml)
    # 标准RSS item
    for item in root.findall(".//item"):
        title = re.sub(r"<.+?>", "", item.findtext("title", "").strip())
        link = item.findtext("link", "").strip()
        desc = re.sub(r"<.+?>", "", item.findtext("description", "")[:1000])
        pub = item.findtext("pubDate", "")
        if title and link:
            res.append({"title": title, "link": link, "desc": desc, "pub": pub})
    # Atom entry兼容
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

# 时间解析
def parse_pubdate(date_str: str):
    if not date_str:
        return datetime.now(timezone.utc)
    m1 = re.match(r'\w{3}, (\d{1,2}) (\w{3}) (\d{4}) (\d{2}):(\d{2}):(\d{2})', date_str.strip())
    if m1:
        month_map = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,"Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
        d, mon, y, h, mi, s = m1.groups()
        return datetime(int(y), month_map[mon], int(d), int(h), int(mi), int(s), tzinfo=timezone.utc)
    m2 = re.match(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})', date_str)
    if m2:
        return datetime(*map(int, m2.groups()), tzinfo=timezone.utc)
    return datetime.now(timezone.utc)

def is_news_recent(pub_str: str):
    dt = parse_pubdate(pub_str)
    return dt >= CUTOFF

# 全局存储
news_pool = []
source_counter = {}

# 添加新闻到池，控制单源上限
def add_news(source_name: str, title_en: str, url: str, summary: str, pub_raw: str):
    if source_counter.get(source_name, 0) >= MAX_PER_SOURCE:
        return False
    if not is_news_recent(pub_raw):
        return False
    # 去重：同英文标题不重复入库
    for item in news_pool:
        if item["title_en"].lower().strip() == title_en.lower().strip():
            return False
    news_pool.append({
        "source": source_name,
        "title_en": title_en,
        "title_cn": "",
        "url": url,
        "summary": summary,
        "pub_raw": pub_raw,
        "pub_sort_dt": parse_pubdate(pub_raw)
    })
    source_counter[source_name] = source_counter.get(source_name, 0) + 1
    return True

# 抓取Google News外媒聚合RSS
def load_media_google_rss():
    media_list = [
        ("Reuters", "site:reuters.com+china+when:1d"),
        ("Bloomberg", "site:bloomberg.com+china+when:1d"),
        ("AP", "site:apnews.com+china+when:1d"),
        ("Nikkei Asia", "site:asia.nikkei.com+china+when:1d"),
        ("Financial Times", "site:ft.com+china+when:1d"),
        ("Wall Street Journal", "site:wsj.com+china+when:1d"),
        ("CNBC", "site:cnbc.com+china+when:1d"),
        ("New York Times", "site:nytimes.com+china+when:1d"),
        ("CNN", "site:cnn.com+china+when:1d"),
        ("AFP", "site:afp.com+china+when:1d"),
        ("The Economist", "site:economist.com+china+when:1d"),
        ("New York Times","site:nytimes.com+china+when:1d"),
        ("BBC", "site:BBC.com+china+when:1d"),
        ("DW", "site:DW.com+china+when:1d"),
        ("半岛", "site:aljazeera+china+when:1d"),
        ("yahoo", "site:yahoo+china+when:1d"),   
           
    ]
    for src, q in media_list:
        try:
            time.sleep(1)
            rss_url = f"https://news.google.com/rss/search?q={q}"
            xml = fetch(rss_url)
            items = parse_rss(xml)
            for it in items:
                if is_cn(it["title"] + it["desc"]):
                    add_news(src, it["title"], it["link"], it["desc"], it["pub"])
        except Exception:
            continue

def main():
    # 1. 加载全部新闻源
    load_media_google_rss()

    # 2. 全局按发布时间倒序排序
    global news_pool
    news_pool = sorted(news_pool, key=lambda x: x["pub_sort_dt"], reverse=True)

    # 3. 批量翻译标题
    en_title_list = [item["title_en"] for item in news_pool]
    cn_title_list = batch_translate(en_title_list)
    for idx, item in enumerate(news_pool):
        item["title_cn"] = cn_title_list[idx]
        # UTC时间 +8小时转为北京时间
        bj_dt = item["pub_sort_dt"] + timedelta(hours=8)
        item["pub_sort_dt"] = bj_dt.strftime("%Y-%m-%d %H:%M:%S")

    # 4. 输出极简新闻清单json（仅前端展示需要的字段）
    output = {
        "update_cst": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S 北京时间"),
        "total_count": len(news_pool),
        "source_stat": source_counter,
        "news": news_pool
    }
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"采集完成，共{len(news_pool)}条新闻，已生成news.json")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err_data = {"error": str(e)}
        with open("news.json", "w", encoding="utf-8") as f:
            json.dump(err_data, f, ensure_ascii=False, indent=2)
