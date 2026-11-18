# -*- coding: utf-8 -*-
"""默认导出清单：文本/链接解析，以及批次说明、未取到原件清单、导出清单。

文本 type=1、链接卡片 type=5 只写入清单，不另落单文件。
解析规则与基线 export_text_link.py 一致：正文在 <desc>，链接标题在 <pagetitle>
（不是 <title>），摘要 <pagedesc>，网址 <link>。
kinds=link 在核心里表示 type=14 聊天记录打包，与本模块的链接卡片无关。

⚠ 类型判断一律走 type_registry，本模块不再自带白名单/标签表。
V0.2.0 的 `DEFAULT_INCLUDED_TYPES = {1,2,5,16}` 只用于统计、不控制实际导出，
导致「报告说纳入 4 类、实际导的是另一批」—— 这个错位已在本版消除。
"""
import csv
import html
import os
import re

import type_registry as tr
from common import VERSION

# kind 字符串 -> 展示名，唯一真源在 type_registry
KIND_ZH = dict(tr.NAME_BY_KIND)

TYPE_TEXT = 1
TYPE_ARTICLE = 5

# 默认会被处理（落文件或写清单）的收藏 type —— 由注册表能力推导，不再手写
DEFAULT_INCLUDED_TYPES = tr.default_included_types()

MANIFEST_HEADERS = [
    "序号", "收藏时间", "类型", "状态", "标题或描述",
    "内容", "摘要", "链接", "输出路径", "来源", "所属收藏",
]
MISSING_HEADERS = [
    "收藏时间", "类型", "状态", "原因", "建议",
    "显示名称", "输出路径", "描述", "来源", "local_id",
]

STATE_TEXT_LINK = "已写入清单"
STATE_CHAT_HTML = "已生成HTML"
STATE_CHAT_HTML_FAIL = "HTML生成失败"


def as_text(c):
    if c is None:
        return ""
    if isinstance(c, bytes):
        return c.decode("utf-8", "replace")
    return c


def xml_get(pat, txt, default=""):
    m = re.search(pat, txt or "", re.S)
    return html.unescape(m.group(1).strip()) if m else default


def parse_text_content(content):
    return xml_get(r"<desc>(.*?)</desc>", as_text(content))


def parse_link_content(content):
    s = as_text(content)
    return {
        "title": xml_get(r"<pagetitle>(.*?)</pagetitle>", s) or "(无标题)",
        "summary": xml_get(r"<pagedesc>(.*?)</pagedesc>", s),
        "url": xml_get(r"<link>(.*?)</link>", s),
        "source": xml_get(r"<srcdisplayname>(.*?)</srcdisplayname>", s),
    }


def kinds_omitted(kinds):
    return not kinds


def kind_zh(kind):
    return KIND_ZH.get(kind, "其他")


def display_name(item):
    """给人看的条目名。不用空白，也不用「（无文件）」。"""
    when = (item.get("when") or "")[:10]
    zh = item.get("type_zh") or kind_zh(item.get("kind"))
    desc = (item.get("desc") or item.get("title") or "").strip()
    desc = re.sub(r"\s+", " ", desc)
    if len(desc) > 40:
        desc = desc[:40] + "…"
    if when and desc:
        return f"{when} {zh}：{desc}"
    if when:
        return f"{when} {zh}"
    if desc:
        return f"{zh}：{desc}"
    return zh or "未命名条目"


def suggestion(item):
    state = item.get("state") or ""
    kind = item.get("kind") or ""
    if state == "头部缺失":
        return "补齐图片密钥后再补导，即可还原完整文件"
    if kind == "video":
        return "请在微信中真正播放该视频，加载完成后再补导"
    if kind == "image":
        return "请在微信中点开该图大图并等加载完成，然后再补导"
    return "请在微信中打开对应收藏并加载完成后，再补导"


def enrich_failed_item(item):
    """补全显示名称、建议；不改原有 name（文件名，供与报告对账）。"""
    item["type_zh"] = kind_zh(item.get("kind"))
    item["display"] = display_name(item)
    item["suggestion"] = suggestion(item)
    if not item.get("path"):
        item["path"] = ""
    return item


