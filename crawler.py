#!/usr/bin/env python3
"""
国内多源新闻聚合爬虫（增强发布时间版）

功能：
- RSS + Web 多源抓取
- 自动进入新闻详情页补抓发布时间
- 按北京时间排序
- 首页显示最新15条
- 标题后显示发布时间
- 输出 index.html 和 news.json
"""

import ssl
import json
import time
import re
import html
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


# =========================
# 配置
# =========================

DEFAULT_MAX = 10

# 每个来源最多抓取数量
SPECIAL_MAX = {
    "ths": 20,
    "eastmoney": 20,
    "sina": 20
}

# 最终网页显示数量
FINAL_LIMIT = 15

# 保存最近几天新闻
CUTOFF_DAYS = 7


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
)


# =========================
# 关键词过滤
# =========================

KEYWORDS = [
    "GDP","降息","加息","LPR","央行","美元","人民币",
    "金融","消费","券商","证券","地产","财经",
    "突发","重大","独家","黄金","CPI",
    "A股","指数","创业","科创","基金","ETF",
    "IPO","资金","利率","纳斯达克",
    "证监会","私募","公募",
    "AI","大模型","芯片","半导体",
    "华为","鸿蒙","新能源",
    "苹果","科技","deepseek",
    "比亚迪","小米","腾讯","阿里",
    "银行","高盛","利润","统计局",
    "汇率"
]


KEYWORD_PATTERN = re.compile(
    "|".join(KEYWORDS),
    re.IGNORECASE
)


def is_relevant(text):
    return bool(KEYWORD_PATTERN.search(text))


# =========================
# 新闻源
# =========================

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
        "url": "https://finance.eastmoney.com/",
        "type": "web"
    },


    "sina": {
        "name_cn": "新浪财经",
        "url": "https://finance.sina.com.cn/",
        "type": "web"
    },


    "cls": {
        "name_cn": "财联社",
        "url": "https://www.cls.cn/",
        "type": "web"
    }

}


# =========================
# 网络请求
# =========================

def fetch_web(url, timeout=15):

    headers = {
        "User-Agent": USER_AGENT
    }

    try:

        r = requests.get(
            url,
            headers=headers,
            timeout=timeout
        )

        r.encoding = r.apparent_encoding

        return r.text

    except Exception:

        return ""



def fetch_rss(url, timeout=15):

    ctx = ssl.create_default_context()

    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE


    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT
        }
    )


    with urllib.request.urlopen(
        req,
        timeout=timeout,
        context=ctx
    ) as resp:

        return resp.read().decode(
            "utf-8",
            "ignore"
        )



# =========================
# RSS解析
# =========================

def parse_rss(xml):

    import xml.etree.ElementTree as ET

    results = []


    try:

        root = ET.fromstring(xml)

    except:

        return results



    for item in root.findall(".//item"):


        title = item.findtext(
            "title",
            ""
        ).strip()


        link = item.findtext(
            "link",
            ""
        ).strip()


        summary = item.findtext(
            "description",
            ""
        )


        pub = item.findtext(
            "pubDate",
            ""
        )


        if title and link:

            results.append({

                "title": title,

                "link": link,

                "summary": BeautifulSoup(
                    summary,
                    "html.parser"
                ).text[:300],

                "pub": pub

            })


    return results



# =========================
# 时间提取
# =========================

TIME_PATTERNS = [

    r"\d{4}-\d{1,2}-\d{1,2}\s\d{1,2}:\d{2}",

    r"\d{4}/\d{1,2}/\d{1,2}\s\d{1,2}:\d{2}",

    r"\d{4}年\d{1,2}月\d{1,2}日\s\d{1,2}:\d{2}",

    r"\d{1,2}月\d{1,2}日\s\d{1,2}:\d{2}",

    r"\d{1,2}:\d{2}"

]



def extract_time(text):

    if not text:

        return ""


    for p in TIME_PATTERNS:

        m = re.search(
            p,
            text
        )

        if m:

            return m.group()


    return ""



