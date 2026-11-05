# -*- coding: utf-8 -*-
"""Generate the K4 MCP artboards as standalone HTML for Pixso code_to_design.

One template, two themes. Every literal value is the resolved token at
--scale:1 from agent_framework/companion/web/assets/tokens.css. Unused CSS
rules are pruned per board so each payload stays small.
"""
import os
import re

OUT = os.path.dirname(os.path.abspath(__file__))

THEMES = {
    "light": dict(
        page="#ececee", workspace="#ffffff", surface="#ffffff",
        selected="#fde0ed", border="#ededf0", strong="#d2d2d7", text="#1d1d1f",
        muted="#8e8e93", dim="#6e6e73", accent="#ff5b8a", soft="#ff9fbd",
        on_accent="#ffffff", danger="#d94f61", success="#43a877",
        warning="#c58a31", code_bg="#f5f5f7",
    ),
    "dark": dict(
        page="#141414", workspace="#1b1b1b", surface="#252525",
        selected="#3a232c", border="#363636", strong="#4a4a4a", text="#ebebeb",
        muted="#a3a3a3", dim="#707070", accent="#ff5b8a", soft="#ff9bb8",
        on_accent="#24151b", danger="#ff6b78", success="#56c58b",
        warning="#e6b45c", code_bg="#1b1b1b",
    ),
}

NAV = [
    ("01", "基础信息", "person"),
    ("02", "模型推理", "square"),
    ("03", "RAG 知识库", "lines"),
    ("04", "MCP 工具", "petal"),
    ("05", "记忆与交互", "bubble"),
    ("06", "高级", "sliders"),
]

