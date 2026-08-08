#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
国内财经新闻聚合爬虫 v3

功能：
1. RSS + Web 多源抓取
2. 自动提取发布时间
3. Web新闻详情页补抓时间
4. 按发布时间排序
5. 输出最新15条
6. 兼容原 news.json 格式
7. GitHub Pages 使用
"""

import ssl
import json
import time
import re
import html
import urllib.request
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup



# =====================================================
# 基础配置
# =====================================================

DEFAULT_MAX = 8


SPECIAL_MAX = {

    "ths": 20,

    "eastmoney": 20,

    "sina": 20,

    "cls": 20

}


# 最终网页显示数量

FINAL_LIMIT = 15



# 保留最近多少天新闻

CUTOFF_DAYS = 7



USER_AGENT = (

    "Mozilla/5.0 "

    "(Windows NT 10.0; Win64; x64) "

    "AppleWebKit/537.36 "

    "Chrome/120 Safari/537.36"

)



# =====================================================
# 关键词过滤
# =====================================================


KEYWORDS = [

    "GDP",
    "降息",
    "加息",
    "LPR",
    "央行",
    "美元",
    "人民币",

    "金融",
    "消费",
    "券商",
    "证券",

    "地产",
    "财经",
    "突发",
    "重大",

    "黄金",
    "CPI",

    "A股",
    "指数",
    "创业板",
    "科创",

    "基金",
    "ETF",
    "IPO",

    "资金",
    "利率",

    "纳斯达克",

    "证监会",

    "AI",
    "大模型",

    "芯片",
    "半导体",

    "华为",
    "鸿蒙",

    "新能源",

    "苹果",

    "deepseek",

    "比亚迪",
    "小米",

    "腾讯",
    "阿里",

    "银行",

    "高盛",

    "利润",

    "统计局",

    "汇率"

]


KEYWORD_PATTERN = re.compile(
    "|".join(KEYWORDS),
    re.IGNORECASE
)



def is_relevant(text):

    return bool(
        KEYWORD_PATTERN.search(text)
    )



# =====================================================
# 新闻源
# =====================================================


SOURCES = {


    "caixin": {

        "name_cn": "财新",

        "url":
        "https://quanwenrss.com/caixin",

        "type":
        "rss"

    },


    "snowball": {

        "name_cn": "雪球",

        "url":
        "https://xueqiu.com/hots/topic/rss",

        "type":
        "rss"

    },


    "ths": {

        "name_cn": "同花顺",

        "url":
        "https://www.10jqka.com.cn/classic",

        "type":
        "web"

    },


    "eastmoney": {

        "name_cn": "东方财富",

        "url":
        "https://finance.eastmoney.com/",

        "type":
        "web"

    },


    "sina": {

        "name_cn": "新浪财经",

        "url":
        "https://finance.sina.com.cn/",

        "type":
        "web"

    },


    "cls": {

        "name_cn": "财联社",

        "url":
        "https://www.cls.cn/",

        "type":
        "web"

    }

}




# =====================================================
# 网络请求
# =====================================================


def fetch_web(url, timeout=15):

    headers = {

        "User-Agent":
        USER_AGENT

    }


    try:

        r = requests.get(

            url,

            headers=headers,

            timeout=timeout

        )


        r.encoding = (
            r.apparent_encoding
            or
            "utf-8"
        )


        return r.text


    except Exception as e:


        print(
            "请求失败:",
            url,
            e
        )


        return ""





def fetch_rss(url, timeout=15):


    ctx = ssl.create_default_context()

    ctx.check_hostname = False

    ctx.verify_mode = ssl.CERT_NONE



    req = urllib.request.Request(

        url,

        headers={

            "User-Agent":
            USER_AGENT

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





# =====================================================
# RSS解析
# =====================================================


def parse_rss(xml_text):


    result = []


    try:

        root = ET.fromstring(
            xml_text
        )


    except Exception:


        return result




    for item in root.findall(
        ".//item"
    ):


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


            result.append({

                "title":
                title,

                "link":
                link,

                "summary":
                BeautifulSoup(

                    summary,

                    "html.parser"

                ).text[:300],

                "pub":
                pub

            })



    return result
# =====================================================
# 时间提取增强
# =====================================================


TIME_PATTERNS = [

    # 2026-08-08 10:30

    r"\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}",


    # 2026/08/08 10:30

    r"\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}",


    # 2026年8月8日 10:30

    r"\d{4}年\d{1,2}月\d{1,2}日\s+\d{1,2}:\d{2}",


    # 8月8日 10:30

    r"\d{1,2}月\d{1,2}日\s+\d{1,2}:\d{2}",


    # 单独时间

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
    从新闻标题附近寻找时间
    """

    try:


        parents = [

            element

        ] + list(

            element.parents

        )[:6]



        for p in parents:


            text = p.get_text(

                " ",

                strip=True

            )


            result = extract_time(
                text
            )


            if result:

                return result



    except:


        pass



    return ""






