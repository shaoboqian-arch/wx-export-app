# -*- coding: utf-8 -*-
"""逐条进度 / 分类计数 / 失败清单复检，并与基线导出报告逐行对账。

用法: python tests/verify_progress.py "<exe 绝对路径>"   # 走 --cli --export（无事件）
      python tests/verify_progress.py                     # 源码态，含事件流校验
"""
import csv
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SRC = os.path.join(APP, "src")
VENDOR = os.path.join(APP, "build", "vendor")
for p in (VENDOR, SRC):
    if p not in sys.path:
        sys.path.insert(0, p)
import engine  # noqa: E402

DEST = os.path.join(APP, "验证输出", "进度验证")
FRM, TO = "2026-11-15", "2026-11-15"
# 本地复检用的失败样本区间（含「本地仅缩略图」的条目）；换机器时按本机数据调整
BAD_FRM, BAD_TO = "2016-08-11", "2016-08-11"
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append({"name": name, "ok": bool(cond), "detail": str(detail)})
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail else ""), flush=True)


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else None
    os.makedirs(DEST, exist_ok=True)
    events = []
    r = engine.safe_call(engine.export, FRM, TO, DEST,
                         on_event=lambda e: events.append(e))
    check("导出成功", r.get("ok") is True, r.get("error", ""))
    if not r.get("ok"):
        return 1

    phases = [e["name"] for e in events if e["type"] == "phase"]
    check("阶段顺序 index→export→report→done",
          phases == ["index", "export", "report", "done"], str(phases))

    items = [e["item"] for e in events if e["type"] == "item"]
    check("逐条事件数 == 报告行数", len(items) == r["total"],
          f"events={len(items)} total={r['total']}")
    check("事件序号连续", [x["i"] for x in items] == list(range(1, len(items) + 1)))
    check("分类计数与总数自洽",
          sum(v["total"] for v in r["by_kind"].values()) == r["total"]
          and sum(v["ok"] for v in r["by_kind"].values()) == r["counts"].get("完整", 0),
          json.dumps(r["by_kind"], ensure_ascii=False))
    check("失败清单条数一致", len(r["failed_items"]) == r["failed_count"]
          and len(r["failed_items"]) == sum(1 for x in items if not x["ok"]),
          f"failed={r['failed_count']}")
    check("失败项都带原因", all(x["reason"] for x in r["failed_items"]))
    check("图片/视频/其他三类都在", set(r["by_kind"]) == {"image", "video", "other"})

    # 与对账行逐行核对：状态、输出文件名必须一致
    report = engine.read_batch_rows(r["batch_path"])
    mismatches = []
    for i, (it, row) in enumerate(zip(items, report), 1):
        disk_name = os.path.basename(row.get("输出路径", "")) if row.get("输出路径") else ""
        if it["name"] != disk_name or it["state"] != row.get("状态"):
            mismatches.append((i, it["name"], disk_name, it["state"], row.get("状态")))
    check("逐条与基线报告一致（文件名/状态）", not mismatches, str(mismatches[:3]))

    # 真实含失败项的区间：验证失败文件名与原因确实被列出来
    r2 = engine.safe_call(engine.export, BAD_FRM, BAD_TO, DEST)
    check("含失败项区间导出成功", r2.get("ok") is True, r2.get("error", ""))
    if r2.get("ok"):
        fi = r2["failed_items"]
        check("真实失败项被列出", r2["failed_count"] == 3 and len(fi) == 3,
              f"count={r2['failed_count']} names={[x['name'] for x in fi]}")
        check("失败项均有文件名与原因", all(x["name"] and x["reason"] for x in fi),
              str([(x["name"], x["reason"]) for x in fi[:2]]))
        check("失败项状态可识别", all(x["state"] == "本地仅缩略图" for x in fi),
              str([x["state"] for x in fi]))
        check("失败项按类型归入视频/其他", all(x["group"] in ("video", "other") for x in fi),
              str([(x["kind"], x["group"]) for x in fi]))
        bad_rows = [x for x in engine.read_batch_rows(r2["batch_path"])
                    if x.get("状态") != "完整"]
        check("失败清单与报告非完整行一一对应", len(bad_rows) == len(fi),
              f"报告 {len(bad_rows)} / 清单 {len(fi)}")

    if exe:
        # 冻结 exe 的 CLI 结果也必须带同一套分类与失败清单字段
        p = subprocess.run([exe, "--cli", "--export", "--dest", DEST,
                            "--from", FRM, "--to", TO],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
        s = (p.stdout or "").strip()
        out = json.loads(s[s.index("{"):]) if "{" in s else {}
        check("冻结 exe 结果含 by_kind/failed_items",
              all(k in out for k in ("by_kind", "failed_items", "failed_count")),
              f"rc={p.returncode}")
    return 1 if [x for x in RESULTS if not x["ok"]] else 0


if __name__ == "__main__":
    sys.exit(main())