BASE = """
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:{W}px;height:{H}px;background:{page};font-family:"Microsoft YaHei UI","Segoe UI",sans-serif;color:{text};font-size:13px;line-height:1.5}}
.win{{width:{win_w}px;height:{win_h}px;margin:8px;border:1px solid {border};border-radius:14px;background:{workspace};display:flex;flex-direction:column;overflow:hidden}}
.wrap{{padding:14px 18px;display:flex;flex-direction:column;gap:10px}}
.head{{height:48px;flex:none;display:flex;align-items:center;padding:0 18px;border-bottom:1px solid {border}}}
.ttl{{font-size:18px;font-weight:600}}
.ver{{margin-left:8px;font-size:11px;color:{dim}}}
.wb{{display:flex;gap:8px}}
.wi{{width:22px;height:22px;border:1px solid {border};border-radius:6px;background:{surface}}}
.body{{flex:1;min-height:0;display:flex;padding:14px 18px 8px;gap:12px}}
.nav{{width:150px;flex:none;display:flex;flex-direction:column}}
.search{{height:32px;flex:none;border:1px solid {border};border-radius:6px;background:{surface};display:flex;align-items:center;padding:0 8px}}
.sq0{{width:9px;height:9px;flex:none;border:1px solid {dim};border-radius:99px}}
.sht{{margin-left:6px;font-size:13px;color:{dim}}}
.ni{{height:36px;flex:none;margin-top:6px;display:flex;align-items:center;padding:0 10px;border:1px solid transparent;border-radius:8px}}
.nion{{border-color:{accent};background:{selected}}}
.nm{{flex:1;min-width:0;margin-left:8px;font-size:14px;font-weight:600;white-space:nowrap}}
.no{{flex:none;font-size:11px;color:{dim}}}
.ic{{width:16px;height:16px;flex:none;position:relative;display:block}}
.hd{{display:block;width:7px;height:7px;border-radius:99px;position:absolute;left:5px;top:0}}
.bd{{display:block;width:13px;height:7px;border-radius:7px 7px 0 0;position:absolute;left:2px;top:8px}}
.sq{{display:block;width:13px;height:13px;border:1px solid currentColor;border-radius:4px;position:absolute;left:2px;top:2px}}
.sqb{{display:block;width:4px;height:4px;border-radius:99px;position:absolute;left:4px;top:4px}}
.ln{{display:block;height:2px;border-radius:2px;width:14px;margin-top:3px}}
.pet{{display:block;width:16px;height:16px;position:relative}}
.pt{{display:block;width:6px;height:6px;border-radius:99px;position:absolute}}
.bu{{display:block;width:14px;height:11px;border:1px solid currentColor;border-radius:4px;position:absolute;left:1px;top:2px}}
.sl{{display:block;width:14px;height:5px;border:1px solid currentColor;border-radius:99px;position:relative;margin-top:3px}}
.slb{{display:block;width:3px;height:3px;border-radius:99px;position:absolute;top:0}}
.nfill{{flex:1}}
.nfoot{{flex:none;display:flex;flex-direction:column;gap:6px;padding-bottom:2px}}
.nfp{{height:26px;border:1px solid {border};border-radius:6px;background:{surface};display:flex;align-items:center;justify-content:center}}
.nfps{{font-size:12px;color:{muted}}}
.main{{flex:1;min-width:0;display:flex;flex-direction:column;gap:10px}}
.h1{{display:flex;align-items:baseline;flex:none}}
.h1b{{font-size:18px;font-weight:600}}
.h1s{{margin-left:8px;font-size:12px;color:{muted}}}
.cap{{font-size:11px;color:{dim}}}
.card{{border:1px solid {border};border-radius:8px;background:{surface};padding:10px 12px;display:flex;flex-direction:column;gap:8px}}
.soft{{background:{page}}}
.seg{{display:inline-flex;gap:2px;padding:3px;border:1px solid {border};border-radius:99px;background:{workspace};align-self:flex-start;flex:none}}
.sgb{{min-height:22px;padding:0 10px;border-radius:99px;font-size:12px;color:{muted}}}
.sgbn{{background:{selected};color:{accent}}}
.row{{display:flex;align-items:center;gap:8px}}
.lb{{width:64px;flex:none;font-size:13px;color:{muted}}}
.f{{flex:1;min-width:0;height:30px;border:1px solid {border};border-radius:6px;background:{page};display:flex;align-items:center;padding:0 8px}}
.foc{{border-color:{accent}}}
.fv{{font-size:13px;color:{text};white-space:nowrap}}
.mono{{font-family:Consolas,monospace;font-size:12px}}
.m11{{font-family:Consolas,monospace;font-size:11px}}
.g{{height:28px;padding:0 10px;border:1px solid {border};border-radius:6px;display:flex;align-items:center;flex:none}}
.gs{{font-size:11px;color:{muted};white-space:nowrap}}
.ac{{border-color:{accent}}}
.acs{{color:{accent}}}
.dg{{border-color:{danger}}}
.dgs{{color:{danger}}}
.p{{height:32px;padding:0 12px;border-radius:8px;background:{accent};display:flex;align-items:center;flex:none}}
.ps{{font-size:13px;font-weight:600;color:{on_accent};white-space:nowrap}}
.pill{{height:19px;padding:0 8px;border:1px solid {border};border-radius:99px;display:flex;align-items:center;flex:none}}
.pills{{font-size:11px;color:{muted};white-space:nowrap}}
.pilla{{border-color:{soft};background:{selected}}}
.pillsa{{color:{accent}}}
.pillk{{border-color:{success}}}
.pillsk{{color:{success}}}
.pilld{{border-color:{danger}}}
.pillsd{{color:{danger}}}
.pillw{{border-color:{warning}}}
.pillsw{{color:{warning}}}
.h11{{font-size:11px;color:{muted};line-height:1.5}}
.err{{font-size:12px;color:{danger};line-height:1.5}}
.sw{{width:34px;height:20px;flex:none;border-radius:99px;position:relative;background:{strong}}}
.swo{{background:{accent}}}
.swk{{display:block;width:14px;height:14px;border-radius:99px;background:{surface};position:absolute;top:3px;left:3px}}
.swr{{left:17px}}
.dis{{opacity:0.45}}
.dot{{width:8px;height:8px;flex:none;border-radius:99px}}
.foot{{height:44px;flex:none;display:flex;align-items:center;padding:0 18px;border-top:1px solid {border}}}
.fdot{{margin-right:8px}}
.note{{font-size:11px;color:{dim};flex:1;min-width:0;white-space:nowrap;overflow:hidden}}
.fa{{display:flex;gap:8px;flex:none}}
.hr{{height:1px;background:{border};flex:none}}
.nm2{{font-size:14px;font-weight:600}}
.meta{{font-size:16px;font-weight:600}}
.kv{{width:52px;flex:none;font-size:11px;color:{dim}}}
.sp{{flex:1}}
"""


