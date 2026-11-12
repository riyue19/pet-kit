"""Built-in actions that are not launchable skills: they only show something on the pet side. They are
registered in config/skills.yaml like the skills (BUILTIN_NAMES in action/skills/config.py: on/off and
optional confirmation, no targets). The LLM may call them only on turns where the user spoke to the pet.

show_code  -> a window with a piece of code (and a copy button)
set_style  -> a proposal to change the pet's own look with CSS; the pet window previews it and the user keeps or reverts
"""

import re
from typing import Any

CODE_MAX_CHARS = 20000
TITLE_MAX_CHARS = 60
CSS_MAX_CHARS = 4000
LANGUAGE_RE = re.compile(r"^[A-Za-z0-9+#._-]{1,20}$")
# The pet window's CSP already blocks external loads; refuse the constructs anyway.
BANNED_CSS = re.compile(r"@import|url\s*\(|</|<!--|expression\s*\(|javascript:|behavior\s*:", re.IGNORECASE)

SHOW_CODE_LINE = (
    "show_code：把一段代码/脚本/CSS 显示在桌面上的代码窗口（主人可以复制）。主人让你写代码、脚本、命令、CSS 时用，"
    'args: {"title": "简短标题", "language": "css/python/js/powershell…", "code": "完整代码"}。'
    "代码只放在 code 里，不要贴进 text；text 只说一句「写好啦，在代码窗口里」之类。只写简单、短的代码（几十行以内）。"
)
SET_STYLE_LINE = (
    "set_style：修改你自己的外观（对话气泡、输入框），只在主人明确要求改样式/颜色/圆角时用。"
    'args: {"css": "普通 CSS"}。气泡是 #bubble，输入框是 #chat-input。优先用变量改：'
    "#bubble { --b-bg: 背景; --b-fg: 文字色; --b-border: 边框简写; --b-radius: 圆角; --b-shadow: 阴影 }，"
    "#chat-input { --i-bg: 背景; --i-bg-active: 聚焦时背景; --i-fg: 文字色; --i-ph: 提示文字色; --i-border: 边框; --i-radius: 圆角 }。"
    "不要用 @import、url() 和选择器之外的东西，css 不超过 4000 字。要恢复默认就传 "
    '{"css": "", "replace": true}。text 只说一句「改好了，你看看合不合适」之类；主人会看到预览并决定保留还是还原。'
)


def _str(v: Any) -> str | None:
    return v if isinstance(v, str) else None


def code_message(args: dict[str, Any]) -> dict[str, str] | str:
    """The WS message for the pet, or a readable reason why the call is refused."""
    code = _str(args.get("code"))
    if code is None or not code.strip():
        return "没有代码内容"
    if len(code) > CODE_MAX_CHARS:
        return f"代码太长（上限 {CODE_MAX_CHARS} 字）"
    title = (_str(args.get("title")) or "代码").strip()[:TITLE_MAX_CHARS] or "代码"
    language = _str(args.get("language")) or ""
    return {"type": "code", "title": title, "language": language if LANGUAGE_RE.match(language) else "", "code": code}


def css_message(args: dict[str, Any]) -> dict[str, Any] | str:
    css = _str(args.get("css"))
    if css is None:
        return "没有 CSS 内容"
    replace = args.get("replace") is True
    if not css.strip() and not replace:
        return "没有 CSS 内容"
    if len(css) > CSS_MAX_CHARS:
        return f"CSS 太长（上限 {CSS_MAX_CHARS} 字）"
    if BANNED_CSS.search(css):
        return "CSS 里有不允许的内容（@import、url()、HTML 标签等）"
    return {"type": "set_css", "css": css.strip(), "replace": replace}
