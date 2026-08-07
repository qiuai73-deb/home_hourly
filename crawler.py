#!/usr/bin/env python3
"""国内新闻爬虫｜支持RSS(财新/雪球)+网页(新浪/凤凰)抓取，关键词过滤"""
import urllib.request, ssl, json, time, re, xml.etree.ElementTree as ET, sys, random
from datetime import datetime, timedelta, timezone

# 基础网络配置
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Referer": "https://www.baidu.com",
    "Accept-Language": "zh-CN,zh;q=0.9"
}

# ========== 核心配置区（可自行修改）==========
MAX_PER_SOURCE = 5  # 单媒体最多5条新闻
CUTOFF = datetime.now(timezone.utc) - timedelta(days=4)

# 白名单关键词：标题/摘要命中任意一条才保留新闻
FILTER_KEYWORDS = [
#    "GDP","降息","降准","LPR","央行","美元","人民币","金融","消费","地产","财经","突发","重大"  
#    "A股","指数","创业","科创","基金","ETF","回购","增持","IPO","纳斯达克","证监会","私募","公募","标普","龙头","指数"
#    "AI","大模型","芯片","半导体","华为","鸿蒙","新能源","苹果","科技","deepseek","比亚迪","小米","大疆","字节","腾讯","阿里","微信","英伟达","谷歌","抖音","kimi","豆包"
#    "政策","新规","国务院","统计局","进出口","外贸","汇率","特朗普"
]


# 黑名单关键词：命中直接丢弃垃圾荐股广告
BLACK_KEYWORDS = ["荐股","牛股","暴涨","必涨","福利","广告","理财课程","内部消息"]

# 新闻源配置
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
    # 网页抓取通道
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
        pub = item.findtext("pubDate", "")
        desc = re.sub(r"<.+?>", "", item.findtext("description", "")[:1000])
        if title and link:
            res.append({"title": title, "link": link, "desc": desc, "pub": pub})
    # Atom entry 兼容
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

# ========= 新增：网页列表提取通用函数 =========
def parse_web_list(html: str, source_name: str):
    result = []
    html_clean = html.replace("\n", "").replace("\r", "")
    # 通用匹配所有带title的a新闻链接，不限域名
    pattern = re.compile(r'<a[^>]+title="([^"]+)"[^>]+href="([^"]+)"')
    items = pattern.findall(html_clean)
    for title, link in items[:20]:
        # 过滤无效链接、锚点、JS链接
        if link.startswith("http") and len(title) > 3:
            result.append({
                "title": title.strip(),
                "link": link.strip(),
                "desc": title,
                "pub": ""
            })
    return result
# ==============================================

# 时间解析函数
def parse_pubdate(date_str: str):
    if not date_str:
        return datetime.now(timezone.utc)
    m1 = re.match(r'\w{3}, (\d{1,2}) (\w{3}) (\d{4}) (\d{2}):(\d{2}):(\d{2})', date_str.strip())
    if m1:
        month_map = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,"Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
        d, mon, y, h, mi, s = m1.groups()
        return datetime(int(y), month_map[mon], int(d), int(h), int(s), tzinfo=timezone.utc)
    m2 = re.match(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})', date_str)
    if m2:
        return datetime(int(m2), int(m2), int(d), int(h), int(mi), tzinfo=timezone.utc)
    # 国内中文时间兜底
    try:
        return datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except:
        return datetime.now(timezone.utc)

def is_news_recent(pub_str: str):
    dt = parse_pubdate(pub_str)
    return dt >= CUTOFF

# 关键词过滤（白名单+黑名单双重校验）
# def match_keyword(title: str, summary: str) -> bool:
#    full_text = title + summary
    # 命中黑名单直接过滤
#    for bw in BLACK_KEYWORDS:
#        if bw in full_text:
#            return False
    # 匹配白名单才保留
#    for kw in FILTER_KEYWORDS:
#        if kw in full_text:
#            return True
    return True

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

# 批量加载所有源：区分rss / web 分支
# 批量加载所有源：区分rss / web 分支
def load_all_sources():
    for key, info in SOURCES.items():
        src_key = key
        src_name = info["name_cn"]
        src_type = info["type"]
        src_url = info["url"]
        # 随机延时防封禁
        time.sleep(random.uniform(0.6, 1.2))
        try:
            html = fetch(src_url)
            item_list = []
            add_count = 0
            if src_type == "rss":
                try:
                    item_list = parse_rss(html)
                except Exception as xml_err:
                    print(f"{src_name} XML解析失败，第三方RSS接口已拦截：{str(xml_err)}", file=sys.stderr)
                    item_list = []
            elif src_type == "web":
                # 打印网页源码调试片段
                print(f"===={src_name}页面源码片段====")
                print(html[:300])
                item_list = parse_web_list(html, src_key)
            # 遍历过滤入库
            for it in item_list:
                if not match_keyword(it["title"], it["desc"]):
                    continue
                if add_news(src_name, it["title"], it["link"], it["desc"], it["pub"]):
                    add_count += 1
            print(f"{src_name} 页面原始新闻{len(item_list)}条，过滤后入库{add_count}条")
        except Exception as e:
            print(f"{src_name} 抓取失败: {str(e)}", file=sys.stderr)

def main():
    # 1. 加载RSS+网页全部新闻源
    load_all_sources()

    # 2. 全局按发布时间倒序
    global news_pool
    news_pool = sorted(news_pool, key=lambda x: x["pub_sort_dt"], reverse=True)

    # 3. UTC时间转换为北京时间字符串
    for item in news_pool:
        bj_dt = item["pub_sort_dt"] + timedelta(hours=8)
        item["pub_sort_dt"] = bj_dt.strftime("%Y-%m-%d %H:%M:%S")

    # 4. 输出和前端完全兼容的json结构
    output = {
        "update_cst": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S 北京时间"),
        "total_count": len(news_pool),
        "source_stat": source_counter,
        "news": news_pool
    }
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n全部采集结束，最终匹配关键词新闻总数：{len(news_pool)}条")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err_data = {"error": str(e)}
        with open("news.json", "w", encoding="utf-8") as f:
            json.dump(err_data, ensure_ascii=False, indent=2)
        print(f"程序全局异常：{str(e)}", file=sys.stderr)
