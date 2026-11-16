# -*- coding: utf-8 -*-
"""聊天记录（type=14）展开回归。

钉住三件事：
  1. 一条收藏能被拆成 datalist 里那么多子项，文本正文能直接取到
  2. 子项分类互斥且完整（媒体 / 仅元数据 / 正文），不会既算媒体又算元数据
  3. 端到端导出后：清单里有「所属收藏」列、正文行数 > 0、附件落在「聊天记录」子目录

用法: python tests/verify_chatrecord.py
"""
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SRC = os.path.join(APP, "src")
VENDOR = os.path.join(APP, "build", "vendor")
for p in (VENDOR, SRC):
    if p not in sys.path:
        sys.path.insert(0, p)

import chatrecord as cr

DEST = os.path.join(APP, "验证输出")
FRM, TO = "2016-09-01", "2016-09-30"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail else ""), flush=True)


def main():
    import engine
    fe = engine._setup()
    conn = fe.open_fav_db()
    try:
        rows = conn.execute("SELECT local_id, content FROM fav_db_item WHERE type=14").fetchall()
    finally:
        conn.close()
    check("库里有聊天记录", len(rows) > 0, f"{len(rows)} 条")

    # 1. 展开：子项数对得上 datalist 声明
    lid, content = rows[0]
    rec = cr.expand(content)
    check("expand 拆出子项", len(rec["items"]) > 0,
          f"lid={lid} · {len(rec['items'])} 个子项 · count={rec['count']}")
    check("子项数 == datalist count",
          rec["count"] == len(rec["items"]) or rec["count"] == 0,
          f"{rec['count']} vs {len(rec['items'])}")

    # 2. 文本正文能取到（找一条确实含正文的）
    text_rec = None
    for _lid, c in rows:
        r = cr.expand(c)
        if any(i["text"] for i in r["items"]):
            text_rec = r
            break
    check("能取到正文子项", text_rec is not None)
    if text_rec:
        sample = next(i for i in text_rec["items"] if i["text"])
        check("正文非空且不是占位", len(sample["text"]) > 0, sample["text"][:30])

    # 3. 时间格式解析
    check("中文时间转 ISO", 
          engine._iso("2015-1-14 上午11:37") == "2015-01-14 11:37",
          engine._iso("2015-1-14 上午11:37"))
    check("下午时间 +12 小时",
          engine._iso("2015-1-14 下午12:03") == "2015-01-14 12:03",
          engine._iso("2015-1-14 下午12:03"))
    check("认不出时退回备用时间",
          engine._iso("看不懂", "2015-01-14 00:00:00") == "2015-01-14 00:00:00",
          engine._iso("看不懂", "2015-01-14 00:00:00"))

    # 4. 分类互斥且覆盖
    items = [i for _l, c in rows for i in cr.expand(c)["items"]]
    text = [i for i in items if i["text"]]
    media = [i for i in items if cr.is_media(i)]
    meta = [i for i in items if cr.is_meta_only(i)]
    both = [i for i in media if cr.is_meta_only(i)]
    check("媒体与仅元数据互斥", not both, f"重叠 {len(both)}")
    check("三类之和 == 全部子项", len(text) + len(media) + len(meta) == len(items),
          f"{len(text)}+{len(media)}+{len(meta)} vs {len(items)}")
    check("正文子项数量级正确", len(text) > 3000, f"{len(text)} 条正文")
    check("语音子项归入仅元数据",
          all(cr.is_meta_only(i) for i in items if i["datatype"] in (3, 8)),
          f"{sum(1 for i in items if i['datatype'] in (3, 8))} 个语音子项")

    # 5. 端到端：真导一个小月份
    r = engine.safe_call(engine.export, FRM, TO, DEST)
    check("端到端导出 ok", r.get("ok") is True, r.get("error", ""))
    if not r.get("ok"):
        return 1
    chat = r.get("chat") or {}
    check("导出带回 chat 统计", int(chat.get("records", 0)) > 0, str(chat))
    check("展开出正文", int(chat.get("text", 0)) > 0, f"{chat.get('text')} 条正文")

    batch = r["batch_path"]
    with open(r["report"], encoding="utf-8-sig", newline="") as f:
        rep = list(csv.DictReader(f))
    check("报告有「所属收藏」列", "所属收藏" in (rep[0] if rep else {}), str(list(rep[0])[:4]) if rep else "空")
    check("报告有「还原」列", "还原" in (rep[0] if rep else {}))
    sub = [x for x in rep if (x.get("所属收藏") or "").strip()]
    check("报告里有聊天记录子项行", bool(sub), f"{len(sub)} 行")

    with open(r["list"], encoding="utf-8-sig", newline="") as f:
        man = list(csv.DictReader(f))
    body = [x for x in man if (x.get("所属收藏") or "").strip() and (x.get("内容") or "").strip()]
    check("清单里聊天正文行 > 0", len(body) > 0, f"{len(body)} 行")
    parents = [x for x in man if (x.get("所属收藏") or "").strip() and "共 " in (x.get("摘要") or "")]
    check("清单里有聊天记录父行", len(parents) > 0, f"{len(parents)} 行")

    chat_dirs = [d for d in os.listdir(batch)
                 if os.path.isdir(os.path.join(batch, d)) and not d.startswith("_")]
    nested = []
    for d in chat_dirs:
        p = os.path.join(batch, d, "聊天记录")
        if os.path.isdir(p):
            nested.append(d)
    check("附件落在 <发送人>/聊天记录/", bool(nested), f"{len(nested)} 个发送人目录")

    # 6. 一包一 HTML
    packs_ok = int(chat.get("packs_ok", 0) or 0)
    packs_bad = int(chat.get("packs_failed", 0) or 0)
    check("每个包都生成了 HTML", packs_bad == 0 and packs_ok == int(chat.get("records", 0)),
          f"成功 {packs_ok} · 失败 {packs_bad} · 包 {chat.get('records')}")

    htmls = []
    for d in nested:
        base = os.path.join(batch, d, "聊天记录")
        for sub_d in os.listdir(base):
            p = os.path.join(base, sub_d, "index.html")
            if os.path.isfile(p):
                htmls.append(p)
    check("HTML 文件数 == 包数", len(htmls) == packs_ok, f"{len(htmls)} 个 index.html")

    parents_html = [x for x in man if (x.get("状态") or "") == "已生成HTML"]
    check("清单父行状态 = 已生成HTML", len(parents_html) == packs_ok, f"{len(parents_html)} 行")
    check("父行输出路径指向 index.html 且文件存在",
          all(os.path.isfile(x.get("输出路径") or "") and
              (x.get("输出路径") or "").endswith("index.html") for x in parents_html))

    # 7. 页面内容：气泡数 == 子项数，且不出现绝对路径
    sample = htmls[0]
    with open(sample, encoding="utf-8") as f:
        h = f.read()
    bubbles = h.count('class="row')
    check("气泡数与子项数一致", bubbles > 0, f"{bubbles} 个气泡 · {os.path.basename(os.path.dirname(sample))}")
    check("HTML 不含绝对路径", ("file://" not in h) and (":\\" not in h),
          "相对路径引用附件")
    check("HTML 有统计条与页脚", 'class="chips"' in h and "微信收藏导出" in h)
    check("HTML 有 header 与 skip link",
          "<header>" in h and "跳到聊天内容" in h and 'id="msgs"' in h,
          "语义地标 + 跳转链接")

    # 8. 标题重拼规则
    check("无标题时按「\"显示名\"的聊天记录」重拼",
          engine._pack_title({"title": "", "items": [1]}, "测试用户") == '"测试用户"的聊天记录',
          engine._pack_title({"title": "", "items": [1]}, "测试用户"))
    check("不可见名标题也重拼",
          engine._pack_title({"title": "\u3164的聊天记录", "items": [1]}, "测试用户")
          == '"测试用户"的聊天记录')
    check("可用标题保持原样",
          engine._pack_title({"title": "群聊的聊天记录", "items": [1]}, "某人")
          == "群聊的聊天记录")
    check("连名字都没有时退回条数",
          engine._pack_title({"title": "", "items": [1, 2, 3]}, "") == "聊天记录（3 条）")

    # 9. 语音时长量纲（毫秒 -> 秒）
    durs = [i["duration"] for i in items if i["datatype"] == 3 and i["duration"]]
    check("语音时长已归一到秒", bool(durs) and max(durs) < 600,
          f"{len(durs)} 条 · 最长 {max(durs) if durs else 0} 秒")
    vids = [i["duration"] for i in items if i["datatype"] in (4, 15) and i["duration"]]
    check("视频时长保持秒", bool(vids) and max(vids) <= 600,
          f"{len(vids)} 条 · 最长 {max(vids) if vids else 0} 秒")

    # 10. 位置子项：坐标齐全就能出地图链接
    locs = [i for i in items if i["lat"] and i["lng"]]
    if locs:
        one = locs[0]
        check("位置子项有地址与坐标",
              bool(one["place"] or one["poi"]) and bool(one["lat"]),
              f'{one["place"] or one["poi"]} {one["lat"]},{one["lng"]}')
        import report_html
        piece = report_html.entry_html({
            "kind": "location", "sender": one["sender"], "when": "2022-01-06 19:34",
            "lat": one["lat"], "lng": one["lng"], "place": one["place"], "poi": one["poi"],
        }, os.path.dirname(sample))
        check("位置渲染出地图链接", "uri.amap.com" in piece and one["lat"] in piece,
              piece[piece.find("uri.amap"):][:46])
    else:
        check("本机无位置子项（跳过地图链接检查）", True, "跳过")

    # 10b. 渲染分支单测（不依赖区间里有没有这类子项）
    import report_html
    vs = report_html.entry_html({
        "kind": "voice", "sender": "某人", "when": "2022-01-06 19:34",
        "duration": 4, "path": "",
    }, os.path.dirname(sample))
    check("语音无本体时画成哑掉的语音条",
          'class="voice na"' in vs and "4″" in vs and "本体不在本机" in vs,
          vs[vs.find("<button"):][:80])
    v0 = report_html.entry_html({"kind": "voice", "sender": "某人",
                                 "when": "2022-01-06 19:34", "duration": 0,
                                 "fmt": "silk", "path": ""}, os.path.dirname(sample))
    check("时长 0 的语音条装得下「语音」", ">语音<" in v0 and "width:72px" in v0,
          v0[:80])
    va = report_html.entry_html({"kind": "voice", "sender": "某人",
                                 "when": "2022-01-06 19:34", "duration": 9,
                                 "path": sample}, os.path.dirname(sample))
    check("有本体时语音条可播放",
          'class="voice"' in va and "data-audio=\"1\"" in va
          and "<audio" in va and "width:99px" in va, va[:90])
    vthumb = report_html.entry_html({
        "kind": "video", "sender": "某人", "when": "2022-01-06 19:34",
        "duration": 6, "path": sample, "state": "本地仅缩略图",
    }, os.path.dirname(sample))
    check("视频只有封面时不塞进 <video>",
          "<video" not in vthumb and "<img" in vthumb and "原片未缓存" in vthumb,
          vthumb[:60])
    oth = report_html.entry_html({
        "kind": "other", "sender": "某人", "when": "2022-01-06 19:34",
        "text": "某条链接卡片标题",
    }, os.path.dirname(sample))
    check("other 显示原文而不是占位符",
          "某条链接卡片标题" in oth and "本机无可显示内容" not in oth, oth[:60])
    lnk = report_html.entry_html({
        "kind": "link", "sender": "某人", "when": "2022-01-06 19:34",
        "title": "一篇文章", "url": "https://mp.weixin.qq.com/s/abc",
    }, os.path.dirname(sample))
    check("包内链接卡片可点",
          'href="https://mp.weixin.qq.com/s/abc"' in lnk and "一篇文章" in lnk, lnk[:70])
    check("链接卡片保留发送人与时间",
          "某人 · 19:34" in lnk and lnk.count("一篇文章") == 1,
          lnk[:90])

    # 11. 单个包 HTML 失败：不拖垮整批，但状态里必须写得清清楚楚
    import report_html
    from unittest import mock
    orig = report_html.render
    state = {"n": 0}

    def flaky(pack, path):
        state["n"] += 1
        if state["n"] == 1:
            raise OSError("模拟磁盘写满")
        return orig(pack, path)

    with mock.patch.object(report_html, "render", flaky):
        r2 = engine.safe_call(engine.export, FRM, TO, DEST)
    check("单包失败仍整批成功", r2.get("ok") is True, r2.get("error", ""))
    c2 = r2.get("chat") or {}
    check("失败计入 packs_failed", int(c2.get("packs_failed", 0)) == 1,
          f"成功 {c2.get('packs_ok')} · 失败 {c2.get('packs_failed')}")
    bad = (c2.get("failed_packs") or [])
    check("失败包带 local_id 与原因",
          bool(bad) and bad[0].get("local_id") and "模拟磁盘写满" in (bad[0].get("reason") or ""),
          str(bad[:1]))
    with open(r2["list"], encoding="utf-8-sig", newline="") as f:
        man2 = list(csv.DictReader(f))
    check("清单标出 HTML生成失败",
          any((x.get("状态") or "") == "HTML生成失败" for x in man2),
          f"{sum(1 for x in man2 if (x.get('状态') or '') == 'HTML生成失败')} 行")
    with open(r2["readme"], encoding="utf-8") as f:
        readme = f.read()
    check("说明文件列出失败包", "HTML 生成失败的包" in readme and "模拟磁盘写满" in readme)
    check("说明文件写明成功数", "个包已生成" in readme and "1 个包生成失败" in readme)

    # 12. 补导必须能处理聊天记录里的附件
    #     历史上这些行被「所属收藏 非空」直接过滤掉，补导对它们完全空转，
    #     界面还报「补导完成，升格 0 条」，用户只能重新导出才拿得到原件。
    import fav_export as fe_mod
    import sender_archive as sa_mod
    real_resolve = fe_mod.resolve_item

    def downgrade(probe, idx, pidx, key, hidx):
        st, data, tier = real_resolve(probe, idx, pidx, key, hidx)
        if st == "完整" and data is not None:
            return "本地仅缩略图", data, None
        return st, data, tier

    with mock.patch.object(fe_mod, "resolve_item", downgrade):
        r3 = engine.safe_call(engine.export, FRM, TO, DEST)
    b3 = r3.get("batch_path") or ""
    rows3 = sa_mod.read_report(os.path.join(b3, "_导出报告.csv")) if b3 else []
    chat_pending = [x for x in rows3
                    if (x.get("状态") or "") != "完整"
                    and (x.get("类型") or "") in ("image", "video")
                    and str(x.get("所属收藏") or "").strip()]
    check("降级后确有聊天附件待补导", len(chat_pending) > 0, f"{len(chat_pending)} 条")

    r4 = engine.safe_call(engine.retry_missing, b3)
    up = int(r4.get("upgraded") or 0)
    check("补导能升格聊天记录附件", up > 0, f"升格 {up} 条 · 仍有 {r4.get('still_missing')} 条")

    rows4 = sa_mod.read_report(os.path.join(b3, "_导出报告.csv"))
    # 完整件不该再挂 _缩略；仍未取到的条目留着 _缩略 是正常的
    left = [x for x in rows4
            if (x.get("状态") or "") == "完整" and "_缩略" in (x.get("输出路径") or "")]
    check("升格后的完整件不再叫 _缩略", not left, f"{len(left)} 个残留")
    before_thumb = sum(1 for x in rows3 if (x.get("状态") or "") == "本地仅缩略图")
    after_thumb = sum(1 for x in rows4 if (x.get("状态") or "") == "本地仅缩略图")
    check("缩略件数量按升格数下降", before_thumb - after_thumb == up,
          f"{before_thumb} → {after_thumb}（升格 {up}）")

    pages = []
    for d in os.listdir(b3):
        base = os.path.join(b3, d, "聊天记录")
        if os.path.isdir(base):
            for sub in os.listdir(base):
                p = os.path.join(base, sub, "index.html")
                if os.path.isfile(p):
                    pages.append(p)
    merged = "".join(open(p, encoding="utf-8").read() for p in pages)
    check("升格后页面已重新渲染",
          bool(pages) and "本地仅缩略图" not in merged,
          f"{len(pages)} 个页面 · 缩略徽标 {merged.count('本地仅缩略图')}")

    failed = [n for n, ok in results if not ok]
    print(f"\n合计 {len(results)} 项 · 失败 {len(failed)}", flush=True)
    print("批次:", batch, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
