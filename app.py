# -*- coding: utf-8 -*-
"""
Nodaroo 聚合站 v2
打开即公众主页：天气（默认深圳）+ 工具集 + 新闻 + 注册用户公开内容。
单文件、纯标准库，无需 pip 安装任何包。
部署：放到 /home/container/app.py，面板点 Restart 即可。

用户系统（数据存在盒子的 users.json / posts.json，不进仓库）：
- 邀请码注册，初始邀请码 nodaroo；注册上限 5 人
- 首位注册者自动成为管理员，可改邀请码和人数上限
- 首位用户可不填用户名（仅密码登录），此时注册关闭，此后账号只由管理员增删
- 登录后可在主页发布公开内容
"""
import os
import json
import time
import html
import hashlib
import hmac
import secrets
import threading
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ===================== 配置区 =====================

# 城市（天气用）：名称 + 经纬度，默认深圳
CITY = "深圳"
LAT, LON = 22.5431, 114.0579

# 新闻 RSS 源：想加就往里添，(名称, 地址)
RSS_FEEDS = [
    ("少数派", "https://sspai.com/feed"),
    ("阮一峰周刊", "https://www.ruanyifeng.com/blog/atom.xml"),
]

# 工具集/快捷入口：(名称, 链接)
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

# 用户系统默认值（可在 /admin 里改）
DEFAULT_INVITE_CODE = "nodaroo"
DEFAULT_MAX_USERS = 5

# ================================================

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# 个人配置：盒子目录下若有 config.json 则覆盖上面的默认值（此文件不进仓库）。
# 格式示例：
# {
#   "city": "深圳", "lat": 22.5431, "lon": 114.0579,
#   "rss_feeds": [["名称", "https://..."]],
#   "quick_links": [["名称", "https://..."]],
#   "refresh_interval": 600
# }
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
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

CACHE_FILE = os.path.join(APP_DIR, "dashboard_cache.json")
USERS_FILE = os.path.join(APP_DIR, "users.json")
POSTS_FILE = os.path.join(APP_DIR, "posts.json")
SECRET_FILE = os.path.join(APP_DIR, "session_secret.key")
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
    """Open-Meteo 当前天气 + 今日温度范围，免密钥。"""
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
           "&current=temperature_2m,apparent_temperature,relative_humidity_2m,weather_code"
           "&daily=temperature_2m_max,temperature_2m_min"
           "&forecast_days=1&timezone=Asia%2FShanghai")
    j = json.loads(http_get(url, timeout=15))
    cur = j["current"]
    daily = j.get("daily", {})
    tmax = daily.get("temperature_2m_max", [None])[0]
    tmin = daily.get("temperature_2m_min", [None])[0]
    return {
        "city": CITY,
        "temp": round(cur["temperature_2m"]),
        "feels": round(cur["apparent_temperature"]),
        "desc": WMO_CODES.get(cur["weather_code"], "—"),
        "humidity": cur["relative_humidity_2m"],
        "tmax": round(tmax) if tmax is not None else None,
        "tmin": round(tmin) if tmin is not None else None,
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


# ===================== 用户系统 =====================

def load_users():
    try:
        with open(USERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"invite_code": DEFAULT_INVITE_CODE, "max_users": DEFAULT_MAX_USERS,
                "registration_closed": False, "users": []}


def save_users(d):
    with _lock:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)


