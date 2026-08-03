# -*- coding: utf-8 -*-
"""Render mt-note-promoter README diagrams to PNG (no Mermaid dependency).

Each diagram is described as a list of nodes + edges, rendered to a standalone
SVG, then screenshotted via headless Chromium so the output PNG displays in
any Markdown viewer.
"""
import os
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "images")
os.makedirs(OUT, exist_ok=True)

FONT = '"Microsoft YaHei","微软雅黑","PingFang SC","SimHei","Noto Sans CJK SC",sans-serif'

# ---- palette ----
C_BLUE   = ("#e3f2fd", "#1e88e5")
C_GREEN  = ("#e8f5e9", "#43a047")
C_ORANGE = ("#fff3e0", "#fb8c00")
C_PURPLE = ("#f3e5f5", "#8e24aa")
C_DPV    = ("#ede7f6", "#5e35b1")
C_RED    = ("#ffebee", "#e53935")
C_GOLD   = ("#fff8e1", "#f9a825")
C_CYAN   = ("#e0f7fa", "#00acc1")
INK      = "#1f2933"

# ----------------------------- helpers --------------------------------------

def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def _text(x, y, s, size=13, weight="normal", fill=INK, anchor="middle",
          family=FONT):
    return (f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}" font-family={family}>'
            f'{esc(s)}</text>')

def _multiline(cx, top, lines, size=13, weight="normal", fill=INK,
               lh=None, anchor="middle"):
    if lh is None:
        lh = size + 5
    parts = []
    for i, ln in enumerate(lines):
        parts.append(_text(cx, top + i * lh, ln, size, weight, fill, anchor))
    return "".join(parts)

def rect_box(n):
    x, y, w, h = n["x"], n["y"], n["w"], n["h"]
    fill, stroke = n["pal"]
    rx = n.get("rx", 10)
    s = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
         f'fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
    cx = x + w / 2
    title = n.get("title", "")
    sub = n.get("sub", [])
    if sub:
        s += _multiline(cx, y + 22, [title], size=14, weight="bold")
        s += _multiline(cx, y + 22 + 22, sub, size=12, fill="#37474f")
    else:
        s += _text(cx, y + h / 2 + 5, title, size=14, weight="bold")
    return s

def band_box(n):
    x, y, w, h = n["x"], n["y"], n["w"], n["h"]
    fill, stroke = n["pal"]
    bar = 8
    s = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" '
         f'fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
    s += (f'<rect x="{x}" y="{y}" width="{bar}" height="{h}" rx="6" '
          f'fill="{stroke}"/>')
    cx = x + bar + (w - bar) / 2
    title = n.get("title", "")
    sub = n.get("sub", [])
    s += _text(cx, y + 24, title, size=15, weight="bold", fill=stroke)
    s += _multiline(cx, y + 44, sub, size=12, fill="#37474f")
    return s

def diamond_box(n):
    x, y, w, h = n["x"], n["y"], n["w"], n["h"]
    fill, stroke = n["pal"]
    cx, cy = x + w / 2, y + h / 2
    pts = f"{cx},{y} {x+w},{cy} {cx},{y+h} {x},{cy}"
    s = (f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" '
         f'stroke-width="2"/>')
    lines = n.get("sub", [n.get("title", "")])
    s += _multiline(cx, cy - (len(lines) - 1) * 9, lines, size=12, weight="bold")
    return s

def db_box(n):
    x, y, w, h = n["x"], n["y"], n["w"], n["h"]
    fill, stroke = n["pal"]
    head_h = 30
    s = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" '
         f'fill="#ffffff" stroke="{stroke}" stroke-width="2"/>')
    s += (f'<path d="M{x},{y+head_h} h{w}" stroke="{stroke}" '
          f'stroke-width="1.5"/>')
    s += (f'<rect x="{x}" y="{y}" width="{w}" height="{head_h}" rx="6" '
          f'fill="{stroke}"/>')
    s += (f'<rect x="{x}" y="{y+head_h-6}" width="{w}" height="6" '
          f'fill="{stroke}"/>')
    s += _text(x + w / 2, y + 20, n["title"], size=13, weight="bold",
               fill="#fff")
    rows = n.get("rows", [])
    s += _multiline(x + 12, y + head_h + 20, rows, size=11.5, anchor="start",
                    fill="#37474f", lh=18)
    return s

