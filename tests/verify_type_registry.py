# -*- coding: utf-8 -*-
"""类型注册表回归：确认「一个类型只有一个结论」。

V0.2.0 之前类型判断散在两处且互相矛盾（core/fav_export.py::kind_of 与
export_list.py::DEFAULT_INCLUDED_TYPES）。这个脚本用真实收藏库把两件事钉住：
  1. 库里出现的每个 type 都在注册表里，或者走「未知类型」降级而不是静默丢弃
  2. 注册表的能力标记与 common.DEFAULT_KINDS / export_list.DEFAULT_INCLUDED_TYPES 一致

用法: python tests/verify_type_registry.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SRC = os.path.join(APP, "src")
VENDOR = os.path.join(APP, "build", "vendor")
for p in (VENDOR, SRC):
    if p not in sys.path:
        sys.path.insert(0, p)

import common
import export_list as el
import type_registry as tr

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail else ""), flush=True)


def main():
    import engine
    fe = engine._setup()
    conn = fe.open_fav_db()
    try:
        counts = {int(t): int(n) for t, n in
                  conn.execute("SELECT type, COUNT(*) FROM fav_db_item GROUP BY type").fetchall()}
    finally:
        conn.close()
    total = sum(counts.values())
    print("真实库:", total, "条 ·", len(counts), "种 type", flush=True)

    unknown = [t for t in counts if not tr.is_known(t)]
    check("库中每个 type 都已登记", not unknown,
          f"未登记 {unknown}" if unknown else f"{len(counts)} 种全部命中")

    # 旧实现的三处误判，逐条钉住
    check("type=4 一律 video（旧实现无 datafmt 时给 other）",
          tr.kind_of(4, "") == "video" and tr.kind_of(4, "<datafmt></datafmt>") == "video",
          tr.kind_of(4, ""))
    check("type=14 是 link（历史词保留，不改字符串）",
          tr.kind_of(14) == "link", tr.kind_of(14))
    check("type=14 展示名是「聊天记录」", tr.classify(14).name == "聊天记录",
          tr.classify(14).name)
    check("type=3 是 voice（旧实现给 other）", tr.kind_of(3) == "voice", tr.kind_of(3))
    check("type=8 是 voice（旧实现给 file）", tr.kind_of(8) == "voice", tr.kind_of(8))
    check("type=5 是 article（旧实现给 other）", tr.kind_of(5) == "article", tr.kind_of(5))
    check("type=20 是 finder（旧实现给 other）", tr.kind_of(20) == "finder", tr.kind_of(20))

    # 未登记类型必须降级，不能抛异常、也不能伪装成已知类型
    k99 = tr.classify(99)
    check("未登记 type 降级为 other", k99.kind == "other", f"{k99.kind} / {k99.name}")
    check("未登记 type 名字带出原值", "99" in k99.name, k99.name)
    check("未登记 type 不纳入默认", not (k99.default_media or k99.default_textual))

    # 三处真源必须一致
    check("common.DEFAULT_KINDS == registry 默认媒体 kind",
          tuple(sorted(common.DEFAULT_KINDS)) == tuple(sorted(tr.default_media_kinds())),
          f"{tuple(sorted(common.DEFAULT_KINDS))} vs {tuple(sorted(tr.default_media_kinds()))}")
    check("export_list.DEFAULT_INCLUDED_TYPES == registry 默认纳入 type",
          set(el.DEFAULT_INCLUDED_TYPES) == set(tr.default_included_types()),
          f"{sorted(el.DEFAULT_INCLUDED_TYPES)} vs {sorted(tr.default_included_types())}")
    check("export_list.KIND_ZH 与 registry 同名",
          all(el.kind_zh(k) == tr.name_by_kind(k) for k in tr.NAME_BY_KIND),
          "kind -> 展示名")
    check("core 的 kind_of 就是 registry 的 kind_of",
          fe.kind_of is not None and fe.kind_of(4) == tr.kind_of(4)
          and fe.kind_of(14) == tr.kind_of(14),
          "同一函数")

    # 全量跑一遍默认路径：不允许出现 other
    ks = engine._kinds(None)
    rows = fe.query("2014-01-01", "2027-01-01", None)
    others = [r for r in rows if r.get("kind") == "other"]
    check("全量分类无 other", not others, f"{len(others)} 条")
    default_rows = [r for r in rows if r.get("kind") in ks]
    # 语音没有本体（payload=False），但它现在也走默认路径，所以对照 default_media 而不是 media_types
    check("默认路径 = registry 默认媒体类型",
          {r["type"] for r in default_rows} == set(tr.default_media_types()),
          f"{sorted({r['type'] for r in default_rows})} vs {sorted(tr.default_media_types())}")
    check("有本体的类型仍不含语音",
          not ({3, 8} & set(tr.media_types())), f"{sorted(tr.media_types())}")

    census = el.census_from_types(counts)
    leaked = [n for n in census if n in {tr.name_of(t) for t in el.DEFAULT_INCLUDED_TYPES}]
    check("普查不含已纳入的类型", not leaked, f"泄漏 {leaked}" if leaked else str(census))

    failed = [n for n, ok in results if not ok]
    print(f"\n合计 {len(results)} 项 · 失败 {len(failed)}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
