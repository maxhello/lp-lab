"""Tool navigation pages shared across the lp-lab app."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

TOOLS = [
    {
        "slug": "scheduling",
        "emoji": "🗓️",
        "title": "排班求解器",
        "subtitle": "Shift Scheduler",
        "desc": "CP-SAT 员工排班:覆盖需求、劳动法规、公平性、偏好,毫秒级出解。",
        "href": "/tools/scheduling",
    },
    # Future tools (VRP, bin packing, LP playground...) append here.
]

_HOME_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>⚡ LP Lab — OR-Tools 优化工具集</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         background: #0b0f1a; color: #e5ecf5; margin: 0; min-height: 100vh;
         background-image: radial-gradient(1100px 550px at 70% -10%, #1e293b66, transparent),
                           radial-gradient(900px 500px at 10% 110%, #1d4ed826, transparent);
         background-attachment: fixed;
         display: flex; flex-direction: column; align-items: center;
         justify-content: center; padding: 40px 20px;
         -webkit-font-smoothing: antialiased; }
  h1 { font-size: 30px; margin: 0 0 8px; letter-spacing: .5px; }
  .tagline { color: #8b98ab; font-size: 14px; margin-bottom: 40px; letter-spacing: .5px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 380px));
          gap: 18px; justify-content: center; }
  a.card { display: block; background: #111827; border: 1px solid #1f2a3d; border-radius: 14px;
           padding: 24px 26px; text-decoration: none; color: inherit;
           box-shadow: 0 8px 24px #00000040;
           transition: box-shadow .18s, transform .18s, border-color .18s; }
  a.card:hover { border-color: #3b82f6; transform: translateY(-3px);
                 box-shadow: 0 10px 34px #2563eb33, 0 8px 24px #00000050; }
  .emoji { font-size: 32px; filter: drop-shadow(0 2px 6px #00000080); }
  .title { font-size: 17px; font-weight: 600; margin: 12px 0 2px; }
  .title small { color: #55637a; font-weight: normal; font-size: 12px; margin-left: 8px; }
  .desc { color: #8b98ab; font-size: 13px; line-height: 1.7; margin: 10px 0 0; }
  .arrow { color: #3b82f6; font-size: 13px; margin-top: 14px; opacity: 0;
           transition: opacity .18s; }
  a.card:hover .arrow { opacity: 1; }
  footer { margin-top: 44px; color: #55637a; font-size: 12px; }
  footer a { color: #3b82f6; text-decoration: none; }
  footer a:hover { text-decoration: underline; }
</style>
</head>
<body>
  <h1>⚡ LP Lab</h1>
  <p class="tagline">Google OR-Tools 优化工具集 — 定义问题,秒级求解</p>
  <div class="grid">
    __CARDS__
  </div>
  <footer>Powered by <a href="/docs">FastAPI</a> · OR-Tools CP-SAT</footer>
</body>
</html>
"""

_CARD = """<a class="card" href="{href}">
  <div class="emoji">{emoji}</div>
  <div class="title">{title}<small>{subtitle}</small></div>
  <p class="desc">{desc}</p>
  <div class="arrow">打开工具 →</div>
</a>"""


def _render_home() -> str:
    cards = "\n".join(_CARD.format(**t) for t in TOOLS)
    return _HOME_HTML.replace("__CARDS__", cards)


def register_home(app: FastAPI) -> None:
    @app.get("/", include_in_schema=False)
    def home() -> HTMLResponse:
        return HTMLResponse(_render_home())