def anchor_point(n, side):
    x, y, w, h = n["x"], n["y"], n["w"], n["h"]
    if side == "t":
        return (x + w / 2, y)
    if side == "b":
        return (x + w / 2, y + h)
    if side == "l":
        return (x, y + h / 2)
    if side == "r":
        return (x + w, y + h / 2)
    return (x + w / 2, y + h / 2)

def edge(e, nodes):
    a, b = nodes[e["from"]], nodes[e["to"]]
    fa = e.get("fa", "b")  # from anchor
    ta = e.get("ta", "t")  # to anchor (opposite)
    x1, y1 = anchor_point(a, fa)
    x2, y2 = anchor_point(b, ta)
    # slight elbow for side-to-side
    d = f"M{x1},{y1} L{x2},{y2}"
    lbl = e.get("label")
    s = (f'<path d="{d}" fill="none" stroke="#607d8b" stroke-width="1.8" '
         f'marker-end="url(#ah)"/>')
    if lbl:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        s += (f'<rect x="{mx-46}" y="{my-11}" width="92" height="18" rx="4" '
              f'fill="#ffffff" stroke="#cfd8dc"/>')
        s += _text(mx, my + 4, lbl, size=11, fill="#455a64")
    return s

def render_svg(w, h, nodes, edges, extra=""):
    body = ""
    for n in nodes:
        sh = n.get("shape", "box")
        if sh == "band":
            body += band_box(n)
        elif sh == "diamond":
            body += diamond_box(n)
        elif sh == "db":
            body += db_box(n)
        else:
            body += rect_box(n)
    for e in edges:
        body += edge(e, {n["id"]: n for n in nodes})
    body += extra
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" font-family={FONT}>'
            f'<defs><marker id="ah" markerWidth="10" markerHeight="10" '
            f'refX="8" refY="3" orient="auto" markerUnits="strokeWidth">'
            f'<path d="M0,0 L9,3 L0,6 Z" fill="#607d8b"/></marker></defs>'
            f'<rect width="{w}" height="{h}" fill="#ffffff"/>{body}</svg>')

def save_png(name, html):
    path = os.path.join(OUT, name)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1200, "height": 1000},
                        device_scale_factor=2)
        pg.set_content(html)
        pg.screenshot(path=path, full_page=True)
        b.close()
    print("wrote", path)