def icon(kind, color):
    if kind == "person":
        return (
            f'<span class="ic" style="color:{color}"><i class="hd" style="background:{color}"></i>'
            f'<i class="bd" style="background:{color}"></i></span>'
        )
    if kind == "square":
        return f'<span class="ic" style="color:{color}"><i class="sq"><b class="sqb" style="background:{color}"></b></i></span>'
    if kind == "lines":
        return (
            f'<span class="ic" style="color:{color}"><i class="ln" style="background:{color}"></i>'
            f'<i class="ln" style="background:{color};width:12px"></i>'
            f'<i class="ln" style="background:{color};width:8px"></i></span>'
        )
    if kind == "petal":
        dots = "".join(
            f'<i class="pt" style="background:{color};left:{x}px;top:{y}px"></i>'
            for x, y in ((0, 0), (8, 0), (0, 8), (8, 8))
        )
        return f'<span class="ic" style="color:{color}"><span class="pet">{dots}</span></span>'
    if kind == "bubble":
        return f'<span class="ic" style="color:{color}"><i class="bu"></i></span>'
    return (
        f'<span class="ic" style="color:{color}"><i class="sl"><b class="slb" style="background:{color};left:2px"></b></i>'
        f'<i class="sl"><b class="slb" style="background:{color};left:10px"></b></i></span>'
    )


def field(label, value, mono=False, focus=False, width=None, disabled=False):
    cls = "f" + (" foc" if focus else "")
    style = ""
    if width:
        style = ' style="flex:0 0 %dpx"' % width
    if disabled:
        style = ' style="opacity:0.55"'
    v = "mono fv" if mono else "fv"
    return f'<div class="row"><span class="lb">{label}</span><div class="{cls}"{style}><span class="{v}">{value}</span></div></div>'


def btn(text, ac=False, danger=False, disabled=False):
    cls = "g"
    if ac:
        cls += " ac"
    if danger:
        cls += " dg"
    if disabled:
        cls += " dis"
    span = "gs"
    if ac:
        span = "gs acs"
    if danger:
        span = "gs dgs"
    if disabled:
        span += " dis"
    return f'<div class="{cls}"><span class="{span}">{text}</span></div>'


def prim(text):
    return f'<div class="p"><span class="ps">{text}</span></div>'


def p(text, kind=""):
    cls = "pill" + ((" " + kind) if kind else "")
    span = {"pilla": "pills pillsa", "pillk": "pills pillsk",
            "pilld": "pills pillsd", "pillw": "pills pillsw"}.get(kind, "pills")
    return f'<div class="{cls}"><span class="{span}">{text}</span></div>'


def sw(on=True, dis=False):
    cls = "sw" + (" swo" if on else "") + (" dis" if dis else "")
    knob = "swk" + (" swr" if on else "")
    return f'<div class="{cls}"><i class="{knob}"></i></div>'


def shell(t, title, sub, main, foot_dot, foot_note, theme, tail=""):
    items = []
    for no, nm, kind in NAV:
        on = no == "04"
        items.append(
            f'<div class="ni{" nion" if on else ""}">{icon(kind, t["accent"] if on else t["muted"])}'
            f'<span class="nm">{nm}</span><span class="no">{no}</span></div>'
        )
    return (
        f'<div class="win"><div class="head"><span class="ttl">设置</span>'
        f'<span class="ver">本机配置 v1.53</span><div class="sp"></div>'
        f'<div class="wb"><i class="wi"></i><i class="wi"></i></div></div>'
        f'<div class="body"><div class="nav">'
        f'<div class="search"><i class="sq0"></i><span class="sht">搜索设置</span></div>'
        f'{"".join(items)}<div class="nfill"></div>'
        f'<div class="nfoot"><div class="nfp"><span class="nfps">主题：{"浅色" if theme == "light" else "深色"}</span></div>'
        '<div class="nfp"><span class="nfps">桌面浮窗：开</span></div></div></div>'
        f'<div class="main"><div class="h1"><span class="h1b">{title}</span>'
        f'<span class="h1s">{sub}</span>{tail}</div>{main}</div></div>'
        f'<div class="foot"><i class="dot fdot" style="background:{foot_dot}"></i>'
        f'<span class="note">{foot_note}</span><div class="fa">{btn("放弃修改")}{prim("保存设置")}</div></div></div>'
    )


