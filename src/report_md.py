# -*- coding: utf-8 -*-
"""批次内唯一的人读报告《导出报告.md》：概览 + 未取到原件 + 按发送人分组的导出明细 + 说明。

告别难读的 11~13 列宽表格：
  · 顶部「概览」仅保留 4 列窄计数表（只列非 0 类别）与媒体状态摘要
  · 「未取到原件」改为结构化清单（显示名称 / 发送人 / 状态原因 / 建议）
  · 「导出明细」按发送人目录名分组，用清晰列表展示类型、状态、体积/时长、可点击相对链接、文本引用块与聊天记录子项
"""
import os
import re
from pathlib import Path

import type_registry as tr
from common import VERSION
from export_list import (
    MANIFEST_HEADERS, STATE_CHAT_HTML, build_missing_rows, kind_zh, kinds_phrase,
)

REPORT_MD_NAME = "导出报告.md"
_PARENT_SEG = os.curdir * 2
_KIND_ORDER = ("图片", "视频", "语音", "文件", "其他", "文本", "链接", "聊天记录")
_TOP_CAT_ORDER = ("图片", "视频", "语音", "文本", "链接", "聊天记录", "文件", "其他")


def report_md_path(batch_dir):
    """《导出报告.md》在批次目录内的落点：规范化 + 必须落在批次目录内。"""
    base = os.path.normpath(os.path.abspath(batch_dir))
    dest = os.path.normpath(os.path.abspath(os.path.join(base, REPORT_MD_NAME)))
    if not dest.startswith(base + os.sep):
        raise ValueError(f"报告落点越出批次目录：{dest}")
    return dest


def _inline(v):
    """压成单行安全 Markdown 文本（不含原始换行与竖线）。"""
    s = "" if v is None else str(v)
    return re.sub(r"\s+", " ", s).strip().replace("|", "\\|")


def _human_size(val):
    try:
        n = int(float(str(val).strip()))
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 10:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.2f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def _human_dur(val):
    try:
        s = int(round(float(str(val).strip())))
    except (TypeError, ValueError):
        return ""
    if s <= 0:
        return ""
    if s < 60:
        return f"{s} 秒"
    m, sec = divmod(s, 60)
    if sec == 0:
        return f"{m} 分钟"
    return f"{m} 分 {sec:02d} 秒"


def _short_when(when):
    s = ("" if when is None else str(when)).strip()
    if len(s) >= 16 and s[4:5] == "-" and s[10:11] in (" ", "T"):
        return s[:10] + " " + s[11:16]
    return s


def _rel_path(path, base_dir):
    """把路径规范化为批次内相对路径；若越界或不可相对化返回空串（不泄露绝对路径）。"""
    raw = ("" if path is None else str(path)).strip()
    if not raw:
        return ""
    norm = os.path.normpath(raw.replace("\\", "/"))
    if os.path.isabs(norm):
        if not base_dir:
            return ""
        base_abs = os.path.abspath(base_dir)
        target_abs = os.path.abspath(norm)
        try:
            if os.path.commonpath([base_abs, target_abs]) != base_abs:
                return ""
            rel = os.path.relpath(target_abs, base_abs).replace("\\", "/")
        except ValueError:
            return ""
    else:
        rel = norm.replace("\\", "/")
    if rel in ("", ".") or rel.startswith(_PARENT_SEG) or any(
        seg == _PARENT_SEG for seg in rel.split("/")
    ):
        return ""
    return rel


def _inline_link(label, rel):
    safe_label = _inline(label).replace("[", "\\[").replace("]", "\\]") or "文件"
    if not rel:
        return safe_label
    return f"[{safe_label}](<{rel}>)"


def _safe_url_link(title, url):
    label = _inline(title or url or "链接").replace("[", "\\[").replace("]", "\\]")
    u = (url or "").strip()
    if re.match(r"^https?://\S+$", u, re.I):
        return f"[{label}](<{u}>)"
    return label


def _norm_manifest_item(row):
    if isinstance(row, dict):
        return {k: ("" if row.get(k) is None else str(row.get(k))) for k in MANIFEST_HEADERS}
    return {
        k: ("" if idx >= len(row) or row[idx] is None else str(row[idx]))
        for idx, k in enumerate(MANIFEST_HEADERS)
    }