def to_html(svg):
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<style>html,body{{margin:0;background:#fff;}}</style></head>'
            f'<body>{svg}</body></html>')

# ----------------------------- diagrams --------------------------------------

def d_arch():
    nodes = [
        {"id": "cli", "shape": "band", "x": 280, "y": 10, "w": 440, "h": 64,
         "pal": C_BLUE, "title": "CLI 入口层", "sub": ["main.py  (explore / import-history / dry-run / run / db / refresh-stores)"]},
        {"id": "orch", "shape": "band", "x": 280, "y": 100, "w": 440, "h": 64,
         "pal": C_GREEN, "title": "编排层", "sub": ["flow.py  (门店循环 · 轮次循环 · 重试 · 熔断 · 弹窗清理)"]},
        {"id": "dom", "shape": "band", "x": 280, "y": 190, "w": 440, "h": 78,
         "pal": C_ORANGE, "title": "领域层（业务逻辑）",
         "sub": ["notes.py  查重库 · 门店完成态 · 选择引擎", "budget_db.py  共享预算镜像 · 历史去重 · 爬取", "history_import.py  历史推广初始化"]},
        {"id": "core", "shape": "band", "x": 280, "y": 290, "w": 440, "h": 78,
         "pal": C_PURPLE, "title": "浏览器核心层",
         "sub": ["browser.py  CDP/launch · 操作原语 · 跨 iframe 定位", "retry.py  指数退避   logging_setup.py  日志"]},
        {"id": "cfg", "shape": "band", "x": 20, "y": 100, "w": 230, "h": 150,
         "pal": C_DPV, "title": "配置层",
         "sub": ["settings.json  投放/安全/浏览器", "selectors.json  页面选择器", "stores.json  37 家门店"]},
        {"id": "data", "shape": "band", "x": 760, "y": 200, "w": 260, "h": 170,
         "pal": C_RED, "title": "数据 / 环境层",
         "sub": ["promoted.db  (SQLite)", "edge_debug_profile  登录态", "logs/<时间戳>/  每步截图"]},
    ]
    edges = [
        {"from": "cli", "to": "orch"},
        {"from": "orch", "to": "dom"},
        {"from": "dom", "to": "core"},
        {"from": "cfg", "to": "orch", "fa": "r", "ta": "l", "label": "读取"},
        {"from": "dom", "to": "data", "fa": "r", "ta": "l", "label": "读写"},
        {"from": "core", "to": "data", "fa": "r", "ta": "l", "label": "连接/写入"},
    ]
    svg = render_svg(1040, 480, nodes, edges)
    save_png("arch-layers.png", to_html(svg))

def d_iframe():
    # sequence diagram
    W, H = 880, 430
    parts = [("Caller", 70), ("_locate()", 270), ("frame A\n(menu)", 470),
             ("frame B\n(cpm-edit)", 640), ("frame C\n(budget-list)", 810)]
    top = 60
    body = ""
    for name, x in parts:
        body += _text(x, top - 18, name.replace("\n", " "), size=13,
                      weight="bold", fill="#37474f")
        body += (f'<line x1="{x}" y1="{top}" x2="{x}" y2="{H-40}" '
                 f'stroke="#b0bec5" stroke-width="1.5" '
                 f'stroke-dasharray="4 3"/>')
    # messages
    def msg(x1, x2, y, txt, dashed=False, self_loop=False):
        s = (f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="#607d8b" '
             f'stroke-width="1.6" marker-end="url(#ah)"'
             + (' stroke-dasharray="5 3"' if dashed else "") + '/>')
        mx = (x1 + x2) / 2
        s += (f'<rect x="{mx-78}" y="{y-13}" width="156" height="18" rx="4" '
              f'fill="#fff" stroke="#cfd8dc"/>')
        s += _text(mx, y + 2, txt, size=11, fill="#455a64")
        return s
    y = 90
    body += msg(70, 270, y, "click(selector, timeout=5000)")
    y += 30
    body += msg(270, 70, y, "deadline = now + 5s", dashed=True)
    y += 34
    # loop box
    body += (f'<rect x="250" y="{y-14}" width="600" height="190" rx="8" '
             f'fill="none" stroke="#90a4ae" stroke-dasharray="6 4"/>')
    body += _text(560, y + 2, "每 120ms 一轮轮询所有 frame", size=11,
                  fill="#607d8b")
    y += 30
    body += msg(270, 470, y, "count()>0 且 is_visible()?", dashed=True)
    y += 28
    body += msg(270, 640, y, "count()>0 且 is_visible()?", dashed=True)
    y += 28
    body += msg(270, 810, y, "count()>0 且 is_visible()?", dashed=True)
    y += 26
    body += msg(810, 270, y, "是 → 命中", dashed=True)
    y += 28
    body += msg(270, 70, y, "写 frame 亲和缓存, 返回 (loc,frame)", dashed=True)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}" font-family={FONT}>'
           f'<defs><marker id="ah" markerWidth="10" markerHeight="10" refX="8" '
           f'refY="3" orient="auto" markerUnits="strokeWidth">'
           f'<path d="M0,0 L9,3 L0,6 Z" fill="#607d8b"/></marker></defs>'
           f'<rect width="{W}" height="{H}" fill="#fff"/>{body}</svg>')
    save_png("iframe-sequence.png", to_html(svg))