def s04b(t):
    seg = (
        '<div class="seg"><span class="sgb sgbn">stdio</span>'
        '<span class="sgb">SSE</span><span class="sgb">streamable-http</span></div>'
    )
    env = (
        '<div class="row"><span class="lb">环境变量</span>'
        '<div class="f" style="flex:0 0 100px"><span class="mono fv">API_TOKEN</span></div>'
        '<div class="f"><span class="mono fv">••••••••••3f</span></div>'
        + btn("替换") + "</div>"
    )
    ok = (
        '<div class="card"><div class="row">'
        f'<i class="dot" style="background:{t["success"]}"></i>{p("校验通过", "pillk")}'
        '<span class="h11">已发现 6 个工具，添加后默认全部关闭</span></div>'
        '<div class="h11">工具名会加上服务器前缀：mcp__filesystem__read_file。</div></div>'
    )
    main = (
        '<div class="card">' + seg
        + field("名称", "filesystem")
        + field("命令", "npx")
        + field("参数", "-y @modelcontextprotocol/server-filesystem", mono=True)
        + field("工作目录", "D:\\docs", mono=True)
        + env
        + '<div class="h11">密钥只写入 config/mcp_keys.json，不进数据库、不随备份导出。</div>'
        + f'<div class="row"><div class="sp"></div>{btn("取消")}{btn("重新校验")}{prim("添加")}</div>'
        + "</div>" + ok
    )
    return shell(t, "MCP 工具", "添加服务器", main, t["success"],
                 "添加后需手动开启工具才会注入到对话", THEME)


def s04c(t):
    fail = (
        '<div class="card"><div class="row">'
        f'<i class="dot" style="background:{t["danger"]}"></i>{p("连接失败", "pilld")}'
        '<span class="h11">HTTP 401 · 未通过远端鉴权</span></div>'
        '<div class="err">密钥可能已过期。错误文案不会回显令牌原文。</div>'
        + f'<div class="row">{btn("重试")}{btn("断开连接")}<div class="sp"></div>{prim("保存")}</div></div>'
    )
    danger = (
        f'<div class="card" style="border-color:{t["danger"]};flex-direction:row">'
        '<div style="flex:1;min-width:0"><div class="fv">移除服务器 web-search</div>'
        '<div class="h11">同时删除它的 2 个工具开关记录与 mcp_keys.json 里的凭据条目。</div></div>'
        + btn("移除", danger=True) + "</div>"
    )
    main = (
        '<div class="card">'
        + field("名称", "web-search", disabled=True)
        + '<div class="h11">改名等于新增服务器，工具开关全部回到关闭 · 传输类型编辑时锁定</div>'
        + field("地址", "https://mcp.example.com/search/sse", mono=True, focus=True)
        + '<div class="row"><span class="lb">请求头</span>'
        '<div class="f"><span class="mono fv">Authorization: Bearer ••••••••••••</span></div>'
        + btn("替换") + "</div>"
        + '<div class="row"><span class="lb">超时</span>'
        '<div class="f" style="flex:0 0 56px"><span class="fv">15</span></div>'
        '<div class="sp"></div>' + p("连接即出网", "pillw") + "</div></div>"
        + fail + danger
    )
    return shell(t, "MCP 工具", "编辑与移除服务器", main, t["danger"],
                 "凭据改动只落盘到 config/mcp_keys.json", THEME,
                 '<div class="pill" style="margin-left:8px"><span class="pills">streamable-http</span></div>')


