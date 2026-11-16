# -*- coding: utf-8 -*-
"""收藏类型注册表 —— 类型判断的单一真源。

为什么要有它：V0.2.0 之前类型判断散在两处且结论不同 ——
`core/fav_export.py::kind_of()`（决定实际导出什么）与
`export_list.py::DEFAULT_INCLUDED_TYPES`（决定统计里说"纳入了什么"）。
结果是：同一个条目走 GUI 与走 CLI 可能被判成不同类型，报告里说的范围
和真实导出的范围是两回事。

本模块改用「能力」而不是「白名单」描述类型：

    payload        本机是否存在可定位的本体文件
    textual        正文是否写在收藏 XML 里（可直接取，不需要解密）
    container      是否需要展开 datalist 逐子项处理
    online         只有在线内容，本机不可能有本体
    default_media  是否纳入默认的文件导出范围
    default_textual 是否纳入默认的文本清单
    note           给人看的一句话说明（为什么拿不到）

未登记的新类型不会被静默丢弃：`classify()` 返回 kind="other" 的占位，
报告里单独列出。V0.2.0 的 `other` 是"漏掉了"，这里的 `other` 是"我知道它
是未知类型，且明说了"。

⚠ `kind` 是历史词，已定保留不改：
  type=14 的内部 kind 仍是 "link"。它表示**聊天记录打包**，不是公众号文章链接
  （后者是 type=5 / kind="article"）。展示名走 `name`，与内部字符串解耦，
  这样既不改坏既有脚本与 `kinds` 参数取值，也不会再让用户看到"108 条链接"的错标。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Kind:
    type: int
    kind: str
    name: str
    payload: bool = False
    textual: bool = False
    container: bool = False
    online: bool = False
    default_media: bool = False
    default_textual: bool = False
    note: str = ""


# 类型依据见 ROADMAP.md §1.1（公开文档不收录本机收藏规模）
# 刻意不登记 type=19：主表未见，只在聊天记录子项里偶尔出现，语义未确认。
# 留给 classify() 的未知分支 —— 这样一旦真出现，报告里会写「未知类型 19」而不是静默并入「其他」。
_TABLE = (
    Kind(1, "text", "文本",
         textual=True, default_textual=True),
    Kind(2, "image", "图片",
         payload=True, default_media=True),
    Kind(3, "voice", "语音",
         default_media=True,
         note="silk 本体通常不在 PC；但条目本身仍登记进清单，"
              "能在本机找到音频的（被当文件发过的）直接导出"),
    Kind(4, "video", "视频",
         payload=True, default_media=True),
    Kind(5, "article", "链接",
         textual=True, default_textual=True),
    Kind(8, "voice", "语音",
         default_media=True,
         note="m4a 语音；这一类常是「当文件发的音频」，本机有文件时可按 md5 认领并导出"),
    Kind(14, "link", "聊天记录",
         payload=True, textual=True, container=True, default_textual=True,
         note="内部 kind 沿用历史词 link；datalist 内含多条子项，需展开逐条导出"),
    Kind(16, "video", "视频",
         payload=True, default_media=True),
    Kind(20, "finder", "视频号",
         online=True,
         note="在线内容：本机只有 finderFeed 元数据与封面 URL"),
)

REGISTRY = {k.type: k for k in _TABLE}

# kind 字符串 -> 展示名。同 kind 多条时取第一条（type=3/8 都是 voice）。
NAME_BY_KIND = {}
for _k in _TABLE:
    NAME_BY_KIND.setdefault(_k.kind, _k.name)
NAME_BY_KIND.setdefault("chat", "聊天")
NAME_BY_KIND.setdefault("other", "其他")


def _as_int(typ):
    try:
        return int(typ)
    except (TypeError, ValueError):
        return None


def get(typ):
    """按收藏 type 取 Kind；未登记返回 None。"""
    t = _as_int(typ)
    return REGISTRY.get(t) if t is not None else None


def is_known(typ):
    return get(typ) is not None


def classify(typ, content=None):
    """type -> Kind。未登记的类型返回占位而不是 KeyError。

    content 参数只为兼容旧的 kind_of(typ, content) 签名，本模块不再看正文：
    V0.2.0 里靠 `<datatype>`/`<datafmt>` 猜类型的做法会误判 ——
    15 条 type=4 视频因为没有 datafmt 掉进 other，9 条 type=14 聊天记录
    因为第一个子项是 mp4 被标成 video。
    """
    k = get(typ)
    if k is not None:
        return k
    t = _as_int(typ)
    return Kind(t if t is not None else -1, "other", f"未知类型 {typ}",
                note="本版未登记该类型，只导出元数据并在报告里单独列出")


def kind_of(typ, content=None):
    """兼容旧接口：只要内部 kind 字符串。"""
    return classify(typ, content).kind


def name_of(typ, default="其他"):
    """按 type 取中文展示名。"""
    k = get(typ)
    return k.name if k else default


def name_by_kind(kind, default="其他"):
    """按内部 kind 字符串取中文展示名。"""
    return NAME_BY_KIND.get(kind or "", default)


def default_media_kinds():
    """默认走 resolve_item 的 kind 集合（有序，供界面展示）。"""
    out = []
    for k in _TABLE:
        if k.default_media and k.kind not in out:
            out.append(k.kind)
    return tuple(out)


def default_included_types():
    """默认会被处理（文件或清单）的收藏 type。"""
    return {k.type for k in _TABLE if k.default_media or k.default_textual}


def media_types():
    """有本体的类型（payload，不含容器）。语音不算 —— PC 上没有 silk。"""
    return {k.type for k in _TABLE if k.payload and not k.container}


def default_media_types():
    """默认导出时会被 visit 的类型，含「登记进清单但不一定有本体」的语音。"""
    return {k.type for k in _TABLE if k.default_media}


def textual_types():
    return {k.type for k in _TABLE if k.textual}


def container_types():
    return {k.type for k in _TABLE if k.container}


def unavailable(typ):
    """该类型是否本机不可能拿到本体（语音 / 视频号 / 未知）。"""
    k = get(typ)
    if k is None:
        return True
    return not k.payload


def note_for_name(name):
    """按中文展示名取说明（同名的取第一条非空 note）。"""
    for k in _TABLE:
        if k.name == name and k.note:
            return k.note
    return ""


def note_of(typ):
    k = get(typ)
    return k.note if k else ""


__all__ = [
    "Kind", "REGISTRY", "NAME_BY_KIND", "get", "is_known", "classify",
    "kind_of", "name_of", "name_by_kind", "default_media_kinds",
    "default_included_types", "media_types", "textual_types",
    "container_types", "unavailable", "note_for_name", "note_of",
]