def load_posts():
    try:
        with open(POSTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_posts(posts):
    with _lock:
        with open(POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=1)


def get_secret():
    """会话签名密钥：首次生成并保存，之后复用。"""
    try:
        with open(SECRET_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        s = secrets.token_hex(32)
        with open(SECRET_FILE, "w", encoding="utf-8") as f:
            f.write(s)
    return s


def hash_pw(password, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
    return salt, h


def verify_pw(password, salt, expect):
    return hmac.compare_digest(hash_pw(password, salt)[1], expect)


def display_name(username):
    return username or "admin"


def make_session(username):
    expiry = int(time.time()) + 30 * 86400
    token = hmac.new(get_secret().encode(), f"{username}|{expiry}".encode(), hashlib.sha256).hexdigest()
    return f"{urllib.parse.quote(username)}|{expiry}|{token}"


def parse_session(cookie_value):
    try:
        username, expiry, token = cookie_value.split("|")
        username = urllib.parse.unquote(username)
        if int(expiry) < time.time():
            return None
        expect = hmac.new(get_secret().encode(), f"{username}|{expiry}".encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(token, expect):
            return username
    except Exception:
        pass
    return None


def current_user(headers):
    """返回 (用户dict 或 None, 用户配置dict)。"""
    d = load_users()
    for part in headers.get("Cookie", "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == "dash_session":
            username = parse_session(v)
            if username is not None:
                for u in d["users"]:
                    if u["username"] == username:
                        return u, d
    return None, d


# ===================== 页面模板 =====================

STYLE = """
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; padding:16px; background:#12121c; color:#e8e8f0;
         font-family:-apple-system,"PingFang SC",sans-serif; }
  a { color:#dcdcf0; }
  .top { display:flex; align-items:center; justify-content:space-between;
         flex-wrap:wrap; gap:8px; margin-bottom:16px; }
  .top h1 { font-size:22px; margin:0; }
  .nav { font-size:13px; }
  .nav a { margin-left:12px; color:#b8a8ff; text-decoration:none; }
  .nav .who { color:#8888a0; margin-left:12px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:14px; }
  .card { background:#1c1c2a; border-radius:14px; padding:14px 16px; }
  .card h2 { font-size:15px; margin:0 0 10px; color:#b8a8ff; }
  .card ul { list-style:none; margin:0; padding:0; }
  .card li { padding:5px 0; border-bottom:1px solid #2a2a3c; font-size:13px; }
  .card li:last-child { border-bottom:none; }
  .card a { color:#dcdcf0; text-decoration:none; }
  .card a:hover { color:#b8a8ff; }
  .muted { color:#66667e; font-size:12px; }
  .links a { display:inline-block; margin:4px 8px 4px 0; padding:6px 12px;
             background:#2a2a40; border-radius:8px; font-size:13px; color:#dcdcf0;
             text-decoration:none; }
  .status-on { color:#7fe8a0; } .status-off { color:#ff9a9a; }
  .weather-big { font-size:30px; font-weight:600; }
  .err { color:#ff9a9a; font-size:12px; }
  .ok { color:#7fe8a0; font-size:13px; }
  .posts { margin-top:14px; }
  .post { background:#1c1c2a; border-radius:14px; padding:12px 16px; margin-bottom:10px; }
  .post .meta { color:#8888a0; font-size:12px; margin-bottom:6px; }
  .post .meta a { color:#ff9a9a; margin-left:10px; }
  .post .text { font-size:14px; white-space:pre-wrap; word-break:break-word; }
  textarea, input { width:100%; padding:10px; border-radius:10px; border:1px solid #2a2a3c;
                    background:#12121c; color:#e8e8f0; font-size:14px; font-family:inherit; }
  button { padding:10px 18px; margin-top:8px; border:none; border-radius:10px;
           background:#7c5cff; color:#fff; font-size:14px; }
  .box { background:#1c1c2a; border-radius:16px; padding:28px; width:min(360px, 90vw);
         margin:8vh auto; }
  .box h1 { font-size:20px; margin:0 0 16px; text-align:center; }
  .box label { display:block; font-size:13px; color:#8888a0; margin:10px 0 4px; }
  .box .chk { font-size:13px; color:#dcdcf0; margin-top:10px; }
  .box .chk input { width:auto; margin-right:6px; }
  .box .err { min-height:18px; margin-top:10px; text-align:center; }
  .box button { width:100%; }
  .box .alt { text-align:center; font-size:13px; margin-top:12px; }
  .box .alt a { color:#b8a8ff; }
  .back { display:inline-block; margin-bottom:12px; color:#b8a8ff; font-size:13px;
          text-decoration:none; }
"""

BASE_PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>__STYLE__</style>
</head>
<body>
__BODY__
</body></html>"""


def page(title, body):
    return BASE_PAGE.replace("__TITLE__", html.escape(title)).replace("__STYLE__", STYLE).replace("__BODY__", body)


def nav_html(user):
    if user:
        links = f'<span class="who">你好，{html.escape(display_name(user["username"]))}</span>'
        links += '<a href="/">主页</a><a href="/panel">用户主页</a>'
        if user.get("is_admin"):
            links += '<a href="/admin">管理</a>'
        links += '<a href="/logout">退出</a>'
    else:
        links = '<a href="/login">登录</a>'
    return f'<div class="top"><h1>🌐 Nodaroo 广场</h1><div class="nav">{links}</div></div>'


def render_home(user, d, msg="", err=""):
    # 天气
    if d.get("weather"):
        w = d["weather"]
        extra = []
        if w.get("tmax") is not None and w.get("tmin") is not None:
            extra.append(f"今日 {w['tmin']}~{w['tmax']}°C")
        extra.append(f"体感 {w['feels']}°C")
        extra.append(f"湿度 {w['humidity']}%")
        weather_html = (f'<div class="weather-big">{html.escape(w["desc"])} {w["temp"]}°C</div>'
                        f'<div class="muted">{" · ".join(extra)}</div>')
        w_city = w["city"]
    else:
        weather_html, w_city = '<div class="muted">暂未获取到</div>', CITY

    # 工具集
    links_html = "".join(f'<a href="{html.escape(url)}" target="_blank">{html.escape(name)}</a>'
                         for name, url in QUICK_LINKS)

    # 新闻
    rss_blocks = []
    for feed in d.get("rss", []):
        if feed.get("ok"):
            items = "".join(
                f'<li><a href="{html.escape(i["link"])}" target="_blank">{html.escape(i["title"][:40])}</a></li>'
                for i in feed["items"])
            rss_blocks.append(f'<div class="card"><h2>📰 {html.escape(feed["name"])}</h2><ul>{items}</ul></div>')
        else:
            rss_blocks.append(f'<div class="card"><h2>📰 {html.escape(feed["name"])}</h2>'
                              f'<div class="err">抓取失败：{html.escape(feed.get("error", ""))}</div></div>')

    # 注册用户公开内容
    posts = sorted(load_posts(), key=lambda p: p.get("id", 0), reverse=True)
    notice = f'<div class="ok">{html.escape(msg)}</div>' if msg else ""
    notice += f'<div class="err">{html.escape(err)}</div>' if err else ""
    if user:
        composer = (f'<div class="card posts"><h2>✏️ 说点什么</h2>{notice}'
                    f'<form method="POST" action="/post">'
                    f'<textarea name="text" rows="3" maxlength="500" placeholder="公开内容，所有人可见"></textarea>'
                    f'<button type="submit">发布</button></form></div>')
    else:
        composer = (f'<div class="card posts"><h2>✏️ 说点什么</h2>{notice}'
                    f'<div class="muted"><a href="/login">登录</a> 后可以在这里发布公开内容。</div></div>')
    if posts:
        rows = []
        for p in posts:
            del_link = ""
            if user and p.get("author") == user["username"]:
                del_link = (f'<a href="#" onclick="var f=document.createElement(\'form\');'
                            f'f.method=\'POST\';f.action=\'/delete_post\';'
                            f'var i=document.createElement(\'input\');i.name=\'id\';i.value=\'{p["id"]}\';'
                            f'f.appendChild(i);document.body.appendChild(f);f.submit();return false;">删除</a>')
            rows.append(f'<div class="post"><div class="meta">{html.escape(display_name(p.get("author", "")))}'
                        f' · {html.escape(p.get("time", ""))}{del_link}</div>'
                        f'<div class="text">{html.escape(p.get("text", ""))}</div></div>')
        posts_html = "".join(rows)
    else:
        posts_html = '<div class="muted">还没有内容，来发第一条吧。</div>'

    body = (nav_html(user)
            + f'<div class="grid">'
            + f'<div class="card"><h2>🌤 天气 · {html.escape(w_city)}</h2>{weather_html}</div>'
            + f'<div class="card links"><h2>🧰 工具集</h2>{links_html}</div>'
            + "".join(rss_blocks)
            + f'</div>'
            + composer
            + f'<div class="posts"><h2 style="font-size:15px;color:#b8a8ff;margin:4px 0 10px;">💬 注册用户的公开内容</h2>{posts_html}</div>'
            + f'<div class="muted" style="margin-top:12px;">数据更新于 {html.escape(d.get("time", ""))}</div>')
    return page("Nodaroo 广场", body)


def form_page(title, inner, back=False):
    back_html = '<a class="back" href="/">← 回主页</a><br>' if back else ""
    body = f'<div class="box">{back_html}<h1>{html.escape(title)}</h1>{inner}</div>'
    return page(title, body)


def login_page(err=""):
    inner = (f'<form method="POST" action="/login">'
             f'<label>用户名（免用户名账号留空）</label><input name="username" autocomplete="username">'
             f'<label>密码</label><input type="password" name="password" autocomplete="current-password" autofocus>'
             f'<button type="submit">进入</button>'
             f'<div class="err">{html.escape(err)}</div></form>'
             f'<div class="alt">还没有账号？<a href="/register">注册</a> · 登录后进入<a href="/panel">用户主页</a></div>')
    return form_page("登录", inner)


def register_page(d, err=""):
    first = len(d["users"]) == 0
    closed = d.get("registration_closed") or len(d["users"]) >= d.get("max_users", DEFAULT_MAX_USERS)
    if closed:
        inner = (f'<div class="err">注册已关闭。</div>'
                 f'<div class="alt"><a href="/login">去登录</a></div>')
        return form_page("注册", inner, back=True)
    first_hint = '<div class="muted">你是第一位用户，注册后自动成为管理员。</div>' if first else ""
    nousername = ('<div class="chk"><input type="checkbox" name="nousername" value="1">'
                  '免用户名，仅密码登录（首位用户专用，勾选后注册将关闭）</div>') if first else ""
    inner = (f'<form method="POST" action="/register">{first_hint}'
             f'<label>邀请码</label><input name="invite" placeholder="邀请码" autofocus>'
             f'<label>用户名{"" if first else "（必填）"}</label><input name="username" autocomplete="username">'
             f'<label>密码</label><input type="password" name="password" autocomplete="new-password">'
             f'{nousername}'
             f'<button type="submit">注册</button>'
             f'<div class="err">{html.escape(err)}</div></form>'
             f'<div class="alt">已有账号？<a href="/login">去登录</a></div>')
    return form_page("注册", inner, back=True)


def render_panel(user, d):
    s = d.get("simular")
    if s is None:
        simular_html = '<div class="muted">未启用。在面板环境变量加 SIMULAR_KEY 即可显示盒子状态。</div>'
    elif s.get("error"):
        simular_html = f'<div class="err">{html.escape(s["error"])}</div>'
    else:
        rows = "".join(
            f'<li>{"<span class=\"status-on\">●</span>" if b["status"].lower() in ("running", "online") else "<span class=\"status-off\">●</span>"} '
            f'{html.escape(b["name"])}：{html.escape(b["status"])}</li>'
            for b in s.get("boxes", []))
        simular_html = f"<ul>{rows}</ul>" if rows else '<div class="muted">没有盒子</div>'
    links_html = "".join(f'<a href="{html.escape(url)}" target="_blank">{html.escape(name)}</a>'
                         for name, url in QUICK_LINKS)
    body = (nav_html(user)
            + f'<div class="grid">'
            + f'<div class="card"><h2>🖥 Simular 盒子</h2>{simular_html}</div>'
            + f'<div class="card links"><h2>🧰 工具集</h2>{links_html}</div>'
            + f'</div>'
            + f'<div class="muted" style="margin-top:12px;">数据更新于 {html.escape(d.get("time", ""))}</div>')
    return page("用户主页", body)


def render_admin(user, d, msg="", err=""):
    notice = f'<div class="ok">{html.escape(msg)}</div>' if msg else ""
    notice += f'<div class="err">{html.escape(err)}</div>' if err else ""
    user_rows = "".join(
        f'<li>{html.escape(display_name(u["username"]))}'
        f'{"（管理员）" if u.get("is_admin") else ""}</li>'
        for u in d["users"])
    remove_opts = "".join(
        f'<option value="{html.escape(u["username"])}">{html.escape(display_name(u["username"]))}</option>'
        for u in d["users"] if u["username"] != user["username"])
    body = (nav_html(user)
            + notice
            + f'<div class="grid">'
            + f'<div class="card"><h2>👥 当前用户（{len(d["users"])}/{d.get("max_users", DEFAULT_MAX_USERS)}）</h2>'
            + f'<ul>{user_rows}</ul>'
            + f'<div class="muted">注册状态：{"已关闭" if d.get("registration_closed") else "开放"}</div></div>'
            + f'<div class="card"><h2>🔑 邀请码与上限</h2>'
            + f'<form method="POST" action="/admin">'
            + f'<label>邀请码</label><input name="invite" value="{html.escape(d.get("invite_code", ""))}">'
            + f'<label>人数上限</label><input name="max" value="{d.get("max_users", DEFAULT_MAX_USERS)}">'
            + f'<button type="submit">保存</button><input type="hidden" name="action" value="settings"></form></div>'
            + f'<div class="card"><h2>➕ 代建账号</h2>'
            + f'<form method="POST" action="/admin">'
            + f'<label>用户名</label><input name="username">'
            + f'<label>密码</label><input type="password" name="password">'
            + f'<button type="submit">创建</button><input type="hidden" name="action" value="add"></form></div>'
            + f'<div class="card"><h2>➖ 移除用户</h2>'
            + (f'<form method="POST" action="/admin">'
               f'<label>选择用户</label><select name="username" style="width:100%;padding:10px;border-radius:10px;'
               f'border:1px solid #2a2a3c;background:#12121c;color:#e8e8f0;">{remove_opts}</select>'
               f'<button type="submit">移除</button><input type="hidden" name="action" value="remove"></form>'
               if remove_opts else '<div class="muted">没有可移除的用户</div>')
            + f'</div>'
            + f'</div>')
    return page("管理", body)


# ===================== HTTP 服务 =====================

class Handler(BaseHTTPRequestHandler):
    def _send(self, body, status=200, headers=None, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location, headers=None):
        self._send("", status=302, headers=list(headers or []) + [("Location", location)])

    def _form(self):
        length = int(self.headers.get("Content-Length", 0))
        form = self.rfile.read(length).decode("utf-8", "ignore")
        return urllib.parse.parse_qs(form)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        user, d = current_user(self.headers)

        if path == "/logout":
            self._redirect("/", headers=[("Set-Cookie", "dash_session=; Max-Age=0; Path=/")])
            return
        if path == "/":
            self._send(render_home(user, load_cache()))
            return
        if path == "/login":
            if user:
                self._redirect("/")
                return
            self._send(login_page())
            return
        if path == "/register":
            if user:
                self._redirect("/")
                return
            self._send(register_page(load_users()))
            return
        if path == "/panel":
            if not user:
                self._redirect("/login")
                return
            self._send(render_panel(user, load_cache()))
            return
        if path == "/admin":
            if not user or not user.get("is_admin"):
                self._redirect("/")
                return
            self._send(render_admin(user, load_users()))
            return
        self._send("Not found", status=404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        form = self._form()
        user, d = current_user(self.headers)

        if path == "/login":
            username = form.get("username", [""])[0].strip()
            password = form.get("password", [""])[0]
            data = load_users()
            for u in data["users"]:
                if u["username"] == username and verify_pw(password, u["salt"], u["hash"]):
                    cookie = make_session(u["username"])
                    self._redirect("/panel", headers=[("Set-Cookie",
                        f"dash_session={cookie}; Path=/; Max-Age=2592000; HttpOnly")])
                    return
            self._send(login_page("用户名或密码不对，再试试"))
            return

        if path == "/register":
            data = load_users()
            invite = form.get("invite", [""])[0].strip()
            username = form.get("username", [""])[0].strip()
            password = form.get("password", [""])[0]
            nousername = form.get("nousername", [""])[0] == "1"
            first = len(data["users"]) == 0
            closed = data.get("registration_closed") or len(data["users"]) >= data.get("max_users", DEFAULT_MAX_USERS)
            if closed:
                self._send(register_page(data, "注册已关闭"))
                return
            if invite != data.get("invite_code", DEFAULT_INVITE_CODE):
                self._send(register_page(data, "邀请码不对"))
                return
            if not password or len(password) < 4:
                self._send(register_page(data, "密码至少 4 位"))
                return
            if not first and not username:
                self._send(register_page(data, "请填写用户名"))
                return
            if username and (len(username) > 20 or any(c.isspace() for c in username)):
                self._send(register_page(data, "用户名不能超过 20 位且不能含空格"))
                return
            if username and any(u["username"] == username for u in data["users"]):
                self._send(register_page(data, "用户名已被占用"))
                return
            salt, h = hash_pw(password)
            account = {"username": username, "salt": salt, "hash": h,
                       "is_admin": first, "created": time.strftime("%Y-%m-%d %H:%M")}
            data["users"].append(account)
            if first and (nousername or not username):
                # 首位用户免用户名注册：此后注册关闭，账号只由管理员增删
                data["registration_closed"] = True
            save_users(data)
            cookie = make_session(account["username"])
            self._redirect("/panel", headers=[("Set-Cookie",
                f"dash_session={cookie}; Path=/; Max-Age=2592000; HttpOnly")])
            return

        if path == "/post":
            if not user:
                self._redirect("/login")
                return
            text = form.get("text", [""])[0].strip()
            if not text:
                self._redirect("/")
                return
            posts = load_posts()
            posts.append({"id": int(time.time() * 1000), "author": user["username"],
                          "text": text[:500], "time": time.strftime("%Y-%m-%d %H:%M")})
            save_posts(posts)
            self._redirect("/")
            return

        if path == "/delete_post":
            if not user:
                self._redirect("/login")
                return
            try:
                pid = int(form.get("id", ["0"])[0])
            except ValueError:
                pid = 0
            posts = [p for p in load_posts() if not (p.get("id") == pid and p.get("author") == user["username"])]
            save_posts(posts)
            self._redirect("/")
            return

        if path == "/admin":
            if not user or not user.get("is_admin"):
                self._redirect("/")
                return
            data = load_users()
            action = form.get("action", [""])[0]
            if action == "settings":
                invite = form.get("invite", [""])[0].strip()
                try:
                    max_users = max(1, int(form.get("max", [""])[0]))
                except ValueError:
                    max_users = data.get("max_users", DEFAULT_MAX_USERS)
                if invite:
                    data["invite_code"] = invite
                data["max_users"] = max_users
                save_users(data)
                self._send(render_admin(user, load_users(), msg="已保存"))
                return
            if action == "add":
                username = form.get("username", [""])[0].strip()
                password = form.get("password", [""])[0]
                if not username or not password or len(password) < 4:
                    self._send(render_admin(user, data, err="用户名和密码（至少 4 位）都要填"))
                    return
                if len(data["users"]) >= data.get("max_users", DEFAULT_MAX_USERS):
                    self._send(render_admin(user, data, err="人数已满，先调高上限"))
                    return
                if any(u["username"] == username for u in data["users"]):
                    self._send(render_admin(user, data, err="用户名已存在"))
                    return
                salt, h = hash_pw(password)
                data["users"].append({"username": username, "salt": salt, "hash": h,
                                      "is_admin": False, "created": time.strftime("%Y-%m-%d %H:%M")})
                save_users(data)
                self._send(render_admin(user, load_users(), msg=f"已创建 {username}"))
                return
            if action == "remove":
                username = form.get("username", [""])[0]
                if username and username != user["username"]:
                    data["users"] = [u for u in data["users"] if u["username"] != username]
                    save_users(data)
                self._send(render_admin(user, load_users(), msg="已移除" if username else ""))
                return
            self._redirect("/admin")
            return

        self._send("Not found", status=404)

    def log_message(self, *a):
        pass


def bootstrap_git():
    """启动时自动接线并同步仓库：没有 .git 就初始化；已接线就同步到远端最新。
    有未提交的本地改动时跳过重置，避免覆盖盒子上的修改。"""
    repo_dir = APP_DIR
    import subprocess

    def run(*args):
        return subprocess.run(["git", *args], cwd=repo_dir, capture_output=True, text=True)

    remote = os.environ.get("GIT_REPO", "https://github.com/xc7s/nodraoo-dashboard.git")
    try:
        if not os.path.exists(os.path.join(repo_dir, ".git")):
            run("init", "-b", "main")
            run("remote", "add", "origin", remote)
        if run("rev-parse", "--verify", "origin/main").returncode != 0:
            run("fetch", "origin")
        if run("rev-parse", "--verify", "origin/main").returncode == 0:
            dirty = run("status", "--porcelain")
            if dirty.returncode == 0 and dirty.stdout.strip():
                print("git sync skipped: local changes present")
                return
            run("reset", "--hard", "origin/main")
            run("branch", "--set-upstream-to=origin/main", "main")
            print("git synced to origin/main")
    except Exception as e:
        print("git bootstrap skipped:", e)


if __name__ == "__main__":
    bootstrap_git()
    port = int(os.environ.get("SERVER_PORT", "8000"))
    threading.Thread(target=refresher, daemon=True).start()
    refresh_once()  # 启动先抓一轮
    print(f"dashboard serving on 0.0.0.0:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