def extract_time_from_element(element):

    """
    从新闻列表附近提取时间
    """

    for parent in (
        [element]
        +
        list(element.parents)[:5]
    ):


        text = parent.get_text(
            " ",
            strip=True
        )


        result = extract_time(text)


        if result:

            return result


    return ""



# =========================
# 详情页时间补抓
# =========================

def fetch_detail_time(url):

    """
    访问新闻详情页
    提取发布时间
    """

    try:

        page = fetch_web(url)

        if not page:

            return ""


        soup = BeautifulSoup(
            page,
            "html.parser"
        )


        # 优先查找time标签

        for t in soup.find_all("time"):

            if t.text.strip():

                return t.text.strip()



        # 全文搜索

        text = soup.get_text(
            " ",
            strip=True
        )


        return extract_time(text)



    except Exception:

        return ""
# =========================
# Web列表页解析
# =========================

def parse_web_generic(page, base_url):

    soup = BeautifulSoup(
        page,
        "html.parser"
    )

    results = []

    seen = set()


    for a in soup.find_all(
        "a",
        href=True
    ):

        title = a.get_text(
            strip=True
        )

        href = a["href"]


        if not title:
            continue


        if len(title) < 6 or len(title) > 100:
            continue


        url = urljoin(
            base_url,
            href
        )


        if url in seen:
            continue


        # 排除无关链接

        bad_words = [
            "首页",
            "登录",
            "注册",
            "下载",
            "关于",
            "客服",
            "广告"
        ]


        if any(
            x in title
            for x in bad_words
        ):
            continue



        seen.add(url)


        # 列表页尝试获取时间

        pub = extract_time_from_element(a)



        results.append({

            "title": title,

            "link": url,

            "summary": title,

            "pub": pub

        })


        if len(results) >= 100:

            break



    return results



# =========================
# 时间标准化
# =========================

def parse_pubdate(text):

    if not text:

        return None


    text = text.strip()



    patterns = [

        (
            r"(\d{4})-(\d{1,2})-(\d{1,2})\s(\d{1,2}):(\d{2})",
            "%Y-%m-%d %H:%M"
        ),

        (
            r"(\d{4})/(\d{1,2})/(\d{1,2})\s(\d{1,2}):(\d{2})",
            "%Y/%m/%d %H:%M"
        )

    ]



    for pattern, fmt in patterns:


        m = re.search(
            pattern,
            text
        )


        if m:

            try:

                dt = datetime.strptime(
                    m.group(),
                    fmt
                )


                return dt.replace(
                    tzinfo=timezone.utc
                )


            except:

                pass



    # 只有时间

    m = re.search(
        r"(\d{1,2}):(\d{2})",
        text
    )


    if m:


        now = datetime.now(
            timezone.utc
        )


        return datetime(
            now.year,
            now.month,
            now.day,
            int(m.group(1)),
            int(m.group(2)),
            tzinfo=timezone.utc
        )



    return None




# =========================
# 新闻池
# =========================

news_pool = []

source_counter = {}



def add_news(
        source,
        title,
        url,
        summary,
        pub,
        max_limit
):


    if not title:

        return False



    if not is_relevant(
        title + summary
    ):

        return False



    if source_counter.get(
        source,
        0
    ) >= max_limit:

        return False



    dt = parse_pubdate(pub)



    # 如果没有时间，尝试详情页

    if dt is None:


        detail_time = fetch_detail_time(url)


        dt = parse_pubdate(
            detail_time
        )



    # 没时间排最后

    if dt is None:

        dt = datetime(
            2000,
            1,
            1,
            tzinfo=timezone.utc
        )



    # 过滤旧新闻

    if (
        datetime.now(timezone.utc)
        -
        dt
    ).days > CUTOFF_DAYS:

        return False



    # 去重

    for n in news_pool:


        if n["url"] == url:

            return False


        if n["title"] == title:

            return False




    bj = (
        dt
        +
        timedelta(hours=8)
    )


    news_pool.append({

        "source": source,

        "title": title,

        "url": url,

        "summary": summary,

        "pub_dt": dt,

        "pub_beijing":
            bj.strftime(
                "%Y-%m-%d %H:%M"
            )

    })



    source_counter[source] = (
        source_counter.get(
            source,
            0
        )
        +
        1
    )


    return True




