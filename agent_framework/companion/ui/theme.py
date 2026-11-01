"""Semantic colour tokens and Qt stylesheet builders for the companion UI.

Widget code reads tokens instead of literals so the chat bubbles and the
dialogs all follow one theme contract.
"""

from __future__ import annotations

import re

FONT_FAMILY = "Microsoft YaHei UI"

DEFAULT_THEME = "charcoal-pink"

FONT_SCALES = (100, 125, 150, 200)

TYPE_TOKENS = {
    "caption": 11,
    "meta": 12,
    "ui": 13,
    "body": 14,
    "large": 16,
    "subtitle": 18,
    "display": 24,
}

SPACE_TOKENS = {
    "xs": 2,
    "sm": 4,
    "md": 8,
    "lg": 12,
    "xl": 18,
}

CHARCOAL_PINK = {
    "id": "charcoal-pink",
    "label": "经典深色",
    "scheme": "dark",
    "page": "#141414",
    "workspace": "#1b1b1b",
    "surface": "#252525",
    "hover": "#303030",
    "selected": "#3a232c",
    "border": "#363636",
    "border_strong": "#4a4a4a",
    "text": "#ebebeb",
    "muted": "#a3a3a3",
    "disabled": "#707070",
    "accent": "#ff5b8a",
    "accent_hover": "#ff7aa0",
    "accent_soft": "#ff9bb8",
    "on_accent": "#24151b",
    "danger": "#ff6b78",
    "success": "#56c58b",
    "warning": "#e6b45c",
    "bubble_agent": "#252525",
    "bubble_agent_border": "#363636",
    "bubble_user": "#3a232c",
    "bubble_user_border": "#4a2936",
    # M13.2: the wash the Web page puts over a user photo, mirrored here so the
    # Qt compatibility window dims the same image by the same amount.
    "bg_scrim": "rgba(8, 9, 12, 184)",
}

PEARL_WHITE = {
    "id": "pearl-white",
    "label": "珍珠白",
    "scheme": "light",
    "page": "#ececee",
    "workspace": "#ffffff",
    "surface": "#ffffff",
    "hover": "#fff1f6",
    "selected": "#fde0ed",
    "border": "#f2f2f2",
    "border_strong": "#d2d2d7",
    "text": "#1d1d1f",
    "muted": "#8e8e93",
    "disabled": "#6e6e73",
    "accent": "#ff5b8a",
    "accent_hover": "#f04b7c",
    "accent_soft": "#ff9fbd",
    "on_accent": "#ffffff",
    "danger": "#d94f61",
    "success": "#43a877",
    "warning": "#c58a31",
    "bubble_agent": "#f5f5f7",
    "bubble_agent_border": "#e5e5ea",
    "bubble_user": "#fde0ed",
    "bubble_user_border": "#ff9fbd",
    "bg_scrim": "rgba(247, 246, 244, 214)",
}

THEMES = {
    CHARCOAL_PINK["id"]: dict(CHARCOAL_PINK),
    PEARL_WHITE["id"]: dict(PEARL_WHITE),
}

def normalize_theme(name) -> str:
    theme_id = str(name or "").strip().lower()
    return theme_id if theme_id in THEMES else DEFAULT_THEME


def normalize_font_scale(value) -> int:
    try:
        percent = int(value)
    except (TypeError, ValueError):
        return 100
    return percent if percent in FONT_SCALES else 100


def font_px(token, font_scale=100) -> str:
    """Resolve a type token to a CSS pixel size honouring the scale preset."""
    size = TYPE_TOKENS.get(token, TYPE_TOKENS["body"])
    scale = normalize_font_scale(font_scale)
    return "{}px".format(max(9, int(round(size * scale / 100.0))))


def spacing(token) -> int:
    """Resolve a spacing token to logical pixels."""
    return SPACE_TOKENS.get(token, SPACE_TOKENS["md"])


def theme(name=None) -> dict:
    return dict(THEMES[normalize_theme(name)])


def theme_choices():
    return [(key, THEMES[key]["label"]) for key in (PEARL_WHITE["id"], CHARCOAL_PINK["id"])]


def _rule(selector, properties):
    body = "".join("{}: {};".format(key, value) for key, value in properties.items())
    return "{} {{\n{}\n}}".format(selector, body)


def _type(token):
    return "{}px".format(TYPE_TOKENS.get(token, TYPE_TOKENS["body"]))


