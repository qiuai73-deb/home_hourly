import urllib.parse, urllib.request, json, time

def translate_single(text: str) -> str:
    """单条英文标题翻译为中文"""
    if not text.strip():
        return ""
    url = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        "sl": "en",
        "tl": "zh-CN",
        "dt": "t",
        "q": text
    }
    full_url = url + "?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        req = urllib.request.Request(full_url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        return "".join([seg[0] for seg in data[0]])
    except Exception:
        return text

def batch_translate(text_list: list[str]) -> list[str]:
    """批量翻译，间隔防封禁"""
    result = []
    for txt in text_list:
        result.append(translate_single(txt))
        time.sleep(0.4)
    return result