# =========================
# 单个来源处理
# =========================

def process_source(
        key,
        cfg
):


    name = cfg["name_cn"]


    limit = SPECIAL_MAX.get(
        key,
        DEFAULT_MAX
    )


    items = []



    try:


        if cfg["type"] == "rss":


            xml = fetch_rss(
                cfg["url"]
            )


            items = parse_rss(
                xml
            )



        elif cfg["type"] == "web":


            page = fetch_web(
                cfg["url"]
            )


            items = parse_web_generic(
                page,
                cfg["url"]
            )



    except Exception as e:


        print(
            "抓取失败:",
            name,
            e
        )


        return




    # 先排序

    items.sort(

        key=lambda x:
        parse_pubdate(
            x.get("pub","")
        )
        or datetime(
            2000,
            1,
            1,
            tzinfo=timezone.utc
        ),

        reverse=True

    )



    count = 0


    for item in items:


        if add_news(

            name,

            item["title"],

            item["link"],

            item.get(
                "summary",
                ""
            ),

            item.get(
                "pub",
                ""
            ),

            limit

        ):

            count += 1



        if count >= limit:

            break



    print(
        name,
        "完成",
        count
    )



# =========================
# HTML生成
# =========================

def generate_html():


    now = datetime.now(
        timezone(
            timedelta(hours=8)
        )
    )



    html_content = f"""
<!DOCTYPE html>
<html>
<head>

<meta charset="utf-8">

<title>财经新闻聚合</title>


<style>

body {{

font-family:
Arial,
"Microsoft YaHei";

background:#f5f5f5;

padding:20px;

}}


.container {{

max-width:1200px;

margin:auto;

}}


.news {{

background:white;

padding:18px;

margin-bottom:15px;

border-radius:10px;

}}


.title {{

font-size:18px;

font-weight:bold;

}}


.title a {{

text-decoration:none;

color:#1a4d8f;

}}


.time {{

color:#999;

font-size:13px;

}}


.source {{

color:#666;

font-size:14px;

margin-top:8px;

}}

</style>


</head>


<body>


<div class="container">


<h2>
📰 财经新闻
</h2>


<p>
更新时间:
{now.strftime("%Y-%m-%d %H:%M")}
北京时间
</p>

"""


    for n in news_pool:


        short_time = (
            n["pub_beijing"]
            [-5:]
        )


        html_content += f"""

<div class="news">


<div class="title">

<a href="{n['url']}" target="_blank">

{html.escape(n['title'])}

<span class="time">

🕒 {short_time}

</span>

</a>

</div>


<div class="source">

{n['source']}

</div>


</div>


"""



    html_content += """

</div>

</body>

</html>

"""



    with open(
        "index.html",
        "w",
        encoding="utf-8"
    ) as f:


        f.write(
            html_content
        )



# =========================
# 主程序
# =========================

def main():


    global news_pool, source_counter


    news_pool = []

    source_counter = {}



    for key,cfg in SOURCES.items():


        process_source(
            key,
            cfg
        )


        time.sleep(1)



    # 按发布时间排序

    news_pool.sort(

        key=lambda x:
        x["pub_dt"],

        reverse=True

    )



    # 只保留最新15条

    news_pool = news_pool[:FINAL_LIMIT]



    data = {


        "update":

        datetime.now(
            timezone(
                timedelta(hours=8)
            )
        ).strftime(
            "%Y-%m-%d %H:%M"
        ),


        "count":

        len(news_pool),



        "news":

        news_pool

    }



    with open(
        "news.json",
        "w",
        encoding="utf-8"
    ) as f:


        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
            default=str
        )



    generate_html()



    print(
        "完成，共",
        len(news_pool),
        "条"
    )




if __name__ == "__main__":


    main()