def _manifest_from_report(report_rows):
    out = []
    for i, r in enumerate(report_rows or [], 1):
        k = r.get("类型") or r.get("kind") or ""
        zh = kind_zh(k) if k in tr.NAME_BY_KIND else (k or "其他")
        out.append({
            "序号": str(r.get("序号") or i),
            "收藏时间": str(r.get("收藏时间") or ""),
            "类型": zh,
            "状态": str(r.get("状态") or ""),
            "标题或描述": str(r.get("描述") or ""),
            "内容": "",
            "摘要": f"语音 · {r.get('时长')} 秒" if zh == "语音" and r.get("时长") else "",
            "链接": "",
            "输出路径": str(r.get("输出路径") or ""),
            "来源": str(r.get("来源") or ""),
            "所属收藏": str(r.get("所属收藏") or ""),
        })
    return out


def _index_report_rows(report_rows, base_dir):
    by_rel = {}
    by_sig = {}
    for r in report_rows or []:
        rel = _rel_path(r.get("输出路径") or "", base_dir)
        if rel:
            by_rel[rel] = r
        k = r.get("类型") or r.get("kind") or ""
        zh = kind_zh(k) if k in tr.NAME_BY_KIND else (k or "其他")
        sig = (
            str(r.get("收藏时间") or ""),
            zh,
            str(r.get("状态") or ""),
            str(r.get("描述") or ""),
            str(r.get("来源") or ""),
            str(r.get("所属收藏") or ""),
        )
        by_sig.setdefault(sig, []).append(r)
    return by_rel, by_sig


def _sender_of(rel_path, source, sender_names):
    if rel_path and "/" in rel_path:
        head = rel_path.split("/", 1)[0].strip()
        if head:
            return head
    src = (source or "").strip()
    if src:
        mapped = (sender_names or {}).get(src)
        if mapped:
            return mapped
        return src
    return "未知发送人"


def _is_chat_parent(item):
    if (item.get("类型") or "") != "聊天记录":
        return False
    if (item.get("状态") or "") in (STATE_CHAT_HTML, "HTML生成失败"):
        return True
    rel = (item.get("rel_path") or "").lower()
    return rel.endswith("index.html")


def _meta_parts(item):
    parts = []
    sz = _human_size(item.get("体积"))
    dur = _human_dur(item.get("时长"))
    if sz:
        parts.append(sz)
    if dur:
        parts.append(dur)
    if (item.get("还原") or "").strip() == "缓存还原":
        parts.append("缓存还原")
    return parts


def _render_overview_table(enriched_top):
    counts = {}
    for it in enriched_top:
        t = it.get("类型") or "其他"
        c = counts.setdefault(t, {"total": 0, "done": 0, "missing": 0})
        c["total"] += 1
        st = it.get("状态") or ""
        if st in ("本地仅缩略图", "头部缺失", "未定位本体", "HTML生成失败"):
            c["missing"] += 1
        else:
            c["done"] += 1
    rows = []
    for k in _KIND_ORDER:
        if k in counts and counts[k]["total"] > 0:
            c = counts[k]
            rows.append(f"| {k} | {c['total']} | {c['done']} | {c['missing']} |")
    for k in sorted(counts):
        if k not in _KIND_ORDER and counts[k]["total"] > 0:
            c = counts[k]
            rows.append(f"| {k} | {c['total']} | {c['done']} | {c['missing']} |")
    if not rows:
        return []
    return [
        "| 类别 | 总数 | 已完成 | 未取到原件 |",
        "|---|---:|---:|---:|",
        *rows,
        "",
    ]


