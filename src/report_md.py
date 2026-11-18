# -*- coding: utf-8 -*-
"""批次内唯一的人读报告《导出报告.md》：说明 + 未取到原件 + 导出清单 + 对账表 + 补导摘要。

合并原 _导出说明.txt 与 _未取到原件.csv 的全部内容；
_补导状态.json / _导出报告.csv / _导出清单.csv 仍按原样保留——
补导恢复、与基线对账、聊天记录包内容都依赖它们的机器格式。
"""
import os
from pathlib import Path

import type_registry as tr
from common import VERSION
from export_list import (
    MANIFEST_HEADERS, build_missing_rows, kinds_phrase,
    _md_cell, _md_link,
)

REPORT_MD_NAME = "导出报告.md"

# 对账表人读列（head256md5 只在 _导出报告.csv 里，人读版省去）。
REPORT_MD_COLS = ["序号", "收藏时间", "类型", "体积", "时长", "状态", "local_id",
                  "fullmd5", "输出路径", "描述", "来源", "所属收藏", "还原"]


def report_md_path(batch_dir):
    """《导出报告.md》在批次目录内的落点：规范化 + 必须落在批次目录内。"""
    base = os.path.normpath(os.path.abspath(batch_dir))
    dest = os.path.normpath(os.path.abspath(os.path.join(base, REPORT_MD_NAME)))
    if not dest.startswith(base + os.sep):
        raise ValueError(f"报告落点越出批次目录：{dest}")
    return dest


def _table(cols, rows, link_col="", rel_base=""):
    """markdown 表格；link_col 指定列转「打开网页/文件」链接，其余转义为安全文本。

    行可以是 dict（按列名取值）或定序列表（按列位置取值，导出清单行就是列表）。
    rel_base 给出时，链接列里的绝对路径先相对化到批次根——人读报告会随批次
    目录被拷走转发，不能把本机绝对路径带出去（CSV 里的原始路径不受影响）。
    """
    out = []
    if rows:
        out.append("| " + " | ".join(cols) + " |")
        out.append("|" + "|".join([" --- "] * len(cols)) + " |")
        idx = {c: i for i, c in enumerate(cols)}
        for r in rows:
            def val(c, _r=r):
                if isinstance(_r, dict):
                    return _r.get(c, "")
                i = idx[c]
                return _r[i] if i < len(_r) else ""
            cells = []
            for c in cols:
                v = val(c)
                if c == link_col and v:
                    if rel_base and os.path.isabs(str(v)):
                        try:
                            v = os.path.relpath(str(v), rel_base)
                        except ValueError:
                            pass
                    cells.append(_md_link(v))
                else:
                    cells.append(_md_cell(v))
            out.append("| " + " | ".join(cells) + " |")
    return out


