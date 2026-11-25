# -*- coding: utf-8 -*-
"""
Nodaroo 个人聚合面板 v1
单文件、纯标准库，无需 pip 安装任何包。
部署：放到 /home/container/app.py，面板点 Restart 即可。

想改内容？只动下面的「配置区」就行。
"""
import os
import json
import time
import html
import threading
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ===================== 配置区 =====================

# 城市（天气用）：名称 + 经纬度
CITY = "深圳"
LAT, LON = 22.5431, 114.0579

# RSS 订阅源：想加就往里添，(名称, 地址)
RSS_FEEDS = [
    ("少数派", "https://sspai.com/feed"),
    ("linux.do 最新", "https://linux.do/latest.rss"),
    ("阮一峰周刊", "https://www.ruanyifeng.com/blog/atom.xml"),
]

# 常用工具/快捷入口：(名称, 链接)
QUICK_LINKS = [
    ("Nodaroo 面板", "https://us2.nodaroo.fun"),
    ("box.simular.ai", "https://box.simular.ai"),
    ("百度", "https://www.baidu.com"),
]

# Simular 盒子状态（可选）：在面板环境变量里加 SIMULAR_KEY=你的cb_密钥 即启用；
# 不配置就显示"未启用"，不影响其他功能。
SIMULAR_KEY = os.environ.get("SIMULAR_KEY", "")

# 数据刷新间隔（秒）
REFRESH_INTERVAL = 600

# ================================================

# 个人配置：盒子目录下若有 config.json 则覆盖上面的默认值（此文件不进仓库）。
# 格式示例：
# {
#   "city": "深圳", "lat": 22.5431, "lon": 114.0579,
#   "rss_feeds": [["名称", "https://..."]],
#   "quick_links": [["名称", "https://..."]],
#   "refresh_interval": 600
# }
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
try:
    with open(CONFIG_FILE, encoding="utf-8") as _f:
        _ov = json.load(_f)
    CITY = _ov.get("city", CITY)
    LAT = _ov.get("lat", LAT)
    LON = _ov.get("lon", LON)
    RSS_FEEDS = [tuple(x) for x in _ov.get("rss_feeds", RSS_FEEDS)]
    QUICK_LINKS = [tuple(x) for x in _ov.get("quick_links", QUICK_LINKS)]
    REFRESH_INTERVAL = int(_ov.get("refresh_interval", REFRESH_INTERVAL))
except Exception:
    pass

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_cache.json")
_lock = threading.Lock()