def _rgba(color, alpha):
    value = str(color).lstrip("#")
    return "rgba({}, {}, {}, {})".format(
        int(value[0:2], 16),
        int(value[2:4], 16),
        int(value[4:6], 16),
        int(round(alpha * 255)),
    )


def stylesheet(name=None, font_scale=100, background=None) -> str:
    """Qt stylesheet for the compatibility UI.

    ``background`` is the resolved photo path; None keeps the solid theme so a
    page without an image is byte-for-byte the M12.5 sheet.
    """
    t = theme(name)
    scale = normalize_font_scale(font_scale)
    base_font = {
        "font-family": FONT_FAMILY,
        "font-size": _type("body"),
    }
    field = {
        "background": t["surface"],
        "border": "1px solid {}".format(t["border"]),
        "border-radius": "8px",
        "padding": "5px 8px",
        "color": t["text"],
        "selection-background-color": t["selected"],
        "selection-color": t["text"],
    }
    rules = [
        _rule(
            "QMainWindow, QDialog",
            dict(base_font, background=t["page"], color=t["text"]),
        ),
        _rule(
            "QWidget#shell",
            {
                **base_font,
                "background": t["workspace"],
                "border": "1px solid {}".format(t["border"]),
                "border-radius": "14px",
            },
        ),
        _rule(
            "QFrame#rail",
            dict(
                background=t["page"],
                **{"border-right": "1px solid {}".format(t["border"])},
            ),
        ),
        _rule("QLabel#brandSmall", {"font-size": _type("ui"), "font-weight": "600", "color": t["text"]}),
        _rule("QLabel#heading", {"font-size": _type("subtitle"), "font-weight": "600", "color": t["text"]}),
        _rule("QLabel#muted", {"color": t["muted"]}),
        _rule("QLabel#faint", {"color": t["disabled"], "font-size": _type("caption")}),
        _rule(
            "QLabel#pill",
            {
                "background": t["surface"],
                "border": "1px solid {}".format(t["border"]),
                "border-radius": "9px",
                "padding": "1px 8px",
                "color": t["muted"],
                "font-size": _type("caption"),
            },
        ),
        _rule(
            "QLabel#pillActive",
            {
                "background": t["selected"],
                "border": "1px solid {}".format(t["accent"]),
                "border-radius": "9px",
                "padding": "1px 8px",
                "color": t["accent"],
                "font-size": _type("caption"),
                "font-weight": "600",
            },
        ),
        _rule(
            "QPushButton",
            dict(base_font, **{
                "background": t["surface"],
                "border": "1px solid {}".format(t["border"]),
                "border-radius": "8px",
                "padding": "6px 12px",
                "color": t["text"],
            }),
        ),
        _rule("QPushButton:hover", {"background": t["hover"]}),
        _rule("QPushButton:pressed", {"background": t["selected"]}),
        _rule(
            "QPushButton:disabled",
            {"background": t["surface"], "color": t["disabled"], "border": "1px solid {}".format(t["border"])},
        ),
        _rule(
            "QPushButton#primary",
            {
                "background": t["accent"],
                "border": "1px solid {}".format(t["accent"]),
                "color": t["on_accent"],
                "font-weight": "600",
                "border-radius": "16px",
                "padding": "6px 16px",
            },
        ),
        _rule("QPushButton#primary:hover", {"background": t["accent_hover"], "border-color": t["accent_hover"]}),
        _rule("QPushButton#primary:pressed", {"background": t["accent"]}),
        _rule(
            'QPushButton#primary[mode="stop"]',
            {
                "background": t["danger"],
                "border-color": t["danger"],
                "color": t["on_accent"],
            },
        ),
        _rule(
            'QPushButton#primary[mode="stop"]:hover',
            {"background": t["danger"], "border-color": t["danger"]},
        ),
        _rule(
            "QPushButton#primary:disabled",
            {"background": t["hover"], "color": t["disabled"], "border-color": t["hover"]},
        ),
        _rule(
            "QPushButton#primary:focus, QPushButton#ghost:focus, "
            "QPushButton#railAction:focus, QPushButton#railMini:focus, "
            "QPushButton#bubbleAction:focus, QPushButton#toolToggle:focus, "
            "QPushButton#runStop:focus, QPushButton#jumpLatest:focus, "
            "QPushButton#inspectorClose:focus, QPushButton#settingsButton:focus",
            {"border": "1px solid {}".format(t["accent"])},
        ),
        _rule(
            "QPushButton#active",
            {
                "background": t["selected"],
                "border": "1px solid {}".format(t["accent"]),
                "color": t["accent"],
                "font-weight": "600",
            },
        ),
        _rule(
            "QPushButton#railAction",
            {
                "background": "transparent",
                "border": "0",
                "border-radius": "8px",
                "padding": "7px 8px",
                "color": t["text"],
                "text-align": "left",
            },
        ),
        _rule("QPushButton#railAction:hover", {"background": t["hover"]}),
        _rule("QPushButton#railAction:pressed", {"background": t["selected"]}),
        _rule(
            "QLineEdit#railSearch",
            {
                **base_font,
                "font-size": _type("meta"),
                "background": t["surface"],
                "border": "1px solid {}".format(t["border"]),
                "border-radius": "8px",
                "padding": "5px 8px",
                "color": t["text"],
            },
        ),
        _rule(
            "QLineEdit#railSearch:focus",
            {"border": "1px solid {}".format(t["accent"])},
        ),
        _rule("QLabel#railIcon", {"background": "transparent"}),
        _rule(
            "QFrame#workspaceRow",
            {
                "background": "transparent",
                "border": "0",
                "border-radius": "7px",
            },
        ),
        _rule("QFrame#workspaceRow:hover", {"background": t["hover"]}),
        _rule(
            "QLabel#workspaceName",
            {
                "background": "transparent",
                "color": t["text"],
                "font-size": _type("meta"),
                "font-weight": "600",
            },
        ),
        _rule(
            "QPushButton#railMini",
            {
                "background": "transparent",
                "border": "0",
                "border-radius": "5px",
                "padding": "0",
            },
        ),
        _rule("QPushButton#railMini:hover", {"background": t["selected"]}),
        _rule(
            "QScrollArea#sessionList",
            {"background": "transparent", "border": "0"},
        ),
        _rule("QWidget#sessionListBody", {"background": "transparent"}),
        _rule(
            "QFrame#sessionRow",
            {
                "background": "transparent",
                "border": "0",
                "border-radius": "7px",
            },
        ),
        _rule("QFrame#sessionRow:hover", {"background": t["hover"]}),
        _rule(
            "QFrame#sessionRow:focus, QFrame#sessionActive:focus",
            {
                "border": "1px solid {}".format(t["accent"]),
                "border-radius": "7px",
            },
        ),
        _rule(
            "QFrame#sessionActive",
            {
                "background": t["selected"],
                "border": "0",
                "border-radius": "7px",
            },
        ),
        _rule("QFrame#sessionRow QLabel, QFrame#sessionActive QLabel", {"background": "transparent"}),
        _rule(
            "QLabel#sessionLabel",
            {"color": t["text"], "font-size": _type("ui")},
        ),
        _rule(
            "QFrame#sessionActive QLabel#sessionLabel",
            {"color": t["accent"], "font-weight": "600"},
        ),
        _rule(
            "QLabel#sessionMeta",
            {"color": t["muted"], "font-size": _type("caption")},
        ),
        _rule(
            "QFrame#sessionActive QLabel#sessionMeta",
            {"color": t["muted"]},
        ),
        _rule(
            "QLabel#sessionGroup",
            {
                "color": t["muted"],
                "font-size": _type("meta"),
                "font-weight": "600",
                "padding": "8px 8px 2px 8px",
            },
        ),
        _rule(
            "QPushButton#sessionMore",
            {
                "background": "transparent",
                "border": "0",
                "border-radius": "4px",
                "color": t["muted"],
                "font-size": _type("ui"),
                "padding": "0",
            },
        ),
        _rule(
            "QPushButton#sessionMore:hover",
            {"background": t["hover"], "color": t["text"]},
        ),
        _rule(
            "QPushButton#sessionMore:focus",
            {"border": "1px solid {}".format(t["accent"])},
        ),
        _rule(
            "QLabel#sessionEmpty",
            {"color": t["disabled"], "font-size": _type("caption")},
        ),
        _rule(
            "QFrame#settingsRow",
            {
                "background": t["surface"],
                "border": "1px solid {}".format(t["border"]),
                "border-radius": "10px",
            },
        ),
        _rule(
            "QFrame#settingsRow:hover",
            {
                "background": t["hover"],
                "border": "1px solid {}".format(t["border_strong"]),
            },
        ),
        _rule("QFrame#settingsRow QLabel", {"background": "transparent"}),
        _rule("QLabel#settingsTitle", {"color": t["text"], "font-weight": "600"}),
        _rule(
            "QLabel#settingsArrow",
            {"color": t["muted"], "font-size": _type("subtitle")},
        ),
        _rule(
            "QPushButton#settingsButton",
            {
                "background": t["surface"],
                "border": "1px solid {}".format(t["border"]),
                "border-radius": "10px",
                "padding": "9px 12px",
                "color": t["text"],
                "text-align": "left",
            },
        ),
        _rule(
            "QPushButton#settingsButton:hover",
            {"background": t["hover"], "border-color": t["border_strong"]},
        ),
        _rule(
            "QPushButton#settingsButton:checked",
            {
                "background": t["selected"],
                "border": "1px solid {}".format(t["accent"]),
                "color": t["accent"],
                "font-weight": "600",
            },
        ),
        _rule(
            "QFrame#settingsRow:focus",
            {"border": "2px solid {}".format(t["accent"])},
        ),
        _rule(
            "QFrame#settingsNav",
            {
                "background": t["workspace"],
                "border": "1px solid {}".format(t["border"]),
                "border-radius": "12px",
            },
        ),
        _rule(
            "QFrame#settingsNavItem",
            {
                "background": "transparent",
                "border": "1px solid transparent",
                "border-radius": "9px",
            },
        ),
        _rule("QFrame#settingsNavItem:hover", {"background": t["hover"]}),
        _rule(
            'QFrame#settingsNavItem[active="true"]',
            {
                "background": t["selected"],
                "border": "1px solid {}".format(t["accent"]),
            },
        ),
        _rule(
            "QFrame#settingsNavItem:focus",
            {"border": "2px solid {}".format(t["accent"])},
        ),
        _rule(
            "QFrame#settingsNavItem QLabel",
            {"background": "transparent"},
        ),
        _rule(
            "QLabel#settingsNavTitle",
            {"color": t["text"], "font-size": _type("body"), "font-weight": "600"},
        ),
        _rule(
            'QFrame#settingsNavItem[active="true"] QLabel#settingsNavTitle',
            {"color": t["accent"]},
        ),
        _rule(
            "QLabel#settingsNavMeta",
            {"color": t["muted"], "font-size": _type("caption")},
        ),
        _rule(
            "QLineEdit#settingsSearch",
            {
                "background": t["surface"],
                "border": "1px solid {}".format(t["border"]),
                "border-radius": "8px",
                "padding": "6px 9px",
                "color": t["text"],
                "selection-background-color": t["accent"],
            },
        ),
        _rule(
            "QLineEdit#settingsSearch:focus",
            {"border": "1px solid {}".format(t["accent"])},
        ),
        _rule(
            "QFrame#settingsSummaryCard",
            {
                "background": t["surface"],
                "border": "1px solid {}".format(t["border"]),
                "border-radius": "10px",
            },
        ),
        _rule("QFrame#settingsSummaryCard QLabel", {"background": "transparent"}),
        _rule(
            "QLabel#settingsSummaryText",
            {"color": t["text"], "font-size": _type("ui")},
        ),
        _rule(
            "QLabel#settingsSummaryDetail",
            {"color": t["muted"], "font-size": _type("meta")},
        ),
        _rule(
            "QPushButton#settingsExpand",
            {
                "background": "transparent",
                "border": "1px solid {}".format(t["border_strong"]),
                "border-radius": "7px",
                "padding": "2px 8px",
                "color": t["muted"],
                "font-size": _type("caption"),
            },
        ),
        _rule(
            "QPushButton#settingsExpand:hover",
            {"background": t["hover"], "color": t["text"]},
        ),
        _rule(
            "QPushButton#settingsExpand:focus",
            {"border": "1px solid {}".format(t["accent"])},
        ),
        _rule(
            "QScrollArea#settingsScroll",
            {"background": "transparent", "border": "0"},
        ),
        _rule(
            "QWidget#settingsPageBody",
            {"background": "transparent"},
        ),
        _rule("QWidget#settingsStack", {"background": "transparent"}),
        _rule(
            "QPushButton#ghost",
            {
                "background": "transparent",
                "border": "1px solid {}".format(t["border_strong"]),
                "border-radius": "8px",
                "padding": "5px 10px",
                "color": t["muted"],
            },
        ),
        _rule("QPushButton#ghost:hover", {"background": t["hover"], "color": t["text"]}),
        _rule(
            "QPushButton#danger",
            {
                "background": "transparent",
                "border": "1px solid {}".format(t["danger"]),
                "border-radius": "8px",
                "color": t["danger"],
            },
        ),
        _rule("QPushButton#danger:hover", {"background": t["selected"]}),
        _rule(
            "QFrame#modeSwitch",
            {
                "background": t["surface"],
                "border": "1px solid {}".format(t["border"]),
                "border-radius": "14px",
                "padding": "2px",
            },
        ),
        _rule(
            "QFrame#modeSwitch QPushButton",
            {
                "background": "transparent",
                "border": "0",
                "border-radius": "11px",
                "padding": "5px 20px",
                "color": t["muted"],
            },
        ),
        _rule("QFrame#modeSwitch QPushButton:hover", {"background": t["hover"], "color": t["text"]}),
        _rule(
            "QFrame#modeSwitch QPushButton#active",
            {
                "background": t["selected"],
                "color": t["accent"],
                "border": "0",
                "font-weight": "600",
            },
        ),
        _rule("QScrollArea#chat", {"background": t["workspace"], "border": "0"}),
        _rule("QWidget#chatList", {"background": t["workspace"]}),
        _rule(
            "QFrame#bubbleAgent",
            {
                "background": t["bubble_agent"],
                "border": "1px solid {}".format(t["bubble_agent_border"]),
                "border-radius": "12px",
                "padding": "8px 10px",
            },
        ),
        _rule(
            "QFrame#bubbleUser",
            {
                "background": t["bubble_user"],
                "border": "1px solid {}".format(t["bubble_user_border"]),
                "border-radius": "12px",
                "padding": "8px 10px",
            },
        ),
        _rule("QFrame#bubbleAgent QLabel, QFrame#bubbleUser QLabel", {"background": "transparent", "color": t["text"]}),
        _rule(
            "QLabel#bubbleBody",
            {
                "font-size": _type("body"),
                "background": "transparent",
                "color": t["text"],
            },
        ),
        _rule("QLabel#bubbleMeta", {"color": t["muted"], "font-size": _type("caption"), "background": "transparent"}),
        _rule(
            "QLabel#bubbleRole",
            {
                "color": t["accent"],
                "font-size": _type("caption"),
                "font-weight": "600",
                "background": "transparent",
            },
        ),
        _rule(
            "QFrame#bubbleUser QLabel#bubbleRole",
            {
                "color": t["muted"],
                "font-weight": "600",
                "background": "transparent",
            },
        ),
        _rule(
            "QPushButton#bubbleAction",
            {
                "background": "transparent",
                "border": "0",
                "border-radius": "6px",
                "padding": "0 6px",
                "color": t["muted"],
                "font-size": _type("caption"),
            },
        ),
        _rule("QPushButton#bubbleAction:hover", {"background": t["hover"], "color": t["text"]}),
        _rule(
            "QLabel#dateDivider",
            {
                "color": t["disabled"],
                "font-size": _type("ui"),
                "padding": "6px 0 2px 0",
                "background": "transparent",
            },
        ),
        _rule(
            "QFrame#emptyState",
            {
                "background": t["surface"],
                "border": "1px solid {}".format(t["border"]),
                "border-radius": "14px",
            },
        ),
        _rule("QFrame#emptyState QLabel", {"background": "transparent"}),
        _rule(
            "QLabel#emptyTitle",
            {"color": t["text"], "font-size": _type("display"), "font-weight": "600"},
        ),
        _rule(
            "QLabel#emptyBody",
            {"color": t["muted"], "font-size": _type("ui")},
        ),
        _rule(
            "QLabel#emptyRecent",
            {"color": t["accent_soft"], "font-size": _type("meta")},
        ),
        _rule(
            "QPushButton#emptyAction",
            {
                "background": "transparent",
                "border": "1px solid {}".format(t["border_strong"]),
                "border-radius": "12px",
                "padding": "4px 10px",
                "color": t["text"],
                "font-size": _type("ui"),
            },
        ),
        _rule(
            "QPushButton#emptyAction:hover",
            {"background": t["hover"], "border-color": t["accent"]},
        ),
        _rule(
            "QPushButton#emptyAction:focus",
            {
                "border": "2px solid {}".format(t["accent"]),
                "padding": "3px 9px",
            },
        ),
        _rule(
            "QFrame#runBar",
            {
                "background": "transparent",
                "border": "0",
                "border-bottom": "1px solid {}".format(t["border"]),
                "padding": "0 2px 5px 2px",
            },
        ),
        _rule(
            "QLabel#runStage",
            {
                "color": t["accent"],
                "font-size": _type("ui"),
                "font-weight": "600",
                "background": "transparent",
            },
        ),
        _rule(
            'QLabel#runStage[stage="failed"]',
            {"color": t["danger"]},
        ),
        _rule(
            'QLabel#runStage[stage="cancelled"]',
            {"color": t["warning"]},
        ),
        _rule(
            'QLabel#runStage[stage="completed"]',
            {"color": t["success"]},
        ),
        _rule(
            "QLabel#runDetail",
            {"color": t["muted"], "font-size": _type("ui"), "background": "transparent"},
        ),
        _rule(
            "QLabel#runElapsed",
            {"color": t["disabled"], "font-size": _type("ui"), "background": "transparent"},
        ),
        _rule(
            "QPushButton#runStop",
            {
                "background": "transparent",
                "border": "1px solid {}".format(t["border_strong"]),
                "border-radius": "10px",
                "padding": "1px 10px",
                "color": t["muted"],
                "font-size": _type("ui"),
            },
        ),
        _rule(
            "QPushButton#runStop:hover",
            {"background": t["hover"], "color": t["text"]},
        ),
        _rule(
            "QPushButton#runStepsToggle",
            {
                "background": "transparent",
                "border": "0",
                "padding": "1px 6px",
                "color": t["muted"],
                "font-size": _type("meta"),
            },
        ),
        _rule(
            "QPushButton#runStepsToggle:hover",
            {"background": t["hover"], "color": t["text"]},
        ),
        _rule(
            "QScrollArea#runStepsArea",
            {
                "background": "transparent",
                "border": "0",
                "border-top": "1px solid {}".format(t["border"]),
                "padding": "3px 0 0 0",
            },
        ),
        _rule(
            "QScrollArea#runStepsArea > QWidget",
            {"background": "transparent", "border": "0"},
        ),
        _rule(
            "QLabel#runSteps",
            {
                "color": t["muted"],
                "font-size": _type("meta"),
                "background": "transparent",
                "padding": "0 0 0 8px",
            },
        ),
        _rule(
            "QPushButton#jumpLatest",
            {
                "background": t["accent"],
                "border": "0",
                "border-radius": "13px",
                "padding": "5px 12px",
                "color": t["on_accent"],
                "font-size": _type("meta"),
                "font-weight": "600",
            },
        ),
        _rule(
            "QPushButton#jumpLatest:hover",
            {"background": t["accent_hover"]},
        ),
        _rule(
            "QFrame#toolCard",
            {
                "background": t["surface"],
                "border": "1px solid {}".format(t["border"]),
                "border-left": "3px solid {}".format(t["accent"]),
                "border-radius": "8px",
                "padding": "6px 9px",
            },
        ),
        _rule(
            "QFrame#toolCardDone",
            {
                "background": t["surface"],
                "border": "1px solid {}".format(t["border"]),
                "border-left": "3px solid {}".format(t["success"]),
                "border-radius": "8px",
                "padding": "6px 9px",
            },
        ),
        _rule(
            "QFrame#toolCardFailed",
            {
                "background": t["surface"],
                "border": "1px solid {}".format(t["border"]),
                "border-left": "3px solid {}".format(t["danger"]),
                "border-radius": "8px",
                "padding": "6px 9px",
            },
        ),
        _rule("QFrame#toolCard QLabel, QFrame#toolCardDone QLabel, QFrame#toolCardFailed QLabel", {"background": "transparent", "color": t["text"]}),
        _rule(
            "QFrame#composer",
            {
                "background": t["surface"],
                "border": "1px solid {}".format(t["border"]),
                "border-radius": "14px",
            },
        ),
        _rule(
            "QFrame#composer QTextEdit",
            {"background": "transparent", "border": "0", "padding": "0", "color": t["text"], "font-size": _type("body")},
        ),
        _rule(
            "QLabel#composerPill",
            {
                "background": t["hover"],
                "border": "1px solid {}".format(t["border"]),
                "border-radius": "9px",
                "color": t["muted"],
                "font-size": _type("meta"),
                "padding": "1px 7px",
            },
        ),
        _rule(
            'QLabel#composerPill[variant="mode"]',
            {"color": t["accent"]},
        ),
        _rule(
            "QLabel#composerCount",
            {"color": t["disabled"], "font-size": _type("caption")},
        ),
        _rule(
            'QLabel#composerCount[warning="true"]',
            {"color": t["warning"]},
        ),
        _rule(
            'QLabel#composerCount[error="true"]',
            {"color": t["danger"]},
        ),
        _rule(
            "QFrame#confirm",
            {
                "background": t["surface"],
                "border": "1px solid {}".format(t["accent"]),
                "border-radius": "12px",
                "padding": "8px",
            },
        ),
        _rule(
            "QFrame#approvalCard",
            {
                "background": t["surface"],
                "border": "1px solid {}".format(t["accent"]),
                "border-radius": "12px",
                "padding": "6px",
            },
        ),
        _rule("QFrame#approvalCard QLabel", {"background": "transparent"}),
        _rule(
            "QLabel#approvalMeta",
            {"color": t["text"], "font-size": _type("ui")},
        ),
        _rule(
            "QLabel#approvalPlan",
            {"color": t["muted"], "font-size": _type("meta")},
        ),
        _rule(
            "QLabel#approvalResult",
            {"color": t["text"], "font-size": _type("ui")},
        ),
        _rule(
            "QLabel#diffSummary",
            {"color": t["muted"], "font-size": _type("caption"), "font-weight": "600"},
        ),
        _rule(
            "QLabel#diffAdd",
            {
                "color": t["success"],
                "font-family": "Consolas",
                "font-size": _type("caption"),
            },
        ),
        _rule(
            "QLabel#diffDel",
            {
                "color": t["danger"],
                "font-family": "Consolas",
                "font-size": _type("caption"),
            },
        ),
        _rule(
            "QLabel#diffContext",
            {
                "color": t["muted"],
                "font-family": "Consolas",
                "font-size": _type("caption"),
            },
        ),
        _rule(
            "QPushButton#toolToggle",
            {
                "background": "transparent",
                "border": "0",
                "color": t["muted"],
                "font-size": _type("caption"),
                "padding": "0 4px",
                "border-radius": "6px",
            },
        ),
        _rule(
            "QPushButton#toolToggle:hover",
            {"background": t["hover"], "color": t["text"]},
        ),
        _rule(
            "QLabel#toolDetail",
            {
                "color": t["muted"],
                "font-family": "Consolas",
                "font-size": _type("caption"),
            },
        ),
        _rule(
            "QLabel#toolError",
            {"color": t["danger"], "font-size": _type("caption")},
        ),
        _rule(
            "QLabel#toolSource",
            {"color": t["accent_soft"], "font-size": _type("caption")},
        ),
        _rule(
            "QLabel#toolHandle",
            {"color": t["disabled"], "font-size": _type("caption")},
        ),
        _rule(
            "QPushButton#inspectorClose",
            {
                "background": "transparent",
                "border": "0",
                "color": t["muted"],
                "font-size": _type("large"),
                "border-radius": "6px",
            },
        ),
        _rule(
            "QPushButton#inspectorClose:hover",
            {"background": t["hover"], "color": t["text"]},
        ),
        _rule(
            "QTabWidget#inspectorTabs::pane",
            {"border": "0", "top": "0"},
        ),
        _rule(
            "QTabWidget#inspectorTabs QTabBar::tab",
            {
                "background": "transparent",
                "color": t["muted"],
                "padding": "4px 8px",
                "border": "0",
                "border-bottom": "2px solid transparent",
                "font-size": _type("meta"),
            },
        ),
        _rule(
            "QTabWidget#inspectorTabs QTabBar::tab:selected",
            {
                "color": t["accent"],
                "border-bottom": "2px solid {}".format(t["accent"]),
            },
        ),
        _rule(
            "QTabWidget#inspectorTabs QTabBar::tab:hover",
            {"color": t["text"], "background": t["hover"]},
        ),
        _rule("QSplitter#mainSplitter::handle", {"background": t["page"]}),
        _rule(
            "QSplitter#mainSplitter::handle:hover",
            {"background": t["accent"]},
        ),
        _rule(
            "QFrame#inspector",
            {
                "background": t["page"],
                "border-left": "1px solid {}".format(t["border"]),
            },
        ),
        _rule("QScrollArea#inspectorArea", {"background": t["page"], "border": "0"}),
        _rule("QWidget#inspectorList", {"background": t["page"]}),
        _rule(
            "QFrame#section",
            {
                "background": t["surface"],
                "border": "1px solid {}".format(t["border"]),
                "border-radius": "10px",
                "padding": "8px",
            },
        ),
        _rule("QFrame#section QLabel", {"background": "transparent"}),
        _rule("QLabel#sectionTitle", {"color": t["muted"], "font-size": _type("meta"), "font-weight": "600"}),
        _rule("QLabel#accentText", {"color": t["accent"], "font-weight": "600"}),
        _rule("QTextBrowser", dict(base_font, **field)),
        _rule("QLineEdit", dict(base_font, **field)),
        _rule("QPlainTextEdit", dict(base_font, **field)),
        _rule("QTextEdit", dict(base_font, **field)),
        _rule("QSpinBox", dict(base_font, **field)),
        _rule("QDoubleSpinBox", dict(base_font, **field)),
        _rule("QComboBox", dict(base_font, **field)),
        _rule(
            "QComboBox QAbstractItemView",
            {
                "background": t["surface"],
                "border": "1px solid {}".format(t["border"]),
                "color": t["text"],
                "selection-background-color": t["selected"],
                "selection-color": t["accent"],
            },
        ),
        _rule("QLineEdit:focus, QTextEdit:focus, QTextBrowser:focus, QComboBox:focus", {"border": "1px solid {}".format(t["accent"])}),
        _rule("QCheckBox", {"background": "transparent", "color": t["text"]}),
        _rule(
            "QTableWidget, QListWidget, QTreeView",
            {
                **base_font,
                "background": t["workspace"],
                "alternate-background-color": t["surface"],
                "border": "1px solid {}".format(t["border"]),
                "border-radius": "8px",
                "color": t["text"],
                "gridline-color": t["border"],
                "selection-background-color": t["selected"],
                "selection-color": t["text"],
            },
        ),
        _rule("QTableWidget::item:selected, QListWidget::item:selected", {"background": t["selected"], "color": t["text"]}),
        _rule(
            "QHeaderView::section",
            {
                "background": t["surface"],
                "color": t["text"],
                "padding": "6px",
                "border": "0",
                "border-bottom": "1px solid {}".format(t["border"]),
            },
        ),
        _rule(
            "QTabWidget::pane",
            {"border": "1px solid {}".format(t["border"]), "border-radius": "8px", "top": "-1px"},
        ),
        _rule(
            "QTabBar::tab",
            {
                "background": "transparent",
                "color": t["muted"],
                "padding": "6px 14px",
                "border": "0",
                "border-bottom": "2px solid transparent",
            },
        ),
        _rule("QTabBar::tab:selected", {"color": t["accent"], "border-bottom": "2px solid {}".format(t["accent"])}),
        _rule("QTabBar::tab:hover", {"color": t["text"], "background": t["hover"]}),
        _rule(
            "QMenu",
            {
                "background": t["surface"],
                "border": "1px solid {}".format(t["border"]),
                "color": t["text"],
            },
        ),
        _rule("QMenu::item:selected", {"background": t["selected"], "color": t["accent"]}),
        _rule(
            "QToolTip",
            {
                "background": t["surface"],
                "color": t["text"],
                "border": "1px solid {}".format(t["border"]),
            },
        ),
        _rule(
            "QScrollBar:vertical",
            {"background": "transparent", "width": "10px", "margin": "2px"},
        ),
        _rule(
            "QScrollBar::handle:vertical",
            {"background": t["border_strong"], "min-height": "24px", "border-radius": "4px"},
        ),
        _rule("QScrollBar::handle:vertical:hover", {"background": t["accent"]}),
        _rule("QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical", {"height": "0", "width": "0"}),
        _rule("QScrollBar:horizontal", {"background": "transparent", "height": "10px", "margin": "2px"}),
        _rule(
            "QScrollBar::handle:horizontal",
            {"background": t["border_strong"], "min-width": "24px", "border-radius": "4px"},
        ),
        _rule("QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal", {"height": "0", "width": "0"}),
    ]
    if background is not None:
        image = str(background).replace("\\", "/")
        rules.extend(
            [
                _rule(
                    "QMainWindow, QDialog",
                    {
                        "background-image": 'url("{}")'.format(image),
                        "background-position": "center",
                        "background-repeat": "no-repeat",
                    },
                ),
                # The scrim sits on the shell; the working surfaces go translucent
                # so the photo reads through them without hurting text contrast.
                _rule("QWidget#shell", {"background": t["bg_scrim"]}),
                _rule("QFrame#rail", {"background": _rgba(t["page"], 0.88)}),
                _rule(
                    "QScrollArea#chat, QWidget#chatList",
                    {"background": _rgba(t["workspace"], 0.82)},
                ),
                _rule(
                    "QFrame#inspector, QScrollArea#inspectorArea, QWidget#inspectorList",
                    {"background": _rgba(t["page"], 0.88)},
                ),
            ]
        )
    css = "\n".join(rules)
    if scale != 100:
        css = re.sub(
            r"font-size:\s*(\d+)px",
            lambda match: "font-size: {}px".format(
                max(9, int(round(int(match.group(1)) * scale / 100.0)))
            ),
            css,
        )
    return css