def d_single_store():
    nodes = [
        {"id": "A", "x": 300, "y": 0, "w": 280, "h": 46, "pal": C_BLUE,
         "title": "启动 Edge(CDP) · 确保登录态"},
        {"id": "B", "x": 300, "y": 60, "w": 280, "h": 46, "pal": C_BLUE,
         "title": "直达 URL 跳到共享预算页"},
        {"id": "C", "x": 300, "y": 120, "w": 280, "h": 46, "pal": C_BLUE,
         "title": "增量爬取预算列表 → budget_db"},
        {"id": "D", "x": 300, "y": 180, "w": 280, "h": 46, "pal": C_GREEN,
         "title": "定位门店预算行 → 新增推广 → 去新建推广"},
        {"id": "E", "x": 300, "y": 240, "w": 280, "h": 46, "pal": C_GREEN,
         "title": "选推广目的 = 内容种草"},
        {"id": "F", "x": 300, "y": 300, "w": 280, "h": 46, "pal": C_GREEN,
         "title": "选门店(叉旧标签→搜索→面包屑提交)"},
        {"id": "G", "x": 300, "y": 360, "w": 280, "h": 46, "pal": C_GREEN,
         "title": "笔记卡片渲染 → 等 N-premium-note-wrapper"},
        {"id": "H", "x": 300, "y": 420, "w": 280, "h": 46, "pal": C_GREEN,
         "title": "点笔记「修改」→ 打开选择抽屉"},
        {"id": "I", "x": 300, "y": 480, "w": 280, "h": 46, "pal": C_ORANGE,
         "title": "抓取候选笔记(标题+浏览量+data-id)"},
        {"id": "J", "shape": "diamond", "x": 330, "y": 540, "w": 220, "h": 70,
         "pal": C_GOLD, "sub": ["去重过滤", "(查重库+历史推广)"]},
        {"id": "K", "x": 300, "y": 630, "w": 280, "h": 46, "pal": C_ORANGE,
         "title": "按浏览量降序取 top1"},
        {"id": "L", "shape": "diamond", "x": 330, "y": 690, "w": 220, "h": 70,
         "pal": C_GOLD, "sub": ["浏览量 ≥ 400?"]},
        {"id": "X1", "x": 620, "y": 700, "w": 240, "h": 50, "pal": C_RED,
         "title": "标 store_done(views_below_min)"},
        {"id": "M", "x": 300, "y": 780, "w": 280, "h": 46, "pal": C_PURPLE,
         "title": "确认修改→关弹窗→等抽屉关闭"},
        {"id": "N", "x": 300, "y": 840, "w": 280, "h": 46, "pal": C_PURPLE,
         "title": "设推广地域: 门店附近 6km(抽屉)"},
        {"id": "O", "x": 300, "y": 900, "w": 280, "h": 46, "pal": C_PURPLE,
         "title": "设出价: 单次点击 1.1"},
        {"id": "P", "x": 300, "y": 960, "w": 280, "h": 46, "pal": C_PURPLE,
         "title": "下一步 → 创意页"},
        {"id": "Q", "shape": "diamond", "x": 330, "y": 1020, "w": 220, "h": 70,
         "pal": C_GOLD, "sub": ["提交模式?"]},
        {"id": "R", "x": 140, "y": 1110, "w": 200, "h": 46, "pal": C_DPV,
         "title": "dry-run: 存草稿"},
        {"id": "S", "x": 460, "y": 1110, "w": 200, "h": 46, "pal": C_GREEN,
         "title": "run: 保存并提交→写库"},
        {"id": "T", "x": 300, "y": 1170, "w": 280, "h": 46, "pal": C_BLUE,
         "title": "返回结果 · 继续同店下一轮"},
    ]
    edges = [
        {"from": "A", "to": "B"}, {"from": "B", "to": "C"}, {"from": "C", "to": "D"},
        {"from": "D", "to": "E"}, {"from": "E", "to": "F"}, {"from": "F", "to": "G"},
        {"from": "G", "to": "H"}, {"from": "H", "to": "I"}, {"from": "I", "to": "J"},
        {"from": "J", "to": "K"}, {"from": "K", "to": "L"},
        {"from": "L", "to": "M"},
        {"from": "L", "to": "X1", "fa": "r", "ta": "l", "label": "否"},
        {"from": "M", "to": "N"}, {"from": "N", "to": "O"}, {"from": "O", "to": "P"},
        {"from": "P", "to": "Q"}, {"from": "Q", "to": "R", "fa": "l", "ta": "t", "label": "dry-run"},
        {"from": "Q", "to": "S", "fa": "r", "ta": "t", "label": "run"},
        {"from": "R", "to": "T"}, {"from": "S", "to": "T"},
    ]
    svg = render_svg(880, 1240, nodes, edges)
    save_png("single-store-flow.png", to_html(svg))