def s04d(t):
    def row(dot, name, meta, right, s, extra=""):
        return (
            f'<div class="row"><i class="dot" style="background:{dot}"></i>'
            f'<div style="flex:1;min-width:0"><div class="nm2">{name}</div>'
            f'<div class="h11">{meta}</div></div>{right}{s}{extra}</div>'
        )

    main = (
        '<div class="row"><span class="h11">5 个服务 · 2 已连接 · 共 13 个工具</span><div class="sp"></div>'
        + btn("重新加载全部") + "</div>"
        + '<div class="card" style="gap:8px">'
        + row(t["success"], "filesystem", "stdio · npx -y @modelcontextprotocol/server-filesystem",
              p("6 个工具"), sw(True), btn("展开工具"))
        + '<div class="hr"></div>'
        + row(t["warning"], "web-search", "sse · mcp.example.com/search/sse · 正在连接并获取工具列表…",
              p("发现中"), sw(False, True))
        + '<div class="hr"></div>'
        + row(t["strong"], "calendar", "stdio · 未连接，缺少凭据", p("0 个工具"), sw(False),
              btn("连接并获取工具", ac=True))
        + '<div class="hr"></div>'
        + row(t["danger"], "browser-use", "http · 127.0.0.1:8931 · 握手超时（15 秒）",
              p("错误", "pilld"), sw(False), btn("重试"))
        + '<div class="hr"></div>'
        + row(t["dim"], "sentry", "sse · mcp.sentry.dev/sse · 服务已停用", p("5 个工具"), sw(False, True))
        + '<div class="h11">列表为空时：还没有添加 MCP 服务器，添加并连接后才会出现工具。</div></div>'
    )
    return shell(t, "MCP 工具", "服务器状态", main, t["success"],
                 "工具变更在下一次对话生效", THEME)


def s04e(t):
    tools = ""
    for nm, desc, on, tag, kind in (
        ("read_file", "读取工作区内的文件", True, "外部调用", "pilla"),
        ("write_file", "写入文件，可能覆盖已有内容", True, "远端写入", "pillw"),
        ("list_directory", "列出目录内容", False, "外部调用", "pilla"),
        ("search_files", "按文件名搜索", True, "外部调用", "pilla"),
    ):
        tools += (
            f'<div class="row"><div style="flex:1;min-width:0"><div class="mono">{nm}</div>'
            f'<div class="h11">{desc}</div></div>{p(tag, kind)}{sw(on)}</div>'
        )
    card = (
        '<div class="card"><div class="row">'
        f'<i class="dot" style="background:{t["success"]}"></i>'
        '<div style="flex:1;min-width:0"><div class="nm2">filesystem</div>'
        '<div class="h11">stdio · npx -y @modelcontextprotocol/server-filesystem</div></div>'
        + p("6 个工具") + sw(True) + btn("收起") + "</div></div>"
    )
    main = (
        card
        + '<div class="card soft" style="gap:6px">'
        + '<div class="row"><span class="h11">工具逐条开关 · 共 6 个工具，此处显示 4 个</span><div class="sp"></div>'
        + btn("全开") + btn("全关") + "</div>" + tools + '<div class="hr"></div>'
        + '<div class="h11">未开启的工具不进模型可见清单；开启的工具每次调用都先出提案，确认后才真正出网。</div>'
        + '<div class="h11">该服务器有 1 个工具的参数结构超出上限，已按关键字白名单收敛（$ref 与 $defs 丢弃）。</div>'
        + "</div>"
    )
    return shell(t, "MCP 工具", "逐工具开关", main, t["success"],
                 "工具变更在下一次对话生效", THEME)


