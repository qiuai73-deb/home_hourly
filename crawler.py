#!/usr/bin/env python3
"""国内新闻爬虫｜修复雪球404、优化RSS源、关闭关键词过滤"""
import urllib.request, ssl, json, time, re, xml.etree.ElementTree as ET, sys, random
from datetime import datetime, timedelta, timezone

# 浏览器请求头，降低403/404拦截
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0 Safari/537.36",
    "Referer": "https://www.baidu.com",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
}
# 忽略SSL证书
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# ========== 核心配置 ==========
MAX_PER_SOURCE = 10  # 原5条太少，上调到10，财新就能入库全部18条里前10
CUTOFF = datetime.now(timezone.utc) - timedelta(days=4)

# 新闻源：全部替换为官方RSS，废弃失效网页地址
SOURCES = {
    # RSS 稳定源（优先抓取，无JS渲染问题）
    "caixin": {
        "name": "caixin",
        "name_cn": "财新",
        "url": "https://www.caixin.com/rss",
        "type": "rss"
    },
    "snowball": {
        "name": "snowball",
        "name_cn": "雪球",
        "url": "https://xueqiu.com/rss/hot",
        "type": "rss"
    },
    "sina_fin": {
        "name": "sina_fin",
        "name_cn": "新浪财经RSS",
        "url": "https://finance.sina.com.cn/rss/finance.xml",
        "type": "rss"
    },
    "cctv_news": {
        "name": "cctv_news",
        "name_cn": "央视新闻",
        "url": "https://news.cctv.com/rss/news.xml",
        "type": "rss"
    },
    "ifeng_fin": {
        "name": "ifeng_fin",
        "name_cn": "凤凰财经",
        "url": "https://finance.ifeng.com/rss",
        "type": "rss"
    },
    # 网页源仅作备用（动态页面几乎抓不到资讯，仅保留）
    "ths": {
        "name": "ths",
        "name_cn": "同花顺",
        "url": "https://www.10jqka.com.cn",
        "type": "web"
    },
    "zaobao": {
        "name": "zaobao",
        "name_cn": "联合早报",
        "url": "https://www.kuzaobao.com/plus/list.php?tid=1",
        "type": "web"
    }
}
# ==============================================

# 通用网页请求
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
        pub = item.findtext("pubDate", "")
        desc = re.sub(r"<.+?>", "", item.findtext("description", "")[:1000])
        if title and link:
            res.append({"title": title, "link": link, "desc": desc, "pub": pub})
    # Atom兼容
    ns = "{http://www.w3.org/2005/Atom}"
    for entry in root.findall(ns + "entry"):
        title = re.sub(r"<.+?>", "", entry.findtext(ns + "title", "").strip())
        link = ""
        for ln in entry.findall(ns + "link"):
            link = ln.get("href", "")
        pub = entry.findtext(ns + "published") or entry.findtext(ns + "updated", "")
        desc = re.sub(r"<.+?>", "", entry.findtext(ns + "summary", "")[:1000])
        if title and link:
            res.append({"title": title, "link": link, "desc": desc, "pub": pub})
    return res

# 网页简易解析（仅静态a标签，动态页面无效）
def parse_web_list(html: str, source_name: str):
    result = []
    html_clean = html.replace("\n", "").replace("\r", "")
    pattern = re.compile(r'<a href="(https[^"]+)">(.*?)</a>')
    all_links = pattern.findall(html_clean)
    temp_dict = {}
    for link, raw_title in all_links:
        title = re.sub(r"<.+?>", "", raw_title).strip()
        if len(title) < 5:
            continue
        if link not in temp_dict:
            temp_dict[link] = title
    for link, title in list(temp_dict.items())[:20]:
        result.append({
            "title": title,
            "link": link,
            "desc": title,
            "pub": ""
        })
    return result

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
        return datetime(int(m2), int(m2), int(d), int(h), int(mi), tzinfo=timezone.utc)
    try:
        return datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except:
        return datetime.now(timezone.utc)

def is_news_recent(pub_str: str):
    dt = parse_pubdate(pub_str)
    return dt >= CUTOFF

# 新闻入库函数（修复get笔误）
news_pool = []
source_counter = {}
def add_news(source_cn: str, title: str, url: str, summary: str, pub_raw: str):
    if source_counter.get(source_cn, 0) >= MAX_PER_SOURCE:
        return False
    if not is_news_recent(pub_raw):
        return False
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

# 抓取主逻辑（无关键词过滤，全部入库）
def load_all_sources():
    for key, info in SOURCES.items():
        src_key = key
        src_name = info["name_cn"]
        src_type = info["type"]
        src_url = info["url"]
        time.sleep(random.uniform(0.8, 1.5))  # 加长延时防404
        try:
            html = fetch(src_url)
            item_list = []
            add_count = 0
            if src_type == "rss":
                try:
                    item_list = parse_rss(html)
                except Exception as xml_err:
                    print(f"{src_name} XML解析失败：{str(xml_err)}", file=sys.stderr)
                    item_list = []
            elif src_type == "web":
                print(f"===={src_name}页面源码片段====")
                print(html[:300])
                item_list = parse_web_list(html, src_key)
            # 无过滤直接入库
            for it in item_list:
                if add_news(src_name, it["title"], it["link"], it["desc"], it["pub"]):
                    add_count += 1
            print(f"{src_name} 原始新闻{len(item_list)}条，入库{add_count}条")
        except urllib.error.HTTPError as he:
            print(f"{src_name} HTTP错误 {he.code}: {he.reason}", file=sys.stderr)
        except Exception as e:
            print(f"{src_name} 抓取失败: {str(e)}", file=sys.stderr)

def main():
    load_all_sources()
    global news_pool
    news_pool = sorted(news_pool, key=lambda x: x["pub_sort_dt"], reverse=True)
    # UTC转北京时间字符串
    for item in news_pool:
        bj_dt = item["pub_sort_dt"] + timedelta(hours=8)
        item["pub_sort_dt"] = bj_dt.strftime("%Y-%m-%d %H:%M:%S")
    output = {
        "update_cst": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S 北京时间"),
        "total_count": len(news_pool),
        "source_stat": source_counter,
        "news": news_pool
    }
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n采集完成，最终入库新闻总数：{len(news_pool)}条")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err_data = {"error": str(e)}
        with open("news.json", "w", encoding="utf-8") as f:
            json.dump(err_data, ensure_ascii=False, indent=2)
        print(f"全局运行异常：{str(e)}", file=sys.stderr)
