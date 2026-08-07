#!/usr/bin/env python3
"""国内新闻爬虫｜修复XML解析报错、404失效RSS、容错清洗HTML实体"""
import urllib.request, ssl, json, time, re, xml.etree.ElementTree as ET, sys, random
from datetime import datetime, timedelta, timezone

# 浏览器完整请求头，降低拦截概率
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0 Safari/537.36",
    "Referer": "https://www.baidu.com",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
}
# SSL忽略证书
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# ========== 核心配置 ==========
MAX_PER_SOURCE = 999  # 取消条数限制，全部入库
CUTOFF = datetime.now(timezone.utc) - timedelta(days=4)

# 【仅保留可访问RSS，全部失效媒体删除】
SOURCES = {
    # 财新第三方RSS中转（原生官网RSS已关闭）
    "caixin": {
        "name": "caixin",
        "name_cn": "财新",
        "url": "https://rsshub.app/caixin/latest",
        "type": "rss"
    },
    # 雪球热帖稳定RSS
    "snowball": {
        "name": "snowball",
        "name_cn": "雪球热榜",
        "url": "https://rsshub.app/xueqiu/hots",
        "type": "rss"
    }
    # 新浪/央视/凤凰官网RSS永久404，直接删除；网页源全部失效，不再配置
}
# ==============================================

# 通用网页请求
def fetch(url: str, timeout=15):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", "replace")

# 【新增关键：XML清洗函数，修复&nbps、标签错位、非法实体】
def clean_rss_xml(xml_str: str) -> str:
    # 替换HTML实体为合法XML实体
    xml_str = re.sub(r"&nbsp;", "&#160;", xml_str)
    xml_str = re.sub(r"&mdash;", "&#8212;", xml_str)
    xml_str = re.sub(r"&ldquo;", "&#8220;", xml_str)
    xml_str = re.sub(r"&rdquo;", "&#8221;", xml_str)
    xml_str = re.sub(r"&lsquo;", "&#8216;", xml_str)
    xml_str = re.sub(r"&rsquo;", "&#8217;", xml_str)
    # 移除未闭合、非法HTML标签，防止mismatched tag
    xml_str = re.sub(r"<br\s*/?>", "", xml_str)
    xml_str = re.sub(r"<hr\s*/?>", "", xml_str)
    xml_str = re.sub(r'<img.*?>', "", xml_str)
    return xml

# 解析标准RSS/Atom（增加清洗前置步骤）
def parse_rss(raw_xml: str):
    res = []
    try:
        clean_xml = clean_rss_xml(raw_xml)
        root = ET.fromstring(clean_xml)
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
    except Exception as e:
        raise Exception(f"清洗后XML仍解析失败: {str(e)}")
    return res

# 网页解析函数（保留，但已知全部网站0结果，仅兼容旧代码）
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
    m1 = re.match(r'\w{3}, (\d{1,2}) (\w{3}) (\d{4}) (\d{2}):(\d{2})', date_str.strip())
    if m1:
        month_map = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,"Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
        d, mon, y, h, mi, s = m1.groups()
        return datetime(int(y), month_map[mon], int(h), int(mi), tzinfo=timezone.utc)
    m2 = re.match(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})', date_str)
    if m2:
        return datetime(int(m2), int(m2), int(d), int(h), tzinfo=timezone.utc)
    try:
        return datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except:
        return datetime.now(timezone.utc)

def is_news_recent(pub_str: str):
    dt = parse_pubdate(pub_str)
    return dt >= CUTOFF

# 全局存储
news_pool = []
source_counter = {}

# 入库函数（修复get笔误）
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

# 批量抓取所有源
def load_all_sources():
    for key, info in SOURCES.items():
        src_key = key
        src_name = info["name_cn"]
        src_type = info["type"]
        src_url = info["url"]
        # 随机延时防封禁
        time.sleep(random.uniform(1.0, 1.8))
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
            # 无关键词过滤，全部入库
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
