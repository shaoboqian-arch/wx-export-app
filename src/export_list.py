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


def write_missing_csv(path, failed_items):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(MISSING_HEADERS)
        for it in failed_items:
            enrich_failed_item(it)
            lid = it.get("local_id")
            if lid is None or lid == "":
                lid = it.get("lid") or ""
            w.writerow([
                it.get("when") or "",
                it.get("type_zh") or kind_zh(it.get("kind")),
                it.get("state") or "",
                it.get("reason") or "",
                it.get("suggestion") or "",
                it.get("display") or display_name(it),
                it.get("path") or "",
                it.get("desc") or "",
                it.get("fromusr") or it.get("source") or "",
                lid,
            ])
    return path


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


def write_manifest_csv(path, media_rows, text_items, link_items, chat_items=None):
    rows = build_manifest_rows(media_rows, text_items, link_items, chat_items)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(MANIFEST_HEADERS)
        w.writerows(rows)
    return path, len(rows)


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


def write_readme(path, *, frm, to, include_text_links, resolve_kinds,
                 counts, written, media_total, text_count, link_count,
                 census=None, chat=None):
    counts = counts or {}
    chat = chat or {}
    lines = [
        "微信收藏导出说明",
        f"版本：{VERSION}",
        "",
        f"日期范围：{frm} 至 {to}（起止均含当天）",
        f"本批导出内容：{kinds_phrase(include_text_links, resolve_kinds)}",
        "",
        "媒体文件（图片/视频及本次按类型导出的文件）：",
        f"  完整：{int(counts.get('完整', 0) or 0)}",
        f"  本地仅缩略图：{int(counts.get('本地仅缩略图', 0) or 0)}",
        f"  头部缺失：{int(counts.get('头部缺失', 0) or 0)}",
        f"  本机未缓存：{int(counts.get('未定位本体', 0) or 0)}"
        "（微信从没把原件下载到这台电脑，导出工具取不到，不是解析失败）",
        f"  写出文件：{int(written or 0)} / {int(media_total or 0)}",
        "",
    ]
    if int(counts.get("缓存还原", 0) or 0) > 0:
        lines += [
            f"其中 {int(counts.get('缓存还原', 0))} 件由微信聊天缓存还原"
            "（整文件单字节异或，报告「还原」列有标注）。",
            "  这类文件的 fullmd5 记的是缓存块的 md5、不是原图 md5，所以不能按 md5 校验；",
            "  内容本身是完整可看的原图，还原后按能否解码来验收。",
            "",
        ]
    if include_text_links:
        lines += [
            f"文本：{int(text_count or 0)} 条（全文写在「_导出清单.csv」，不另存单文件）",
            f"链接：{int(link_count or 0)} 条（标题、摘要和网址写在「_导出清单.csv」，不另存单文件）",
            "",
        ]
    if int(chat.get("records", 0) or 0) > 0:
        packs_ok = int(chat.get("packs_ok", 0) or 0)
        packs_bad = int(chat.get("packs_failed", 0) or 0)
        lines += [
            f"聊天记录：{int(chat.get('records', 0))} 条打包收藏，展开 "
            f"{int(chat.get('items', 0))} 个子项。",
            f"  HTML：{packs_ok} 个包已生成"
            + (f"，{packs_bad} 个包生成失败（见下）" if packs_bad else "，全部成功"),
            "  每个包一个目录：<发送人>/聊天记录/<序号>_<日期>/index.html，",
            "  附件与 index.html 放在同一目录，页面用相对路径引用，整个目录可直接拷走。",
            f"  正文 {int(chat.get('text', 0))} 条同时写在「_导出清单.csv」"
            "（「所属收藏」列是原收藏的 local_id，父行的「输出路径」指向该包的 index.html）。",
            f"  媒体子项 {int(chat.get('media', 0))} 个，落地 {int(chat.get('files', 0))} 个文件。",
        ]
        failed_packs = chat.get("failed_packs") or []
        if failed_packs:
            lines.append("  HTML 生成失败的包：")
            for p in failed_packs[:20]:
                lines.append(f"    · {p.get('title') or '（无标题）'}"
                             f"（local_id {p.get('local_id')}）：{p.get('reason') or '未知原因'}")
            if len(failed_packs) > 20:
                lines.append(f"    …… 另有 {len(failed_packs) - 20} 个，"
                             "完整清单见本次导出结果 JSON 的 chat.failed_packs。")
        if int(chat.get("语音可播", 0) or 0) > 0:
            lines.append(
                f"  语音 {int(chat.get('语音可播', 0))} 条在本机找到了音频（被当文件发过的那种），"
                "已随包导出，HTML 里点语音条可直接播放。")
        if int(chat.get("仅元数据", 0) or 0) > 0:
            lines.append(
                f"  另有 {int(chat.get('仅元数据', 0))} 个语音/位置类子项只记元数据："
                "微信 PC 端不保留 silk 语音本体，本机取不到；"
                "HTML 里语音按微信的样子画成语音条（点一下会说明为什么不能播），"
                "位置给地址和地图链接，不留空白。")
        lines.append("")
    lines += [
        "目录含义：",
        "  同一批次内按发送人分文件夹；没有发送人的放在「未知发送人」。",
        "  完整件与缩略图放在同一发送人文件夹里，缩略图的文件名带「_缩略」。",
        "  聊天记录的附件在「<发送人>/聊天记录/<local_id>_<日期>/」下，不与图片视频混放。",
        "",
        "要拿到图片原件，需要事先在微信中点开大图并加载完成；",
        "要拿到视频原件，需要事先在微信中真正播放过该视频。",
        "「本机未缓存」只统计图片/视频。语音和位置类条目本机从来没有本体，",
        "它们不计入这个数字，只在清单里留一条元数据记录。",
        "头部缺失的条目，请先补齐图片密钥。",
        "未取到原件的条目见「_未取到原件.csv」。按其中建议处理完成后，",
        "可在本程序中点「补导」，只补这些条目，不必重新选择日期和文件夹。",
        "聊天记录里的附件也可以补导（按报告行自己的体积与 md5 单条认领）：升格后原件写回同一个包目录，"
        "并自动重渲这个包的 index.html。",
        "",
        "本批条目见「_导出清单.csv」。核心对账报告仍是「_导出报告.csv」（仅含本次走文件导出的条目）。",
    ]
    shown = []
    if include_text_links and census:
        for label, n in (census or {}).items():
            try:
                n = int(n or 0)
            except (TypeError, ValueError):
                n = 0
            if n > 0:
                shown.append((label, n))
        if shown:
            lines += ["", "同期还有、本次未纳入的类型："]
            for label, n in shown:
                note = tr.note_for_name(label)
                lines.append(f"  {label}：{n}" + (f"（{note}）" if note else ""))
    lines.append("")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    return path


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