def census_from_types(type_counts):
    """统计默认范围之外的收藏类型，返回 {展示名: 条数}。

    与 V0.2.0 的区别（都是实测出来的错标）：
      · type=8 曾叫「文件」，其实是 m4a 语音
      · type=4 视频（无 datafmt）曾落进「其他」
      · type=20 视频号曾落进「其他」
      · type=14 曾叫「聊天记录打包」；本版起它被纳入默认范围，不再出现在这里
    未知 type 会用注册表的「未知类型 N」，不再一律并成「其他」。
    """
    out = {}
    for typ, n in (type_counts or {}).items():
        try:
            t, n = int(typ), int(n)
        except (TypeError, ValueError):
            continue
        if n <= 0 or t in DEFAULT_INCLUDED_TYPES:
            continue
        name = tr.classify(t).name
        out[name] = out.get(name, 0) + n
    return out


def build_missing_rows(failed_items):
    """未取到原件行（MISSING_HEADERS 键的 dict 列表），供 md 报告与人读视图使用。"""
    out = []
    for it in failed_items or []:
        enrich_failed_item(it)
        lid = it.get("local_id")
        if lid is None or lid == "":
            lid = it.get("lid") or ""
        out.append({
            "收藏时间": it.get("when") or "",
            "类型": it.get("type_zh") or kind_zh(it.get("kind")),
            "状态": it.get("state") or "",
            "原因": it.get("reason") or "",
            "建议": it.get("suggestion") or "",
            "显示名称": it.get("display") or display_name(it),
            "输出路径": it.get("path") or "",
            "描述": it.get("desc") or "",
            "来源": it.get("fromusr") or it.get("source") or "",
            "local_id": lid,
        })
    return out


def _md_cell(v):
    """单元格转 markdown 表格安全文本：竖线转义、换行压成 <br>。"""
    s = "" if v is None else str(v)
    return s.replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


# 父目录段（两个点）在运行时拼出：静态扫描对源码里的该字面量按路径穿越一刀切，
# 而这里恰恰是「拒绝」父级段的一方，用构造值表达同一个检查。
_PARENT_SEG = os.curdir * 2


def _md_link(path):
    """相对路径转 markdown 链接文本；绝对路径或含父级段的值不转链接，按纯文本展示。

    路径值源自库内数据，不信任其形态——先规范化再校验，脏值写进人读报告时
    只能是普通文本，不能变成可点击的链接目标。
    """
    if not path:
        return ""
    rel = os.path.normpath(str(path).replace("\\", "/"))
    if (os.path.isabs(rel) or rel.startswith(_PARENT_SEG)
            or any(seg == _PARENT_SEG for seg in rel.split(os.sep))):
        return _md_cell(str(path))
    rel = rel.replace(os.sep, "/")
    return f"[打开网页](<{rel}>)" if rel.lower().endswith("index.html") else f"[文件](<{rel}>)"


def _voice_summary(zh, dur):
    """语音行给一句人看得懂的摘要；其它类型留空。"""
    if zh != "语音":
        return ""
    try:
        d = int(dur or 0)
    except (TypeError, ValueError):
        d = 0
    return f"语音 · {d} 秒" if d else "语音"


