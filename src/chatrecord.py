# -*- coding: utf-8 -*-
"""聊天记录打包（type=14）展开 —— 把一条收藏拆成多条子项。

V0.2.0 对 type=14 的处理是错的两种：
  · 多数被判成 `link`（聊天记录被当成"链接"），默认范围外，静默丢弃；
  · 少数因为第一个子项是 mp4 被判成 `video`，**导出了一个 .mp4 就当成整条聊天记录**。
两条都不对。一条聊天记录里可以有几十上百条子项，本模块把它拆开：

    文本子项  -> 正文直接取 <datadesc>，不碰文件、不需要密钥
    媒体子项  -> 交给调用方按 size/md5 去本地容器里认领（复用 fav_export.resolve_item）

子项口径：
    datatype=1  文本   正文就在 XML 里，全部可取
    datatype=2  图片   有原件或缩略图则可落地
    datatype=4  视频   有原件或缩略图则可落地
    datatype=3  语音   本体通常不在 PC（与顶层 type=3 同因）
    datatype=8  语音   本机有文件则可落地
    datatype=15 视频   有文件则可落地
    其余（链接 / 位置 / 未知）按类型写入清单或呈现

子项里的 `datatype` 与顶层 `favitem.type` 大部分同义但不完全等价：
顶层没有 type=15，而子项里 datatype=15 是 mp4 视频（对应顶层 type=16）。
所以这里用 SUB_NAME 覆盖不一致的两个，其余借 type_registry。
"""
import html
import re

import type_registry as tr

# datatype -> 展示名。只列与 type_registry 不一致或注册表里没有的。
SUB_NAME = {
    6: "位置",
    15: "视频",
    19: "未知",
}


def as_text(c):
    if c is None:
        return ""
    if isinstance(c, bytes):
        return c.decode("utf-8", "replace")
    return c


def _get(pat, txt, default=""):
    m = re.search(pat, txt or "", re.S)
    return html.unescape(m.group(1).strip()) if m else default


def _int(s):
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return 0


def sub_name(datatype):
    try:
        dt = int(datatype)
    except (TypeError, ValueError):
        return "未知"
    if dt in SUB_NAME:
        return SUB_NAME[dt]
    return tr.classify(dt).name


def sub_kind(datatype):
    """子项内部的 kind 字符串，尽量与顶层语义对齐。"""
    try:
        dt = int(datatype)
    except (TypeError, ValueError):
        return "other"
    if dt == 15:
        return "video"
    return tr.classify(dt).kind


def title_of(content):
    """聊天记录包的标题。26/117 有 <title>，其余只能用条数概括。"""
    return _get(r"<title>(.*?)</title>", as_text(content))


def expand(content):
    """把一条 type=14 的 content 拆成结构化子项。

    返回 {
        "title": 包标题（可能为空）,
        "count": datalist 声明的条数,
        "items": [ {datatype, name, kind, when, sender, text, size, md5,
                    thumb_size, md5t, fmt, duration, lat, lng, place, poi}, ... ]
    }
    文本子项 text 非空；媒体子项 size/md5 供调用方去本地认领，text 为空。
    语音带 duration（dt8 的 10 条没有），位置带 lat/lng/place/poi（渲染地图链接用）。
    """
    s = as_text(content)
    items = []
    for m in re.finditer(r"<dataitem\b([^>]*)>(.*?)</dataitem>", s, re.S):
        attrs, body = m.group(1), m.group(2)
        dt = _get(r'datatype="(\d+)"', attrs) or _get(r"datatype='(\d+)'", attrs)
        dt = _int(dt)
        # 时长量纲不统一：语音(3) 是毫秒，视频(4/15) 是秒。
        # 统一成秒再往外给，否则 HTML 里会出现「4019″」这种离谱时长。
        dur_raw = _int(_get(r"<duration>(\d+)</duration>", body))
        dur_s = int(round(dur_raw / 1000.0)) if dt == 3 and dur_raw else dur_raw
        items.append({
            "datatype": dt,
            "name": sub_name(dt),
            "kind": sub_kind(dt),
            "when": _get(r"<datasrctime>(.*?)</datasrctime>", body),
            "sender": _get(r"<datasrcname>(.*?)</datasrcname>", body),
            "fromusr": _get(r"<fromusr>(.*?)</fromusr>", body),
            "text": _get(r"<datadesc>(.*?)</datadesc>", body),
            "title": _get(r"<datatitle>(.*?)</datatitle>", body),
            # 包内的链接卡片（datatype=5）把地址放在 <link> 里，页面要用它做可点链接
            "url": _get(r"<link>(.*?)</link>", body),
            "size": _int(_get(r"<fullsize>(\d+)</fullsize>", body)),
            "md5": _get(r"<fullmd5>([0-9a-fA-F]{32})</fullmd5>", body).lower(),
            "thumb_size": _int(_get(r"<thumbfullsize>(\d+)</thumbfullsize>", body)),
            "md5t": _get(r"<thumbfullmd5>([0-9a-fA-F]{32})</thumbfullmd5>", body).lower(),
            "fmt": _get(r"<datafmt>([^<]*)</datafmt>", body),
            "duration": dur_s,
            "duration_raw": dur_raw,
            "lat": _get(r"<lat>(-?[\d.]+)</lat>", body),
            "lng": _get(r"<lng>(-?[\d.]+)</lng>", body),
            "place": _get(r"<label>(.*?)</label>", body),
            "poi": _get(r"<poiname>(.*?)</poiname>", body),
        })
    declared = _int(_get(r'<datalist[^>]*count="(\d+)"', s))
    return {"title": title_of(s), "count": declared or len(items), "items": items}


def is_media(item):
    """该子项是否值得去本地找文件。

    只认 image/video/file —— 语音（datatype 3/8）本机从来没有本体文件，
    送去 resolve 只会换回一条「未定位」，把报告撑大而不增加任何信息。
    语音走 is_meta_only，只记来源与时间。
    """
    if not item:
        return False
    if item.get("text"):
        return False
    if not (item.get("size") or item.get("md5") or item.get("thumb_size")):
        return False
    return item.get("kind") in ("image", "video", "file")


def is_audio(item):
    """语音子项（dt3 amr/silk、dt8 m4a）。

    PC 端默认不保留语音本体，但**被当文件发过的语音**（dt8 常见）可能就在
    msg/file 或 msg/attach 里，md5 能对上。所以给它们单独一条认领通路，
    而不是一上来就判「仅元数据」。
    """
    if not item or item.get("text"):
        return False
    return item.get("kind") == "voice" and bool(item.get("md5") or item.get("size"))


def is_meta_only(item):
    """有类型但拿不到文件、也不是正文的子项：语音 / 位置 / 未知。"""
    if not item or item.get("text") or is_media(item):
        return False
    return bool(item.get("name"))


__all__ = ["expand", "title_of", "sub_name", "sub_kind", "is_media",
           "is_audio", "is_meta_only", "SUB_NAME"]
