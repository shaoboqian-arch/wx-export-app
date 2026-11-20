# -*- coding: utf-8 -*-
"""导出清单复核：从微信库 + 磁盘现状反推，逐条核对 _导出清单.csv 登记是否无误。

用法: python tests/verify_manifest.py "<批次目录>"

只读：不写批次、不改清单。任何一项不通过都会打印具体行，退出码 1。
"""
import collections
import csv
import hashlib
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
for p in (os.path.join(APP, "src"), os.path.join(APP, "src", "core"),
          os.path.join(APP, "build", "vendor")):
    if p not in sys.path:
        sys.path.insert(0, p)

import chatrecord as cr  # noqa: E402
import engine  # noqa: E402
import sender_archive as sa  # noqa: E402

BATCH = sys.argv[1] if len(sys.argv) > 1 else ""
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail else ""), flush=True)


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    if not BATCH or not os.path.isdir(BATCH):
        print("用法: python tests/verify_manifest.py \"<批次目录>\"")
        return 2
    batch = os.path.abspath(BATCH)
    rows = engine.read_batch_manifest(batch)
    if not rows:
        print("批次里没有导出清单记录（_补导状态.json / _导出清单.csv）")
        return 2

    report = engine.read_batch_rows(batch)
    st = engine.read_retry_state(batch)
    check("新批次无冗余 CSV",
          not os.path.exists(os.path.join(batch, "_导出清单.csv"))
          and not os.path.exists(os.path.join(batch, "_导出报告.csv"))
          if isinstance(st.get("manifest"), list) else True)
    print(f"批次: {os.path.basename(batch)}")
    print(f"区间: {st.get('from')} → {st.get('to')} · kinds={st.get('kinds')}")
    print(f"清单 {len(rows)} 行 · 报告 {len(report)} 行\n")

    fe = engine._setup()
    a, b, query_to = engine._range(st["from"], st["to"])
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        allrows = fe.query(a.isoformat(), query_to, None)
        chat_raw = engine._chat_rows(fe, a.isoformat(), query_to)
    packs = {lid: cr.expand(c) for lid, _ut, c, _f in chat_raw}
    pack_meta = {lid: (ut, f) for lid, ut, c, f in chat_raw}

    # ---------- 1. 序号 ----------
    seqs = [str(r["序号"]) for r in rows]
    check("序号唯一", len(set(seqs)) == len(seqs), f"{len(seqs)} 行")
    try:
        want = [str(i) for i in range(1, len(rows) + 1)]
        check("序号从 1 连续到 N", seqs == want,
              "" if seqs == want else f"首尾 {seqs[:3]}…{seqs[-3:]}")
    except Exception:
        check("序号从 1 连续到 N", False, "序号非数字")

    # ---------- 2. 聊天记录包：父行 ----------
    parent = [r for r in rows if (r.get("状态") or "") == "已生成HTML"]
    failed_parent = [r for r in rows if (r.get("状态") or "") == "HTML生成失败"]
    check("父行数 == 区间内打包收藏数",
          len(parent) + len(failed_parent) == len(packs),
          f"父行 {len(parent)}+{len(failed_parent)} · 库 {len(packs)}")
    bad = [r for r in parent
           if not (r.get("输出路径") or "").strip()
           or not os.path.isfile(r["输出路径"])
           or not r["输出路径"].endswith("index.html")]
    check("父行输出路径都指向存在的 index.html", not bad, f"{len(bad)} 行异常")

    lids_in_parent = {str(r.get("所属收藏") or "") for r in parent}
    check("父行的所属收藏覆盖所有包",
          lids_in_parent == {str(x) for x in packs}, 
          f"缺 {sorted({str(x) for x in packs} - lids_in_parent)[:5]}")

    # ---------- 3. 子项：数量与正文 ----------
    sub = [r for r in rows if (r.get("所属收藏") or "").strip()]
    per_lid = collections.Counter((r["所属收藏"] or "") for r in sub)
    # 每个包的父行也算一行，子行数 = 总行数 - 父行
    mismatch = []
    for lid, rec in packs.items():
        got = per_lid.get(str(lid), 0)
        # 父行也带所属收藏，所以期望 = 子项数 + 1（父行）
        if got != len(rec["items"]) + 1:
            mismatch.append((lid, got, len(rec["items"]) + 1))
    check("每个包的登记行数 == 子项数 + 父行", not mismatch,
          f"{len(mismatch)} 个包不符 · 例 {mismatch[:3]}")

    db_text = {}
    for lid, rec in packs.items():
        for it in rec["items"]:
            if it["text"]:
                db_text.setdefault(it["text"], 0)
                db_text[it["text"]] += 1
    list_text = [r for r in rows if (r.get("内容") or "").strip()
                 and (r.get("类型") or "") == "聊天记录"]
    db_total_text = sum(1 for rec in packs.values() for it in rec["items"] if it["text"])
    check("聊天正文行数 == 库里的正文子项数",
          len(list_text) == db_total_text, f"清单 {len(list_text)} · 库 {db_total_text}")
    unknown = [r for r in list_text if r["内容"] not in db_text]
    check("每条正文都能在库里找到原文", not unknown,
          f"{len(unknown)} 行对不上 · 例 {(unknown[0]['内容'][:20] if unknown else '')}")

    # ---------- 4. 媒体行：状态与文件 ----------
    media = [r for r in rows if (r.get("类型") or "") in ("图片", "视频", "语音")]
    bad_state = []
    for r in media:
        state = (r.get("状态") or "")
        path = (r.get("输出路径") or "").strip()
        if state == "完整" and not (path and os.path.isfile(path)):
            bad_state.append((r["序号"], state, "路径缺失"))
        elif state == "本地仅缩略图":
            if not (path and os.path.isfile(path)):
                bad_state.append((r["序号"], state, "路径缺失"))
            elif "_缩略" not in os.path.basename(path):
                bad_state.append((r["序号"], state, "文件名没带 _缩略"))
        elif state == "未定位本体" and path:
            bad_state.append((r["序号"], state, f"却写了路径 {os.path.basename(path)}"))
        elif state == "仅元数据" and path:
            bad_state.append((r["序号"], state, "却写了路径"))
    check("媒体行的状态与文件一一对上", not bad_state,
          f"{len(bad_state)} 行异常 · 例 {bad_state[:3]}")

    # md5：完整件要跟报告里的 fullmd5 逐字节一致（缓存还原件除外）
    # 清单序号是排序后重编的，报告的序号是导出时的，两套编号不能混用 → 按输出路径对
    rep_by_path = {os.path.abspath(r.get("输出路径") or ""): r for r in report
                   if (r.get("输出路径") or "").strip()}
    bad_md5 = []
    for r in media:
        if (r.get("状态") or "") != "完整":
            continue
        rep = rep_by_path.get(os.path.abspath(r["输出路径"])) or {}
        if (rep.get("还原") or "") == "缓存还原":
            continue
        full = (rep.get("fullmd5") or "").strip().lower()
        if not full:
            continue
        if md5_of(r["输出路径"]) != full:
            bad_md5.append((r["序号"], os.path.basename(r["输出路径"])))
    check("完整件的 md5 与库里 fullmd5 一致", not bad_md5,
          f"{len(bad_md5)} 件不符 · 例 {bad_md5[:3]}")

    # ---------- 5. 路径不出批次 ----------
    outside = [r["序号"] for r in rows
               if (r.get("输出路径") or "").strip()
               and not os.path.abspath(r["输出路径"]).startswith(batch + os.sep)]
    check("所有输出路径都在批次目录内", not outside, f"{len(outside)} 行越界")

    # ---------- 6. 反向：批次里的文件都被登记 ----------
    listed = {os.path.abspath(r["输出路径"]) for r in rows if (r.get("输出路径") or "").strip()}
    # `_` 前缀是机器文件；《导出报告.md》是人读报告，都不属于媒体登记范围。
    meta_names = {"导出报告.md"}
    orphans = []
    for dp, dn, fn in os.walk(batch):
        for f in fn:
            if f.startswith("_") or f in meta_names:
                continue
            p = os.path.abspath(os.path.join(dp, f))
            if p not in listed:
                orphans.append(os.path.relpath(p, batch))
    check("批次里的媒体文件都被清单登记", not orphans,
          f"{len(orphans)} 个孤儿文件 · 例 {orphans[:3]}")

    # ---------- 7. 类型口径与库对齐 ----------
    kind_by_type = {1: "文本", 2: "图片", 4: "视频", 16: "视频", 3: "语音", 8: "语音", 5: "链接"}
    db_count = collections.Counter()
    for r in allrows:
        if r["kind"] == "image":
            db_count["图片"] += 1
        elif r["kind"] == "video":
            db_count["视频"] += 1
        elif r["kind"] == "voice":
            db_count["语音"] += 1
    top_media = [r for r in media if not (r.get("所属收藏") or "").strip()]
    list_media = collections.Counter((r.get("类型") or "") for r in top_media)
    check("图片条数与库一致", list_media.get("图片", 0) == db_count.get("图片", 0),
          f"清单 {list_media.get('图片', 0)} · 库 {db_count.get('图片', 0)}")
    check("视频条数与库一致", list_media.get("视频", 0) == db_count.get("视频", 0),
          f"清单 {list_media.get('视频', 0)} · 库 {db_count.get('视频', 0)}")
    check("语音条数与库一致", list_media.get("语音", 0) == db_count.get("语音", 0),
          f"清单 {list_media.get('语音', 0)} · 库 {db_count.get('语音', 0)}")

    # ---------- 7b. 文本 / 链接条数与库一致 ----------
    db_text_n = sum(1 for r in allrows if r["kind"] == "text")
    db_link_n = sum(1 for r in allrows if r["kind"] == "article")
    list_text_n = sum(1 for r in rows if (r.get("类型") or "") == "文本")
    list_link_n = sum(1 for r in rows if (r.get("类型") or "") == "链接")
    check("文本条数与库一致", list_text_n == db_text_n,
          f"清单 {list_text_n} · 库 {db_text_n}")
    check("链接条数与库一致", list_link_n == db_link_n,
          f"清单 {list_link_n} · 库 {db_link_n}")

    # 文本行必须有正文，链接行必须有网址
    empty_text = [r["序号"] for r in rows
                  if (r.get("类型") or "") == "文本" and not (r.get("内容") or "").strip()]
    check("文本行都带正文", not empty_text, f"{len(empty_text)} 行为空")
    empty_url = [r["序号"] for r in rows
                 if (r.get("类型") or "") == "链接" and not (r.get("链接") or "").strip()]
    check("链接行都带网址", not empty_url, f"{len(empty_url)} 行为空")

    # ---------- 7c. 报告与清单的状态必须一致 ----------
    # 顶层媒体在两张表里都有登记，口径不能各说各话（曾经报告写未定位、清单写仅元数据）
    zh_of = {"image": "图片", "video": "视频", "voice": "语音"}
    rep_state = collections.Counter()
    for r in report:
        if (r.get("所属收藏") or "").strip():
            continue          # 聊天子项在报告里也有行，但不属于顶层媒体口径
        zh = zh_of.get(r.get("类型") or "")
        if zh:
            rep_state[(zh, r.get("状态") or "")] += 1
    man_state = collections.Counter()
    for r in rows:
        if (r.get("所属收藏") or "").strip():
            continue
        if (r.get("类型") or "") in ("图片", "视频", "语音"):
            man_state[(r["类型"], r.get("状态") or "")] += 1
    diff = {k: (rep_state.get(k, 0), man_state.get(k, 0))
            for k in set(rep_state) | set(man_state)
            if rep_state.get(k, 0) != man_state.get(k, 0)}
    check("报告与清单的状态分布一致", not diff, f"{len(diff)} 处不符 · 例 {list(diff.items())[:3]}")

    # 语音行：有时长就写进摘要，且时长按秒
    bad_voice = []
    for r in rows:
        if (r.get("类型") or "") != "语音":
            continue
        if (r.get("状态") or "") not in ("仅元数据",):
            bad_voice.append((r["序号"], "状态不是仅元数据"))
            continue
        summ = (r.get("摘要") or "")
        if not summ.startswith("语音"):
            bad_voice.append((r["序号"], f"摘要异常 {summ!r}"))
        m = re.search(r"(\d+)\s*秒", summ)
        if m and int(m.group(1)) > 600:
            bad_voice.append((r["序号"], f"时长像毫秒 {m.group(1)}"))
    check("语音行登记成「仅元数据 + 秒级时长」", not bad_voice,
          f"{len(bad_voice)} 行异常 · 例 {bad_voice[:3]}")

    # ---------- 8. 《导出报告.md》里的数字与实算一致 ----------
    readme = io.open(os.path.join(batch, "导出报告.md"), encoding="utf-8").read()
    real = collections.Counter((r.get("状态") or "") for r in rows)
    pairs = [("完整", real.get("完整", 0)), ("本地仅缩略图", real.get("本地仅缩略图", 0)),
             ("未定位本体", real.get("未定位本体", 0))]
    bad_doc = []
    for label, n in pairs:
        pat = re.search(r"%s：(\d+)" % re.escape(label), readme)
        if pat and int(pat.group(1)) != n:
            bad_doc.append((label, int(pat.group(1)), n))
    check("导出报告.md 里的计数与清单实算一致", not bad_doc,
          f"{len(bad_doc)} 处不符 · 例 {bad_doc[:3]}")

    failed = [n for n, ok, _d in results if not ok]
    print(f"\n合计 {len(results)} 项 · 失败 {len(failed)}")
    for n in failed:
        print("  ✗", n)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