# =====================================================
# Web新闻列表解析
# =====================================================


def parse_web_generic(
        page,
        base_url
):


    result = []



    if not page:

        return result



    soup = BeautifulSoup(

        page,

        "html.parser"

    )



    seen = set()



    for a in soup.find_all(

        "a",

        href=True

    ):



        title = a.get_text(

            strip=True

        )



        href = a.get(

            "href",

            ""

        )



        if not title:

            continue



        if len(title) < 6:

            continue



        if len(title) > 120:

            continue



        url = urljoin(

            base_url,

            href

        )



        if url in seen:

            continue



        bad = [

            "首页",

            "登录",

            "注册",

            "下载",

            "客户端",

            "关于"

        ]



        if any(

            x in title

            for x in bad

        ):

            continue




        seen.add(url)




        pub = extract_time_from_element(

            a

        )




        result.append({

            "title":
            title,


            "link":
            url,


            "summary":
            title,


            "pub":
            pub

        })



        if len(result) >= 100:

            break



    return result






# =====================================================
# 详情页发布时间补抓
# =====================================================


def fetch_detail_time(url):

    """
    访问新闻详情页
    获取真正发布时间
    """

    try:


        page = fetch_web(

            url

        )


        if not page:

            return ""



        soup = BeautifulSoup(

            page,

            "html.parser"

        )



        # 1. time标签

        for t in soup.find_all(

            "time"

        ):


            txt = t.get_text(

                strip=True

            )


            if txt:

                return txt




        # 2. 常见发布时间class


        keywords = [

            "time",

            "date",

            "publish",

            "pub",

            "create"

        ]



        for tag in soup.find_all(

            ["span",

             "div",

             "p"]

        ):


            cls = " ".join(

                tag.get(

                    "class",

                    []

                )

            ).lower()



            if any(

                k in cls

                for k in keywords

            ):


                txt = tag.get_text(

                    strip=True

                )


                t = extract_time(

                    txt

                )


                if t:

                    return t





        # 3. 全文搜索

        text = soup.get_text(

            " ",

            strip=True

        )


        return extract_time(

            text

        )



    except Exception:


        return ""







# =====================================================
# 时间转换
# =====================================================