def http_get(url, timeout=10, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0 dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_rss(name, url, limit=6):
    """极简 RSS/Atom 解析，取最新若干条标题和链接。"""
    items = []
    raw = http_get(url, timeout=15)
    root = ET.fromstring(raw)
    # RSS 2.0
    for item in root.iter("item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pub = item.findtext("pubDate") or ""
        if title:
            items.append({"title": title.strip(), "link": link.strip(), "date": pub.strip()})
        if len(items) >= limit:
            break
    # Atom
    if not items:
        ns = "{http://www.w3.org/2005/Atom}"
        for entry in root.iter(ns + "entry"):
            title = entry.findtext(ns + "title") or ""
            link_el = entry.find(ns + "link")
            link = link_el.get("href") if link_el is not None else ""
            pub = entry.findtext(ns + "updated") or ""
            if title:
                items.append({"title": title.strip(), "link": link, "date": pub.strip()})
            if len(items) >= limit:
                break
    return {"name": name, "ok": True, "items": items}


WMO_CODES = {0: "晴", 1: "基本晴", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇",
             51: "小毛雨", 53: "毛雨", 55: "大毛雨", 61: "小雨", 63: "中雨", 65: "大雨",
             71: "小雪", 73: "中雪", 75: "大雪", 80: "阵雨", 81: "阵中雨", 82: "阵暴雨",
             95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷雨冰雹"}


def fetch_weather():
    """Open-Meteo 当前天气，免密钥。"""
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
           "&current=temperature_2m,apparent_temperature,relative_humidity_2m,weather_code"
           "&timezone=Asia%2FShanghai")
    j = json.loads(http_get(url, timeout=15))
    cur = j["current"]
    return {
        "city": CITY,
        "temp": round(cur["temperature_2m"]),
        "feels": round(cur["apparent_temperature"]),
        "desc": WMO_CODES.get(cur["weather_code"], "—"),
        "humidity": cur["relative_humidity_2m"],
    }


def fetch_simular():
    """Simular 盒子状态，未配置密钥时返回 None。"""
    if not SIMULAR_KEY:
        return None
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "box_list", "arguments": {}},
    }).encode()
    req = urllib.request.Request(
        "https://mcp.box.simular.ai/mcp", data=payload,
        headers={
            "Authorization": f"Bearer {SIMULAR_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode()
    # 兼容 SSE 输出（data: {...}）
    if body.startswith("data:"):
        body = body.split("data:", 1)[1].strip().split("\n")[0]
    j = json.loads(body)
    text = j.get("result", {}).get("content", [])
    raw = text[0].get("text", "[]") if text else "[]"
    boxes = json.loads(raw)
    if isinstance(boxes, dict):
        boxes = boxes.get("boxes", [boxes])
    out = []
    for b in boxes:
        out.append({
            "name": b.get("name", "?"),
            "status": b.get("status", "unknown"),
        })
    return {"boxes": out}


def refresh_once():
    """抓一轮所有数据，写进缓存文件；单项失败不影响其他。"""
    data = {"time": time.strftime("%Y-%m-%d %H:%M"), "rss": [], "weather": None, "simular": None}
    for name, url in RSS_FEEDS:
        try:
            data["rss"].append(fetch_rss(name, url))
        except Exception as e:
            data["rss"].append({"name": name, "ok": False, "error": str(e)[:80], "items": []})
    try:
        data["weather"] = fetch_weather()
    except Exception:
        data["weather"] = None
    try:
        data["simular"] = fetch_simular()
    except Exception:
        data["simular"] = {"error": "获取失败（密钥或网络问题）"}
    with _lock:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    return data


def load_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"time": "尚未刷新", "rss": [], "weather": None, "simular": None}


def refresher():
    while True:
        try:
            refresh_once()
        except Exception:
            pass
        time.sleep(REFRESH_INTERVAL)


PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>我的面板</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; padding:16px; background:#12121c; color:#e8e8f0;
         font-family:-apple-system,"PingFang SC",sans-serif; }}
  h1 {{ font-size:22px; margin:4px 0 2px; }}
  .sub {{ color:#8888a0; font-size:12px; margin-bottom:16px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:14px; }}
  .card {{ background:#1c1c2a; border-radius:14px; padding:14px 16px; }}
  .card h2 {{ font-size:15px; margin:0 0 10px; color:#b8a8ff; }}
  .card ul {{ list-style:none; margin:0; padding:0; }}
  .card li {{ padding:5px 0; border-bottom:1px solid #2a2a3c; font-size:13px; }}
  .card li:last-child {{ border-bottom:none; }}
  .card a {{ color:#dcdcf0; text-decoration:none; }}
  .card a:hover {{ color:#b8a8ff; }}
  .muted {{ color:#66667e; font-size:12px; }}
  .links a {{ display:inline-block; margin:4px 8px 4px 0; padding:6px 12px;
              background:#2a2a40; border-radius:8px; font-size:13px; color:#dcdcf0;
              text-decoration:none; }}
  .status-on {{ color:#7fe8a0; }} .status-off {{ color:#ff9a9a; }}
  .weather-big {{ font-size:30px; font-weight:600; }}
  .err {{ color:#ff9a9a; font-size:12px; }}
</style>
</head>
<body>
<h1>🏠 我的面板</h1>
<div class="sub">数据更新于 {updated}</div>
<div class="grid">

  <div class="card">
    <h2>🌤 天气 · {w_city}</h2>
    {weather_html}
  </div>

  <div class="card">
    <h2>🖥 Simular 盒子</h2>
    {simular_html}
  </div>

  <div class="card links">
    <h2>⚡ 快捷入口</h2>
    {links_html}
  </div>

  {rss_html}
</div>
</body></html>"""


def render():
    d = load_cache()
    # 天气
    if d.get("weather"):
        w = d["weather"]
        weather_html = (f'<div class="weather-big">{html.escape(w["desc"])} {w["temp"]}°C</div>'
                        f'<div class="muted">体感 {w["feels"]}°C · 湿度 {w["humidity"]}%</div>')
        w_city = w["city"]
    else:
        weather_html, w_city = '<div class="muted">暂未获取到</div>', CITY
    # 盒子
    s = d.get("simular")
    if s is None:
        simular_html = '<div class="muted">未启用。在面板环境变量加 SIMULAR_KEY 即可显示盒子状态。</div>'
    elif s.get("error"):
        simular_html = f'<div class="err">{html.escape(s["error"])}</div>'
    else:
        rows = "".join(
            f'<li>{"<span class=\"status-on\">●</span>" if b["status"].lower() in ("running","online") else "<span class=\"status-off\">●</span>"} '
            f'{html.escape(b["name"])}：{html.escape(b["status"])}</li>'
            for b in s.get("boxes", []))
        simular_html = f"<ul>{rows}</ul>" or '<div class="muted">没有盒子</div>'
    # 快捷入口
    links_html = "".join(f'<a href="{html.escape(url)}" target="_blank">{html.escape(name)}</a></br>'.replace("</br>", "")
                         for name, url in QUICK_LINKS)
    # RSS
    blocks = []
    for feed in d.get("rss", []):
        if feed.get("ok"):
            items = "".join(
                f'<li><a href="{html.escape(i["link"])}" target="_blank">{html.escape(i["title"][:40])}</a></li>'
                for i in feed["items"])
            blocks.append(f'<div class="card"><h2>📰 {html.escape(feed["name"])}</h2><ul>{items}</ul></div>')
        else:
            blocks.append(f'<div class="card"><h2>📰 {html.escape(feed["name"])}</h2>'
                          f'<div class="err">抓取失败：{html.escape(feed.get("error",""))}</div></div>')
    return PAGE.format(updated=d.get("time", ""), w_city=w_city, weather_html=weather_html,
                       simular_html=simular_html, links_html=links_html, rss_html="".join(blocks))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = render().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("SERVER_PORT", "8000"))
    threading.Thread(target=refresher, daemon=True).start()
    refresh_once()  # 启动先抓一轮
    print(f"dashboard serving on 0.0.0.0:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