def s04f(t):
    def card(chips, rows, code, foot, border, actions, note=None):
        r = "".join(
            f'<div class="row"><span class="kv">{k}</span>'
            f'<span class="mono fv">{v}</span></div>'
            for k, v in rows
        )
        return (
            f'<div class="card" style="border-color:{border};gap:6px">'
            + f'<div class="row" style="gap:4px">{chips}</div>'
            + f'<div style="display:flex;flex-direction:column;gap:2px">{r}</div>'
            + (code or "")
            + (f'<div class="h11">{note}</div>' if note else "")
            + (f'<div class="row">{foot}</div>' if foot else "")
            + (f'<div class="row">{actions}</div>' if actions else "")
            + "</div>"
        )

    code = (
        f'<div class="card" style="background:{t["code_bg"]};padding:6px 8px;gap:0">'
        f'<span class="m11" style="color:{t["muted"]}">{{ "path": "D:\\\\docs\\\\周报.md", "mode": "overwrite" }}</span>'
        f'<span class="m11" style="color:{t["muted"]}">"content": "# 本周进展…"（共 1 284 字符，已截断）</span>'
        "</div>"
    )
    c1 = card(
        p("待确认外部调用", "pilla") + p("等待审批"),
        [("目标", "mcp__filesystem__write_file"), ("风险", "外部调用"),
         ("服务器", "filesystem · stdio"), ("可撤销", "不可撤销"),
         ("模式", "工作"), ("有效期", "2026-11-03 15:42")],
        code,
        '<span class="h11">远端服务会真的收到这次调用；本机没有备份，执行后无法撤销。</span>',
        t["accent"], prim("允许这次调用") + btn("取消"),
    )
    c2 = card(
        p("已执行", "pilla") + p("已完成", "pillk"),
        [("目标", "mcp__web_search__search"), ("风险", "外部调用"),
         ("服务器", "web-search · sse"), ("回执", "成功 · 远端返回 3 条结果"),
         ("可撤销", "不可撤销")],
        "",
        '<span class="h11">回执已写入审计日志，导出时不含凭据。</span>',
        t["border"],
        btn("撤销", disabled=True)
        + '<span class="h11">外部调用不提供撤销，点击只会说明原因。</span>',
    )
    c3 = card(
        p("已拒绝", "pilld") + p("档位：保守"),
        [("目标", "mcp__calendar__create_event"), ("风险", "外部调用"),
         ("服务器", "calendar · stdio"), ("模式", "陪伴")],
        "", "", t["strong"], "",
        note="当前权限档位（保守）在陪伴模式下拒绝一切外部调用，模型已收到拒绝原因，不会重试。",
    )
    head = (
        '<div class="row"><span class="meta">外部调用确认卡</span>'
        + p("聊天流内组件") + "</div>"
        + '<div class="h11">这是插入聊天流的卡片，不是设置页。三种状态：待确认、已执行回执、被档位直接拒绝。</div>'
    )
    return '<div class="win"><div class="wrap">' + head + c1 + c2 + c3 + "</div></div>"


BOARDS = [
    ("S04b MCP 添加服务", s04b, (680, 560)),
    ("S04c MCP 编辑与移除", s04c, (680, 560)),
    ("S04d MCP 服务器状态", s04d, (680, 560)),
    ("S04e MCP 逐工具开关", s04e, (680, 560)),
    ("S04f 外部调用确认卡", s04f, (680, 780)),
]


def prune(css_text, html):
    used = set()
    for value in re.findall(r'class="([^"]*)"', html):
        used.update(value.split())
    keep = []
    for rule in re.findall(r"[^\n}]+\{[^}]*\}", css_text):
        sel = rule.split("{")[0]
        names = set(re.findall(r"\.([A-Za-z0-9_-]+)", sel))
        if names and not names <= used:
            continue
        keep.append(rule)
    return "\n".join(keep)


if __name__ == "__main__":
    want = os.environ.get("BOARDS")
    themes = os.environ.get("THEMES", "light,dark").split(",")
    for name, fn, wh in BOARDS:
        if want and name not in want.split(";"):
            continue
        for theme in themes:
            globals()["THEME"] = theme
            t = THEMES[theme]
            body = fn(t)
            css = prune(BASE.format(W=wh[0], H=wh[1], win_w=wh[0] - 16, win_h=wh[1] - 16, **t), body)
            html = "<style>%s</style>%s" % (css, body)
            slug = "%s-%s" % (name.split(" ")[0], theme)
            with open(os.path.join(OUT, slug + ".html"), "w", encoding="utf-8") as fh:
                fh.write(html)
            print(slug, len(html))