def d_store_loop():
    nodes = [
        {"id": "START", "x": 330, "y": 0, "w": 240, "h": 44, "pal": C_BLUE,
         "title": "run() 加载配置+校验"},
        {"id": "LOGIN", "x": 330, "y": 60, "w": 240, "h": 44, "pal": C_BLUE,
         "title": "ensure_login"},
        {"id": "LOOP", "shape": "diamond", "x": 350, "y": 120, "w": 200, "h": 64,
         "pal": C_GOLD, "sub": ["遍历门店"]},
        {"id": "SKIP1", "shape": "diamond", "x": 620, "y": 120, "w": 200, "h": 64,
         "pal": C_GOLD, "sub": ["已 store_done?"]},
        {"id": "SKIP2", "shape": "diamond", "x": 620, "y": 210, "w": 200, "h": 64,
         "pal": C_GOLD, "sub": ["有 budget_keyword?"]},
        {"id": "ROUND", "x": 330, "y": 210, "w": 240, "h": 44, "pal": C_GREEN,
         "title": "单门店轮次循环"},
        {"id": "PROMO", "x": 330, "y": 270, "w": 240, "h": 44, "pal": C_GREEN,
         "title": "_promote_one_store"},
        {"id": "RST", "shape": "diamond", "x": 350, "y": 330, "w": 200, "h": 64,
         "pal": C_GOLD, "sub": ["瞬态失败?"]},
        {"id": "CHECK", "shape": "diamond", "x": 350, "y": 420, "w": 200, "h": 64,
         "pal": C_GOLD, "sub": ["有可用笔记?"]},
        {"id": "LIMIT", "shape": "diamond", "x": 350, "y": 510, "w": 200, "h": 64,
         "pal": C_GOLD, "sub": ["达上限?"]},
        {"id": "DONE", "x": 330, "y": 600, "w": 240, "h": 44, "pal": C_RED,
         "title": "标 store_done · 停止该店"},
        {"id": "END", "x": 330, "y": 670, "w": 240, "h": 44, "pal": C_BLUE,
         "title": "汇总结果"},
    ]
    edges = [
        {"from": "START", "to": "LOGIN"}, {"from": "LOGIN", "to": "LOOP"},
        {"from": "LOOP", "to": "SKIP1"},
        {"from": "SKIP1", "to": "SKIP2", "fa": "r", "ta": "l", "label": "否"},
        {"from": "SKIP2", "to": "ROUND", "fa": "l", "ta": "t", "label": "是"},
        {"from": "ROUND", "to": "PROMO"}, {"from": "PROMO", "to": "RST"},
        {"from": "RST", "to": "ROUND", "fa": "r", "ta": "r", "label": "是 且 round<2"},
        {"from": "RST", "to": "CHECK", "label": "否"},
        {"from": "CHECK", "to": "DONE", "fa": "r", "ta": "l", "label": "无"},
        {"from": "CHECK", "to": "LIMIT", "label": "有"},
        {"from": "LIMIT", "to": "DONE", "fa": "r", "ta": "l", "label": "是"},
        {"from": "LIMIT", "to": "ROUND", "label": "否"},
        {"from": "DONE", "to": "LOOP", "fa": "l", "ta": "l"},
        {"from": "LOOP", "to": "END", "fa": "b", "ta": "t"},
    ]
    svg = render_svg(880, 740, nodes, edges)
    save_png("store-round-loop.png", to_html(svg))

def d_er():
    nodes = [
        {"id": "pn", "shape": "db", "x": 20, "y": 20, "w": 250, "h": 150,
         "pal": C_RED, "title": "promoted_notes",
         "rows": ["id  PK", "note_id", "title  NOT NULL", "store  NOT NULL",
                  "view_count", "promoted_at  NOT NULL", "source  auto/history"]},
        {"id": "sd", "shape": "db", "x": 300, "y": 20, "w": 250, "h": 100,
         "pal": C_RED, "title": "store_done",
         "rows": ["store  PK", "reason", "done_at"]},
        {"id": "sb", "shape": "db", "x": 580, "y": 20, "w": 270, "h": 130,
         "pal": C_CYAN, "title": "shared_budgets",
         "rows": ["id  PK", "name  UNIQUE", "store_keyword",
                  "first_seen_at", "last_seen_at"]},
        {"id": "pr", "shape": "db", "x": 600, "y": 190, "w": 270, "h": 150,
         "pal": C_CYAN, "title": "promotions",
         "rows": ["id  PK", "budget_id  FK", "name", "content", "status",
                  "first_seen_at", "last_seen_at"]},
        {"id": "ss", "shape": "db", "x": 600, "y": 380, "w": 270, "h": 160,
         "pal": C_CYAN, "title": "scrape_sessions",
         "rows": ["id  PK", "scraped_at", "new_budgets",
                  "new_promotions", "total_budgets", "total_promotions"]},
    ]
    edges = [
        {"from": "sb", "to": "pr", "fa": "b", "ta": "t", "label": "1 : N"},
        {"from": "sb", "to": "ss", "fa": "b", "ta": "t", "label": "snapshot"},
    ]
    svg = render_svg(900, 560, nodes, edges)
    save_png("er-diagram.png", to_html(svg))