def write_report(batch_dir, *, frm, to, include_text_links, resolve_kinds,
                 counts, written, media_total, text_count, link_count,
                 census=None, chat=None, failed_items=None,
                 report_rows=None, manifest_rows=None, pending_lids=None):
    """在批次目录内生成《导出报告.md》，返回写入路径。"""
    counts = counts or {}
    chat = chat or {}
    report_rows = report_rows or []
    manifest_rows = manifest_rows or []
    dest = report_md_path(batch_dir)
    base_dir = os.path.dirname(dest)
    out = ["# 微信收藏导出报告", ""]

    # 头 + 说明
    out += [
        f"- 版本：{VERSION}",
        f"- 日期范围：{frm} 至 {to}（起止均含当天）",
        f"- 本批导出内容：{kinds_phrase(include_text_links, resolve_kinds)}",
        "",
        "## 本批说明",
        "",
        "媒体文件（图片/视频及本次按类型导出的文件）：",
        "",
        f"- 完整：{int(counts.get('完整', 0) or 0)}",
        f"- 本地仅缩略图：{int(counts.get('本地仅缩略图', 0) or 0)}",
        f"- 头部缺失：{int(counts.get('头部缺失', 0) or 0)}",
        f"- 本机未缓存：{int(counts.get('未定位本体', 0) or 0)}"
        "（微信从没把原件下载到这台电脑，导出工具会如实标注，不是解析失败）",
        f"- 写出文件：{int(written or 0)} / {int(media_total or 0)}",
        "",
    ]
    if int(counts.get("缓存还原", 0) or 0) > 0:
        out += [
            f"其中 {int(counts.get('缓存还原', 0))} 件由微信聊天缓存还原"
            "（整文件单字节异或，下方对账表「还原」列有标注）。",
            "这类文件的 fullmd5 记的是缓存块的 md5、不是原图 md5，所以不能按 md5 校验；",
            "内容本身是完整可看的原图，还原后按能否解码来验收。",
            "",
        ]
    if include_text_links:
        out += [
            f"- 文本：{int(text_count or 0)} 条（全文见下方「导出清单」）",
            f"- 链接：{int(link_count or 0)} 条（标题、摘要和网址见下方「导出清单」）",
            "",
        ]

    # 聊天记录块
    if int(chat.get("records", 0) or 0) > 0:
        packs_ok = int(chat.get("packs_ok", 0) or 0)
        packs_bad = int(chat.get("packs_failed", 0) or 0)
        out += [
            f"聊天记录：{int(chat.get('records', 0))} 条打包收藏，展开 "
            f"{int(chat.get('items', 0))} 个子项。",
            "",
            f"- HTML：{packs_ok} 个包已生成"
            + (f"，{packs_bad} 个包生成失败（见下）" if packs_bad else "，全部成功"),
            "- 每个包一个目录：<发送人>/聊天记录/<序号>_<日期>/index.html，",
            "  附件与 index.html 放在同一目录，页面用相对路径引用，整个目录可直接拷走；",
            "  下方「导出清单」里这些包的「打开网页」链接可直接跳转。",
            f"- 正文 {int(chat.get('text', 0))} 条同时写入清单"
            "（「所属收藏」列是原收藏的 local_id）。",
            f"- 媒体子项 {int(chat.get('media', 0))} 个，落地 {int(chat.get('files', 0))} 个文件。",
        ]
        failed_packs = chat.get("failed_packs") or []
        if failed_packs:
            out.append("- HTML 生成失败的包：")
            for p in failed_packs[:20]:
                out.append(f"  - {p.get('title') or '（无标题）'}"
                           f"（local_id {p.get('local_id')}）：{p.get('reason') or '未知原因'}")
            if len(failed_packs) > 20:
                out.append(f"  - …… 另有 {len(failed_packs) - 20} 个，"
                           "完整清单见本次导出结果 JSON 的 chat.failed_packs。")
        if int(chat.get("语音可播", 0) or 0) > 0:
            out.append(
                f"- 语音 {int(chat.get('语音可播', 0))} 条在本机找到了音频（被当文件发过的那种），"
                "已随包导出，HTML 里点语音条可直接播放。")
        if int(chat.get("仅元数据", 0) or 0) > 0:
            out.append(
                f"- 另有 {int(chat.get('仅元数据', 0))} 个语音/位置类子项只记元数据："
                "微信 PC 端不保留 silk 语音本体，本机取不到；"
                "HTML 里语音按微信的样子画成语音条（点一下会说明为什么不能播），"
                "位置给地址和地图链接，不留空白。")
        out.append("")

    # 目录含义 + 未纳入普查
    out += [
        "目录含义：",
        "",
        "- 同一批次内按发送人分文件夹；没有发送人的放在「未知发送人」。",
        "- 完整件与缩略图放在同一发送人文件夹里，缩略图的文件名带「_缩略」。",
        "- 聊天记录的附件在「<发送人>/聊天记录/<local_id>_<日期>/」下，不与图片视频混放。",
        "",
        "要拿到图片原件，需要事先在微信中点开大图并加载完成；",
        "要拿到视频原件，需要事先在微信中真正播放过该视频。",
        "「本机未缓存」只统计图片/视频。语音和位置类条目本机从来没有本体，",
        "它们不计入这个数字，只在清单里留一条元数据记录。",
        "头部缺失的条目，请先补齐图片密钥。",
        "未取到原件的条目见下方「未取到原件」一节。按其中建议处理完成后，",
        "可在本程序中点「补导」，只补这些条目，不必重新选择日期和文件夹。",
        "聊天记录里的附件也可以补导（按报告行自己的体积与 md5 单条认领）：升格后原件写回同一个包目录，",
        "并自动重渲这个包的 index.html。",
        "",
        "本批条目见下方「导出清单」一节；核心对账数据另存 `_导出报告.csv`"
        "（仅含本次走文件导出的条目，供程序与脚本对账）。",
        "",
    ]
    if include_text_links and census:
        shown = []
        for label, n in census.items():
            try:
                n = int(n or 0)
            except (TypeError, ValueError):
                n = 0
            if n > 0:
                shown.append((label, n))
        if shown:
            out += ["同期还有、本次未纳入的类型：", ""]
            for label, n in shown:
                note = tr.note_for_name(label)
                out.append(f"- {label}：{n}" + (f"（{note}）" if note else ""))
            out.append("")

    # 未取到原件
    missing_rows = build_missing_rows(failed_items)
    out += [f"## 未取到原件（{len(missing_rows)} 条）", ""]
    if missing_rows:
        cols = ["收藏时间", "类型", "状态", "原因", "建议", "显示名称", "描述", "local_id"]
        out += _table(cols, missing_rows)
    else:
        out.append("本批无未取到原件。")
    out.append("")

    # 导出清单
    out += [f"## 导出清单（{len(manifest_rows)} 条）", ""]
    if manifest_rows:
        out += _table(MANIFEST_HEADERS, manifest_rows, link_col="输出路径",
                      rel_base=base_dir)
    else:
        out.append("本批无清单条目。")
    out.append("")

    # 对账表
    out += [f"## 导出报告（对账，{len(report_rows)} 条）", ""]
    if report_rows:
        out += _table(REPORT_MD_COLS, report_rows, link_col="输出路径",
                      rel_base=base_dir)
        out += ["", "完整 14 列（含 head256md5）以 `_导出报告.csv` 为准。"]
    else:
        out.append("本批无走文件导出的条目。")
    out.append("")

    # 补导摘要
    lids = [str(x) for x in (pending_lids or [])]
    out += [
        "## 补导状态",
        "",
        f"- 待补条目：{len(lids)}"
        + (f"（local_id：{'、'.join(lids[:20])}"
           + ("……" if len(lids) > 20 else "") + "）" if lids else ""),
        "- 状态文件：`_补导状态.json`（记录日期范围/类型/待补 local_id，"
        "不含密钥与账号路径；程序「补导」按钮读取它）",
        "",
    ]

    Path(dest).write_text("\n".join(out) + "\n", encoding="utf-8")
    return dest