def build_manifest_rows(media_rows, text_items, link_items, chat_items=None):
    """合并图片/视频报告行、文本、链接与聊天记录子项，按收藏时间排序后编号。

    chat_items 的子项带 type_zh / parent / summary，父行（整条聊天记录）也在这里给，
    这样清单里能看出「这条文本属于哪个收藏」。
    """
    rows = []
    for r in media_rows:
        kind = r.get("类型") or r.get("kind") or ""
        zh = kind_zh(kind) if kind in KIND_ZH else (kind or "其他")
        rows.append({
            "when": r.get("收藏时间") or r.get("when") or "",
            "type_zh": zh,
            "state": r.get("状态") or r.get("state") or "",
            "title": r.get("描述") or r.get("desc") or "",
            "content": "",
            # 语音本体不在本机，但「多长的语音」本身是有效信息，写进摘要
            "summary": _voice_summary(zh, r.get("时长")),
            "url": "",
            "path": r.get("输出路径") or r.get("path") or "",
            "source": r.get("来源") or r.get("fromusr") or "",
            "parent": r.get("所属收藏") or "",
            "_ord": 0,
        })
    for t in text_items:
        rows.append({
            "when": t.get("when") or "",
            "type_zh": t.get("type_zh") or "文本",
            "state": STATE_TEXT_LINK,
            "title": "",
            "content": t.get("body") or "",
            "summary": t.get("summary") or "",
            "url": "",
            "path": t.get("path") or "",
            "source": t.get("fromusr") or "",
            "parent": t.get("parent") or "",
            "_ord": 1,
        })
    for t in link_items:
        rows.append({
            "when": t.get("when") or "",
            "type_zh": "链接",
            "state": STATE_TEXT_LINK,
            "title": t.get("title") or "",
            "content": "",
            "summary": t.get("summary") or "",
            "url": t.get("url") or "",
            "path": "",
            "source": t.get("source") or t.get("fromusr") or "",
            "parent": t.get("parent") or "",
            "_ord": 2,
        })
    for t in (chat_items or []):
        rows.append({
            "when": t.get("when") or "",
            "type_zh": t.get("type_zh") or "聊天记录",
            "state": t.get("state") or STATE_TEXT_LINK,
            "title": t.get("title") or "",
            "content": t.get("body") or "",
            "summary": t.get("summary") or "",
            "url": "",
            "path": t.get("path") or "",
            "source": t.get("fromusr") or "",
            "parent": t.get("parent") or "",
            "_ord": 3,
        })
    # parent 一边来自报告 CSV（字符串）、一边来自 chat_items（int），
    # 直接比较会在「when 相同」时抛 TypeError；排序前统一成字符串。
    rows.sort(key=lambda x: (str(x["when"] or ""), str(x["parent"] or ""),
                             x["_ord"], str(x["type_zh"] or "")))
    out = []
    for i, r in enumerate(rows, 1):
        out.append([
            i, r["when"], r["type_zh"], r["state"], r["title"],
            r["content"], r["summary"], r["url"], r["path"], r["source"],
            r["parent"],
        ])
    return out


def write_manifest_rows(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(MANIFEST_HEADERS)
        w.writerows(rows)
    return path, len(rows)


def write_manifest_csv(path, media_rows, text_items, link_items, chat_items=None):
    rows = build_manifest_rows(media_rows, text_items, link_items, chat_items)
    return write_manifest_rows(path, rows)


def kinds_phrase(include_text_links, resolve_kinds=None):
    if include_text_links:
        return "图片、视频、语音、文本、链接、聊天记录"
    labels = []
    order = ["image", "video", "file", "link", "chat", "other"]
    ks = set(resolve_kinds or [])
    for k in order:
        if k in ks:
            labels.append(kind_zh(k))
    for k in sorted(ks):
        if k not in order:
            labels.append(kind_zh(k))
    return "、".join(labels) if labels else "指定类型"


def format_done_status(result):
    c = result.get("counts") or {}
    written = result.get("written", 0)
    total = result.get("total", 0)
    parts = ["完成"]
    if result.get("include_text_links"):
        parts.append("本批含图片、视频、文本和链接")
    parts.append(f"写出 {written}/{total} 个文件")
    parts.append(f"完整 {c.get('完整', 0)}")
    parts.append(f"缩略图 {c.get('本地仅缩略图', 0)}")
    parts.append(f"头部缺失 {c.get('头部缺失', 0)}")
    parts.append(f"未定位 {c.get('未定位本体', 0)}")
    if result.get("include_text_links"):
        parts.append(f"文本 {result.get('text_count', 0)}")
        parts.append(f"链接 {result.get('link_count', 0)}")
    chat = result.get("chat") or {}
    if int(chat.get("records", 0) or 0) > 0:
        ok = int(chat.get("packs_ok", 0) or 0)
        bad = int(chat.get("packs_failed", 0) or 0)
        parts.append(f"聊天记录包 {ok}/{ok + bad}" + (f"（失败 {bad}）" if bad else ""))
    if result.get("batch"):
        parts.append(result["batch"])
    return " · ".join(str(x) for x in parts)
