# -*- coding: utf-8 -*-
"""真实数据导出回归：导出一个小区间，逐条核对报告 md5 与磁盘文件，并验证绝不覆盖。

用法:
    python tests/verify_export.py "<exe 绝对路径>"   # 测打包后的 exe --cli --export
    python tests/verify_export.py                    # 测源码 engine

产出落在 wx-export-app/验证输出/（工作区内），不碰用户既有交付目录。
"""
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SRC = os.path.join(APP, "src")
VENDOR = os.path.join(APP, "build", "vendor")
for p in (VENDOR, SRC):
    if p not in sys.path:
        sys.path.insert(0, p)

DEST = os.path.join(APP, "验证输出")
FRM, TO = "2026-11-15", "2026-11-15"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail else ""), flush=True)


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def do_export(exe, dest, frm=FRM, to=TO):
    t0 = time.time()
    if exe:
        r = subprocess.run([exe, "--cli", "--export", "--dest", dest,
                            "--from", frm, "--to", to],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=900)
        out = r.stdout.strip()
        try:
            return json.loads(out[out.index("{"):]), time.time() - t0
        except (ValueError, json.JSONDecodeError):
            return {"ok": False, "error": out[-800:] + r.stderr[-800:]}, time.time() - t0
    import engine
    return engine.safe_call(engine.export, frm, to, dest), time.time() - t0


def snapshot(root):
    s = {}
    for dp, _, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, root).replace("\\", "/")
            try:
                s[rel] = md5(p)
            except OSError:
                s[rel] = "ERR"
    return s


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else None
    os.makedirs(DEST, exist_ok=True)
    print("入口:", exe or "源码 engine", flush=True)
    print("区间:", FRM, "~", TO, "（起止含当天）", flush=True)

    r, sec = do_export(exe, DEST)
    check("导出返回 ok", r.get("ok") is True, r.get("error", "") + f" 耗时 {sec:.1f}s")
    if not r.get("ok"):
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 1
    batch = r["batch_path"]
    check("批次目录存在", os.path.isdir(batch), r.get("batch", ""))
    check("批次名带日期区间", FRM in r["batch"] and TO in r["batch"], r.get("batch", ""))
    # V0.1.0 起改为「按发送人分文件夹 + 缩略图用 _缩略 后缀」，不再有 _完整/_缩略图/_头部缺失 三个固定目录
    subdirs = [d for d in os.listdir(batch)
               if os.path.isdir(os.path.join(batch, d)) and not d.startswith("_")]
    check("按发送人建了子目录", bool(subdirs), f"{len(subdirs)} 个：{subdirs[:3]}")
    thumb_files = [f for _dp, _dn, fs in os.walk(batch) for f in fs if "_缩略" in f]
    check("缩略图用 _缩略 后缀", all("_缩略" in f for f in thumb_files),
          f"{len(thumb_files)} 个缩略图")

    rp = r["report"]
    check("报告存在", os.path.isfile(rp), os.path.basename(rp))
    check("仅保留《导出报告.md》且无冗余 CSV",
          os.path.basename(rp) == "导出报告.md"
          and not os.path.exists(os.path.join(batch, "_导出报告.csv"))
          and not os.path.exists(os.path.join(batch, "_导出清单.csv")),
          str(sorted(os.listdir(batch))))
    state_p = os.path.join(batch, "_补导状态.json")
    has_state = os.path.isfile(state_p)
    hidden_ok = True
    if has_state and os.name == "nt":
        attrs = ctypes.windll.kernel32.GetFileAttributesW(state_p)
        hidden_ok = bool(attrs != -1 and (attrs & 0x02))
    check("_补导状态.json 存在且隐藏", has_state and hidden_ok,
          os.path.basename(state_p))

    import engine
    rows = engine.read_batch_rows(batch)
    check("对账行数 == total", len(rows) == r.get("total"), f"{len(rows)} vs {r.get('total')}")

    full = [x for x in rows if x.get("状态") == "完整"]
    # 「缓存还原」件的 fullmd5 记的是微信 msg/attach 缓存块的 md5，不是原图 md5，
    # 按 md5 校验必然不符 —— 它们单独验「能解码成图片」。其余完整件必须逐字节吻合。
    restored = [x for x in full if (x.get("还原") or "") == "缓存还原"]
    plain = [x for x in full if (x.get("还原") or "") != "缓存还原"]
    bad = []
    for x in plain:
        p = x.get("输出路径", "")
        if not p or not os.path.isfile(p):
            bad.append((x.get("序号"), "缺文件"))
        elif md5(p).lower() != (x.get("fullmd5") or "").lower():
            bad.append((x.get("序号"), "md5 不符"))
    check("完整件逐条 md5 == fullmd5", not bad and len(plain) > 0,
          f"完整 {len(plain)} 件 · 不符 {len(bad)} {bad[:3]}")

    bad_cache = []
    for x in restored:
        p = x.get("输出路径", "")
        if not p or not os.path.isfile(p) or os.path.getsize(p) < 1024:
            bad_cache.append((x.get("序号"), "缺文件"))
            continue
        with open(p, "rb") as f:
            head = f.read(8)
        if not (head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n"):
            bad_cache.append((x.get("序号"), "还原后不是图片"))
    check("缓存还原件全部是有效图片", not bad_cache,
          f"还原 {len(restored)} 件 · 异常 {len(bad_cache)} {bad_cache[:3]}")

    written = [x for x in rows if x.get("输出路径")]
    check("落盘件数 == written", len(written) == r.get("written"),
          f"{len(written)} vs {r.get('written')}")

    before = snapshot(batch)
    # 第二次导出：必须新建批次目录，且第一批一个字节都不能变
    r2, sec2 = do_export(exe, DEST)
    check("第二次导出 ok", r2.get("ok") is True, f"耗时 {sec2:.1f}s")
    check("绝不复用旧批次", r2.get("batch_path") != batch,
          f"{os.path.basename(batch)} -> {r2.get('batch')}")
    after = snapshot(batch)
    check("第一批 0 删 0 改", before == after,
          f"动前 {len(before)} · 动后 {len(after)}")

    failed = [n for n, ok in results if not ok]
    print(f"\n合计 {len(results)} 项 · 失败 {len(failed)}", flush=True)
    print("导出目录:", DEST, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