def parse_pubdate(text):


    if not text:

        return None




    text = text.strip()



    formats = [


        "%Y-%m-%d %H:%M",


        "%Y/%m/%d %H:%M",


        "%Y年%m月%d日 %H:%M"


    ]



    for fmt in formats:


        try:


            dt = datetime.strptime(

                text,

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







# =====================================================
# 新闻池
# =====================================================


news_pool = []


source_counter = {}






def add_news(

        source,

        title,

        url,

        summary,

        pub,

        limit

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

    ) >= limit:

        return False





    dt = parse_pubdate(

        pub

    )




    # 如果列表页没有时间

    # 去详情页补抓


    if dt is None:


        detail = fetch_detail_time(

            url

        )


        dt = parse_pubdate(

            detail

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

        datetime.now(

            timezone.utc

        )

        -

        dt

    ).days > CUTOFF_DAYS:


        return False





    for old in news_pool:


        if old["url"] == url:

            return False


        if old["title"] == title:

            return False





    bj = dt + timedelta(

        hours=8

    )



    news_pool.append({

        "source":
        source,


        "title":
        title,


        "url":
        url,


        "summary":
        summary,


        "pub_dt":
        dt,


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
# =====================================================
# 来源处理
# =====================================================


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

            "❌",

            name,

            "抓取失败:",

            e

        )


        return




    print(

        "📌",

        name,

        "原始",

        len(items),

        "条"

    )




    count = 0



    for item in items:



        ok = add_news(

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

        )



        if ok:


            count += 1



        if count >= limit:

            break




    print(

        "✅",

        name,

        "保留",

        count,

        "条"

    )








# =====================================================
# HTML生成
# =====================================================


def generate_html():


    bj_now = datetime.now(

        timezone(

            timedelta(

                hours=8

            )

        )

    )



    content = f"""

<!DOCTYPE html>

<html lang="zh-CN">

<head>


<meta charset="UTF-8">


<meta name="viewport"

content="width=device-width,initial-scale=1.0">


<title>

财经新闻聚合

</title>


<style>


body {{

background:#f5f7fa;

font-family:

"Microsoft YaHei",

Arial;

margin:0;

padding:20px;

}}



.container {{

max-width:1300px;

margin:auto;

}}



.header {{

background:#1e3c72;

color:white;

padding:20px;

border-radius:12px;

margin-bottom:20px;

}}



.grid {{

display:grid;

grid-template-columns:

repeat(auto-fit,minmax(350px,1fr));

gap:18px;

}}



.card {{

background:white;

padding:18px;

border-radius:12px;

box-shadow:

0 2px 8px rgba(0,0,0,.08);

}}



.title {{

font-size:18px;

font-weight:bold;

line-height:1.5;

}}



.title a {{

color:#174a8b;

text-decoration:none;

}}



.time {{

font-size:13px;

color:#999;

font-weight:normal;

}}



.summary {{

margin-top:10px;

color:#555;

font-size:14px;

line-height:1.6;

}}



.source {{

margin-top:12px;

font-size:13px;

color:#666;

}}



</style>


</head>


<body>


<div class="container">


<div class="header">


<h2>

📰 财经新闻聚合

</h2>


<div>

更新时间：

{bj_now.strftime("%Y-%m-%d %H:%M")}

北京时间

<br>

最新：

{len(news_pool)}

条

</div>


</div>


<div class="grid">

"""



    for n in news_pool:


        show_time = n["pub_beijing"][-5:]



        content += f"""

<div class="card">


<div class="title">


<a href="{n['url']}"

target="_blank">


{html.escape(n['title'])}


<span class="time">

🕒{show_time}

</span>


</a>


</div>



<div class="summary">


{html.escape(n['summary'][:180])}

</div>



<div class="source">


来源：

{n['source']}


</div>


</div>

"""



    content += """

</div>

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

            content

        )








# =====================================================
# 主程序
# =====================================================


def main():


    global news_pool

    global source_counter



    news_pool = []

    source_counter = {}




    print(

        "====== 开始抓取新闻 ======"

    )




    for key,cfg in SOURCES.items():


        process_source(

            key,

            cfg

        )


        time.sleep(

            1

        )





    # =====================
    # 按发布时间排序
    # =====================


    news_pool.sort(

        key=lambda x:

        x["pub_dt"],

        reverse=True

    )




    # 只保留最新15条


    news_pool = news_pool[

        :FINAL_LIMIT

    ]




    # =====================
    # 输出JSON
    # 保持原网页兼容
    # =====================


    output = {


        "update_cst":

        datetime.now(

            timezone(

                timedelta(

                    hours=8

                )

            )

        ).strftime(

            "%Y-%m-%d %H:%M:%S 北京时间"

        ),



        "total_count":

        len(news_pool),



        "source_stat":

        source_counter,



        "news":

        [

            {


                "source":

                n["source"],


                "title":

                n["title"],


                "url":

                n["url"],


                "summary":

                n["summary"],


                "pub_beijing":

                n["pub_beijing"],



                "pub_time":

                n["pub_dt"].strftime(

                    "%Y-%m-%d %H:%M:%S"

                )

            }


            for n in news_pool

        ]

    }





    with open(

        "news.json",

        "w",

        encoding="utf-8"

    ) as f:


        json.dump(

            output,

            f,

            ensure_ascii=False,

            indent=2

        )





    generate_html()




    print(

        "🎉 完成",

        len(news_pool),

        "条新闻"

    )






if __name__ == "__main__":


    try:

        main()


    except Exception as e:


        print(

            "❌运行错误:",

            e

        )