def _render_entry_lines(idx, item, child_map):
    seq = f"`{idx:03d}`"
    when = _short_when(item.get("收藏时间"))
    t = item.get("类型") or "其他"
    st = item.get("状态") or ""
    rel = item.get("rel_path") or ""
    raw_title = (item.get("标题或描述") or "").strip()
    meta = _meta_parts(item)
    meta_txt = f"（{' · '.join(meta)}）" if meta else ""

    if t == "文本":
        body = (item.get("内容") or "").strip()
        summary = _inline(item.get("摘要") or "")
        head = f"- {seq} · {when} · **文本**"
        if summary:
            head += f" · {summary}"
        lines = [head]
        if body:
            for ln in body.splitlines():
                lines.append(f"  > {ln}" if ln.strip() else "  >")
        else:
            lines.append("  > （空文本）")
        return lines

    if t == "链接":
        title = raw_title or "(无标题)"
        link_txt = _safe_url_link(title, item.get("链接") or "")
        lines = [f"- {seq} · {when} · **链接** · {link_txt}"]
        summary = _inline(item.get("摘要") or "")
        if summary:
            lines.append(f"  - 摘要：{summary}")
        return lines

    if _is_chat_parent(item):
        summary = _inline(item.get("摘要") or "")
        title = _inline(raw_title) or "聊天记录"
        head = f"- {seq} · {when} · **聊天记录** · {st}"
        if summary:
            head += f"（{summary}）"
        head += f" · {title}"
        if rel:
            head += f" · {_inline_link('打开网页', rel)}"
        lines = [head]
        children = child_map.get(str(item.get("所属收藏") or "").strip()) or []
        if children:
            # 未完整的媒体子项优先提示，方便补导定位
            bad_kids = [k for k in children if (k.get("状态") or "") in (
                "本地仅缩略图", "头部缺失", "未定位本体"
            )]
            if bad_kids:
                lines.append(f"  - 待补附件：{len(bad_kids)} 项（点开大图或播放视频后可点「补导」）")
            show_n = len(children) if len(children) <= 8 else 5
            for kid in children[:show_n]:
                kt = kid.get("类型") or ""
                ks = kid.get("状态") or ""
                krel = kid.get("rel_path") or ""
                kmeta = _meta_parts(kid)
                kmeta_txt = f"（{' · '.join(kmeta)}）" if kmeta else ""
                if (kid.get("内容") or "").strip():
                    preview = _inline(kid["内容"])[:40]
                    kwho = _inline(kid.get("摘要") or "")
                    lines.append(f"  - [{kt}] {kwho}：{preview}" if kwho else f"  - [{kt}] {preview}")
                else:
                    label = _inline(kid.get("标题或描述") or "") or os.path.basename(krel) or kt
                    target = _inline_link(label, krel) if krel else label
                    lines.append(f"  - [{kt}] {ks}{kmeta_txt} · {target}")
            if len(children) > show_n:
                lines.append(
                    f"  - …… 共 {len(children)} 条子项，完整图文与语音请点上方「打开网页」查看"
                )
        return lines

    if t == "语音":
        dur_txt = _human_dur(item.get("时长")) or _inline(item.get("摘要") or "") or "语音"
        line = f"- {seq} · {when} · **语音** · {st}（{dur_txt}）"
        if rel:
            line += f" · {_inline_link(os.path.basename(rel), rel)}"
        return [line]

    # 图片 / 视频 / 文件 / 其他
    desc = _inline(raw_title)
    fname = os.path.basename(rel) if rel else ""
    target = _inline_link(desc or fname or "查看文件", rel) if rel else (desc or "（无本地文件）")
    line = f"- {seq} · {when} · **{t}** · {st}{meta_txt} · {target}"
    return [line]


