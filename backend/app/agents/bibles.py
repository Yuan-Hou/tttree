"""文风圣经 / 画风圣经的「模板 + 每故事自定义」支撑(故事内设置 · bible 子步)。

两本圣经原是全局单文件、模块常量。现升级为「每故事可自定义」+「可选预制模板」:

  - 默认内容:打包文件 prompts/style_bible/default.md、prompts/visual_style_bible/default.md
    (即原 style_bible.md / visual_style_bible.md 搬入对应文件夹)。某故事未自定义(StorySettings
    里该字段为空串)→ 回退到这份默认,旧故事行为完全不变。
  - 模板:启动时扫描上述两个文件夹下的全部 *.md,文件名(去扩展)即模板名,内容即模板正文。
    放一个新 .md 进去 → 下次启动多一个可选模板。"default" 恒排首位。
  - 每故事自定义:存在 StorySettings.style_bible / visual_style_bible(空串=用默认),由设置面板
    整篇覆盖。注入位置不变(文风圣经=system 前缀、画风圣经=illustrator 易变区),故缓存铁律不破——
    每故事的前缀本就互相独立;故事内改圣经只会让该故事「后续轮」重建前缀,与改知识库同理。
"""

from pathlib import Path

from app.agents.loader import load_prompt

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_STYLE_DIR = _PROMPTS_DIR / "style_bible"
_VISUAL_DIR = _PROMPTS_DIR / "visual_style_bible"

# 文风圣经(叙事):所有叙事 agent 共用的 system 前缀的默认内容。
DEFAULT_STYLE_BIBLE = load_prompt("style_bible/default.md")
# 画风圣经(绘图):illustrator 易变区注入的默认内容。
DEFAULT_VISUAL_STYLE_BIBLE = load_prompt("visual_style_bible/default.md")


def _scan_templates(folder: Path) -> list[dict[str, str]]:
    """扫描某文件夹下全部 *.md → [{name, content}]。name=文件名去扩展,"default" 恒首位,
    其余按名字典序。读不到的文件优雅跳过(不让一个坏模板拖垮启动)。"""
    items: list[dict[str, str]] = []
    if not folder.is_dir():
        return items
    for p in sorted(folder.glob("*.md")):
        try:
            items.append({"name": p.stem, "content": p.read_text(encoding="utf-8").strip()})
        except OSError:
            continue
    items.sort(key=lambda it: (it["name"] != "default", it["name"]))
    return items


# 启动时扫描一次(「每次启动自动扫描」):进程内常驻,不随请求重扫。
STYLE_TEMPLATES: list[dict[str, str]] = _scan_templates(_STYLE_DIR)
VISUAL_TEMPLATES: list[dict[str, str]] = _scan_templates(_VISUAL_DIR)


def resolve_style_bible(custom: str | None) -> str:
    """该故事生效的文风圣经:自定义非空 → 用自定义;否则回退默认。"""
    return (custom or "").strip() or DEFAULT_STYLE_BIBLE


def resolve_visual_style_bible(custom: str | None) -> str:
    """该故事生效的画风圣经:自定义非空 → 用自定义;否则回退默认。"""
    return (custom or "").strip() or DEFAULT_VISUAL_STYLE_BIBLE