def d_pie():
    import math
    W, H = 560, 360
    cx, cy, r = 200, 180, 130
    # 54% nav, 46% business
    a0 = -90
    a1 = a0 + 54 * 3.6
    def pt(ang):
        rad = math.radians(ang)
        return (cx + r * math.cos(rad), cy + r * math.sin(rad))
    x0, y0 = pt(a0)
    x1, y1 = pt(a1)
    large = 1 if (a1 - a0) > 180 else 0
    nav = (f'<path d="M{cx},{cy} L{x0},{y0} A{r},{r} 0 {large} 1 {x1},{y1} Z" '
           f'fill="#1e88e5" stroke="#fff" stroke-width="2"/>')
    # business slice
    x2, y2 = pt(a0 + 360)
    large2 = 1 if (360 - (a1 - a0)) > 180 else 0
    biz = (f'<path d="M{cx},{cy} L{x1},{y1} A{r},{r} 0 {large2} 1 {x2},{y2} Z" '
           f'fill="#fb8c00" stroke="#fff" stroke-width="2"/>')
    # legend
    leg = ""
    items = [("#1e88e5", "导航段(菜单树点击)  54%"),
             ("#fb8c00", "业务段(选笔记→地域→出价→提交)  46%")]
    ly = 120
    for col, txt in items:
        leg += (f'<rect x="400" y="{ly-14}" width="20" height="20" rx="3" '
                f'fill="{col}"/>')
        leg += _text(432, ly + 1, txt, size=14, anchor="start", fill="#37474f")
        ly += 44
    leg += _text(200, 340, "单轮耗时占比(旧基线 ≈115s)", size=13, fill="#607d8b")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}" font-family={FONT}>'
           f'<rect width="{W}" height="{H}" fill="#fff"/>{nav}{biz}{leg}</svg>')
    save_png("perf-pie.png", to_html(svg))

def d_roadmap():
    nodes = [
        {"id": "A", "x": 20, "y": 40, "w": 180, "h": 70, "pal": C_BLUE,
         "title": "批次A: 纯性能", "sub": ["locate / 直达 / slow_mo"]},
        {"id": "B", "x": 250, "y": 40, "w": 180, "h": 70, "pal": C_GREEN,
         "title": "批次B: 等待+稳定性", "sub": ["sleep→条件等待/熔断/截图降级"]},
        {"id": "C", "x": 480, "y": 40, "w": 180, "h": 70, "pal": C_ORANGE,
         "title": "批次C: 结构重构", "sub": ["flow.py 拆 pages/ 层 <200行"]},
        {"id": "D", "x": 710, "y": 40, "w": 180, "h": 70, "pal": C_PURPLE,
         "title": "补全 TODO_EXPLORE", "sub": ["提交成功校验/创意页/历史"]},
        {"id": "E", "x": 940, "y": 40, "w": 180, "h": 70, "pal": C_DPV,
         "title": "批量调度", "sub": ["分店分批/定时/进度看板"]},
    ]
    edges = [
        {"from": "A", "to": "B"}, {"from": "B", "to": "C"},
        {"from": "C", "to": "D"}, {"from": "D", "to": "E"},
    ]
    svg = render_svg(1140, 140, nodes, edges)
    save_png("roadmap.png", to_html(svg))


if __name__ == "__main__":
    d_arch()
    d_iframe()
    d_single_store()
    d_store_loop()
    d_er()
    d_pie()
    d_roadmap()
    print("ALL DONE")