def write_report(batch_dir, *, frm, to, include_text_links, resolve_kinds,
                 counts, written, media_total, text_count, link_count,
                 census=None, chat=None, failed_items=None,
                 report_rows=None, manifest_rows=None, pending_lids=None,
                 sender_names=None):
    """在批次目录内生成唯一人读报告《导出报告.md》，返回写入路径。"""
    counts = counts or {}
    chat = chat or {}
    report_rows = report_rows or []
    dest = report_md_path(batch_dir)
    base_dir = os.path.dirname(dest)

    # 统一构造并富化条目（合并导出清单与对账元数据）
    raw_manifest = [_norm_manifest_item(r) for r in (manifest_rows or [])]
    if not raw_manifest and report_rows:
        raw_manifest = _manifest_from_report(report_rows)

    by_rel, by_sig = _index_report_rows(report_rows, base_dir)
    enriched = []
    for item in raw_manifest:
        rel = _rel_path(item.get("输出路径") or "", base_dir)
        rep = by_rel.get(rel) if rel else None
        if rep is None:
            sig = (
                item.get("收藏时间") or "",
                item.get("类型") or "",
                item.get("状态") or "",
                item.get("标题或描述") or "",
                item.get("来源") or "",
                item.get("所属收藏") or "",
            )
            bucket = by_sig.get(sig)
            if bucket:
                rep = bucket.pop(0)
        enriched.append({
            **item,
            "rel_path": rel,
            "体积": (rep or {}).get("体积") or "",
            "时长": (rep or {}).get("时长") or "",
            "还原": (rep or {}).get("还原") or "",
            "local_id": (rep or {}).get("local_id") or "",
            "sender": _sender_of(rel, item.get("来源") or "", sender_names),
        })

    # 把有父包的聊天记录子项挂到对应父包下；顶层条目按发送人分组展示
    parent_lids = {
        str(it.get("所属收藏") or "").strip()
        for it in enriched
        if _is_chat_parent(it) and str(it.get("所属收藏") or "").strip()
    }
    child_map = {}
    top_items = []
    for it in enriched:
        pid = str(it.get("所属收藏") or "").strip()
        if pid and pid in parent_lids and not _is_chat_parent(it):
            child_map.setdefault(pid, []).append(it)
        else:
            top_items.append(it)

    # 若主聊天包本身没有独立父行但有子项或孤立子项，也兜底保留在 top_items 中
    sender_order = []
    by_sender = {}
    for it in top_items:
        s = it["sender"]
        if s not in by_sender:
            by_sender[s] = []
            sender_order.append(s)
        by_sender[s].append(it)
    if "未知发送人" in by_sender:
        sender_order = [s for s in sender_order if s != "未知发送人"] + ["未知发送人"]

    out = [
        "# 微信收藏导出报告",
        "",
        f"- 版本：V{VERSION} · 日期范围：{frm} 至 {to}（起止均含当天）",
        f"- 本批导出内容：{kinds_phrase(include_text_links, resolve_kinds)}"
        f" · 写出文件：{int(written or 0)} / {int(media_total or 0)}",
        "",
        "## 概览",
        "",
    ]
    out += _render_overview_table(top_items)

    n_full = int(counts.get("完整", 0) or 0)
    n_thumb = int(counts.get("本地仅缩略图", 0) or 0)
    n_head = int(counts.get("头部缺失", 0) or 0)
    n_miss = int(counts.get("未定位本体", 0) or 0)
    out.append(
        f"- 媒体状态：完整：{n_full} · 本地仅缩略图：{n_thumb} · "
        f"头部缺失：{n_head} · 本机未缓存：{n_miss}"
    )
    if int(counts.get("缓存还原", 0) or 0) > 0:
        out.append(
            f"- 缓存还原：{int(counts.get('缓存还原', 0))} 件"
            "（由微信聊天缓存整文件异或还原，内容为完整原图）"
        )
    if include_text_links:
        out.append(f"- 文本：{int(text_count or 0)} 条 · 链接：{int(link_count or 0)} 条")
    if int(chat.get("records", 0) or 0) > 0:
        packs_ok = int(chat.get("packs_ok", 0) or 0)
        packs_bad = int(chat.get("packs_failed", 0) or 0)
        chat_parts = [
            f"{int(chat.get('records', 0))} 个打包（共 {int(chat.get('items', 0))} 子项）",
            f"{packs_ok} 个包已生成" + (f"、{packs_bad} 个包生成失败" if packs_bad else ""),
            f"正文 {int(chat.get('text', 0))} 条",
            f"媒体落地 {int(chat.get('files', 0))}/{int(chat.get('media', 0))} 件",
        ]
        if int(chat.get("语音可播", 0) or 0) > 0:
            chat_parts.append(f"可播语音 {int(chat.get('语音可播', 0))} 条")
        if int(chat.get("仅元数据", 0) or 0) > 0:
            chat_parts.append(f"仅元数据 {int(chat.get('仅元数据', 0))} 条")
        out.append("- 聊天记录：" + " · ".join(chat_parts))

    lids = [str(x) for x in (pending_lids or [])]
    out.append(
        f"- 待补条目：{len(lids)}"
        + (f"（local_id：{'、'.join(lids[:20])}"
           + ("……" if len(lids) > 20 else "") + "）" if lids else "")
    )
    out.append("")

    failed_packs = chat.get("failed_packs") or []
    if failed_packs:
        out += ["### HTML 生成失败的包", ""]
        for p in failed_packs[:20]:
            out.append(
                f"- {_inline(p.get('title') or '（无标题）')}"
                f"（`local_id: {p.get('local_id')}`）：{_inline(p.get('reason') or '未知原因')}"
            )
        if len(failed_packs) > 20:
            out.append(f"- …… 另有 {len(failed_packs) - 20} 个失败包")
        out.append("")

    if include_text_links and census:
        shown = []
        for label, n in census.items():
            try:
                nv = int(n or 0)
            except (TypeError, ValueError):
                nv = 0
            if nv > 0:
                shown.append((label, nv))
        if shown:
            out += ["### 同期还有、本次未纳入的类型", ""]
            for label, nv in shown:
                note = tr.note_for_name(label)
                out.append(f"- {label}：{nv}" + (f"（{note}）" if note else ""))
            out.append("")

    # 未取到原件
    missing_rows = build_missing_rows(failed_items)
    out += [f"## 未取到原件（{len(missing_rows)} 条）", ""]
    if missing_rows:
        grouped_missing = []
        sig_index = {}
        for mr in missing_rows:
            rel = _rel_path(mr.get("输出路径") or "", base_dir)
            sender = _sender_of(rel, mr.get("来源") or "", sender_names)
            lid = mr.get("local_id") or ""
            entry = {
                "mr": mr,
                "rel": rel,
                "sender": sender,
                "lid": lid,
                "count": 1,
            }
            if not rel:
                key = (
                    _inline(mr.get("显示名称")),
                    sender,
                    str(lid),
                    _inline(mr.get("状态")),
                    _inline(mr.get("原因")),
                    _inline(mr.get("建议")),
                )
                if key in sig_index:
                    grouped_missing[sig_index[key]]["count"] += 1
                    continue
                sig_index[key] = len(grouped_missing)
            grouped_missing.append(entry)

        for entry in grouped_missing:
            mr = entry["mr"]
            rel = entry["rel"]
            sender = entry["sender"]
            lid = entry["lid"]
            cnt = entry["count"]
            head = f"- **{_inline(mr.get('显示名称'))}**（发送人：{sender}"
            if lid:
                head += f" · `local_id: {lid}`"
            head += "）"
            if cnt > 1:
                head += f" × {cnt} 件"
            if rel:
                head += f" · {_inline_link('查看缩略图', rel)}"
            out.append(head)
            out.append(f"  - 状态：{_inline(mr.get('状态'))} — {_inline(mr.get('原因'))}")
            out.append(f"  - 建议：{_inline(mr.get('建议'))}")
    else:
        out.append("本批无未取到原件。")
    out.append("")

    # 导出明细（按发送人分组）
    out += [f"## 导出明细（{len(top_items)} 条）", ""]
    if not top_items:
        out.append("本批无导出条目。")
        out.append("")
    else:
        idx = 0
        for sender in sender_order:
            group = by_sender[sender]
            out += [f"### {sender}（{len(group)} 条）", ""]
            for it in group:
                idx += 1
                out += _render_entry_lines(idx, it, child_map)
            out.append("")

    # 简明说明放在文末，数据永远排在最前面
    out += [
        "## 说明",
        "",
        "- **目录结构**：按发送人分文件夹，没有发送人的归入「未知发送人」；未取到原件时的缩略图文件名带 `_缩略`；聊天记录打包在 `<发送人>/聊天记录/<序号>_<日期>/index.html`（附件同目录相对引用，可整体拷走）。",
        "- **如何拿回原件**：图片需事先在微信里点开大图并加载完成，视频需事先在微信里真正播放过；「头部缺失」请先补齐图片密钥。完成后点本程序「补导」即可写回同一批次并重渲聊天记录网页。",
        "- **本机未缓存与语音**：「本机未缓存」指原件仅在微信云端、本电脑从未下载；语音在微信 PC 端通常不保留本体，记为「仅元数据」并在清单保留时长。",
        "- **缓存还原**：部分文件由微信聊天缓存整文件异或还原，内容本身为完整原图（非原始文件 MD5）。",
        "- **文件精简**：批次内仅保留本报告供阅读；`_补导状态.json` 已设为隐藏供程序「补导」读取（不含密钥与账号路径），无需手工操作。",
        "",
    ]

    Path(dest).write_text("\n".join(out) + "\n", encoding="utf-8")
    return dest
