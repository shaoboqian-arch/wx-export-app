# -*- coding: utf-8 -*-
"""界面布局复检（源码态直接构造窗口，能测到跨进程看不到的内部状态）。

检查：居中、尺寸、字体回退、图标、常驻控件数量、长文本不裁切、
进度条出现时状态行仍在窗口内、快捷键绑定；并输出常态 / 长文本 / 安装界面三张截图。

用法: python tests/verify_ui.py [轮次名]
"""
import datetime as dt
import json
import os
import sys
import tkinter as tk
from tkinter import ttk

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SRC = os.path.join(APP, "src")
VENDOR = os.path.join(APP, "build", "vendor")
for p in (VENDOR, SRC):
    if p not in sys.path:
        sys.path.insert(0, p)
from common import VERSION  # noqa: E402

import gui  # noqa: E402
import installer  # noqa: E402
from ui_kit import fonts, icon_path  # noqa: E402
from verify_gui import all_windows  # noqa: E402

OUT = os.path.join(APP, "验证输出")
os.makedirs(OUT, exist_ok=True)
ROUND = sys.argv[1] if len(sys.argv) > 1 else "轮次2"
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append({"name": name, "ok": bool(cond), "detail": str(detail)})
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail else ""), flush=True)


def shot(name):
    import ctypes
    import time
    from PIL import ImageGrab
    # 刚创建的窗口 DWM 还没合成完，立即截图会拍到半透明底；等一下并置前
    time.sleep(0.45)
    hits = [w for w in all_windows() if "微信收藏导出" in w[1] and w[2][2] > w[2][0]]
    if not hits:
        check(f"截图 {name} 找到窗口", False)
        return
    try:
        ctypes.windll.user32.SetForegroundWindow(hits[0][0])
    except Exception:
        pass
    time.sleep(0.3)
    rect = hits[0][2]
    img = ImageGrab.grab(bbox=rect, all_screens=True)
    path = os.path.join(OUT, name)
    img.save(path)
    check(f"截图 {name}", True, f"{img.width}x{img.height}")


def main():
    print(f"=== 界面复检 {ROUND} · {dt.datetime.now():%Y-%m-%d %H:%M:%S} ===", flush=True)
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    fm, fs, family = fonts(root)
    app = gui.App(root, fm, fs, family)
    root.update_idletasks()
    root.update()

    check("窗口标题含版本", "微信收藏导出" in root.title() and VERSION in root.title(), root.title())
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    x, y = root.winfo_x(), root.winfo_y()
    check("窗口水平居中", abs(x - (sw - gui.W) // 2) <= 4, f"x={x} 期望≈{(sw-gui.W)//2}")
    check("窗口尺寸稳定", root.winfo_width() == gui.W and root.winfo_height() == gui.BASE_H,
          f"{root.winfo_width()}x{root.winfo_height()}")
    from tkinter import font as tkfont
    check("字体在本机可用", family == "TkDefaultFont" or family in tkfont.families(root), family)
    check("图标文件存在", os.path.isfile(icon_path()), icon_path())

    check("进度条默认隐藏", not app.prog.winfo_ismapped())
    check("打开按钮默认隐藏", not app.open_btn.winfo_ismapped())
    check("环境提示默认隐藏", not app.info.winfo_ismapped())

    long_text = "导出失败：缺少数据库密钥 C:/Users/用户/.wechat-cli/all_keys.json；请先运行 extract_keys.py 取密钥，然后重新启动本程序，再选择目标文件夹与日期范围重试。"
    app.set_state(long_text, "#b42318")
    root.update()
    check("长文本显示被截断", len(app.state.cget("text")) <= 76,
          f"显示 {len(app.state.cget('text'))} 字")
    check("长文本完整内容进 tooltip", app.full_state == long_text)
    check("状态行不超过两行", app.state.winfo_reqheight() <= 40,
          f"reqheight={app.state.winfo_reqheight()}")
    root_y = root.winfo_rooty() + root.winfo_height()
    state_bottom = app.state.winfo_rooty() + app.state.winfo_reqheight()
    check("长文本不被窗口裁切", state_bottom <= root_y + 2,
          f"状态底 {state_bottom} / 窗口底 {root_y}")
    shot(f"界面-长文本-{ROUND}.png")

    # 模拟导出中：进度条 + 进度表展开，状态行仍要在窗口内
    app.busy = True
    app.prog.grid()
    app.prog.start(14)
    app.show_panel()
    app.set_phase("export")
    app.set_state("正在还原并写出文件…", "#b45309")
    root.update()
    root_y = root.winfo_rooty() + root.winfo_height()
    state_bottom = app.state.winfo_rooty() + app.state.winfo_reqheight()
    check("导出中状态行不被裁切", state_bottom <= root_y + 2,
          f"状态底 {state_bottom} / 窗口底 {root_y}")
    check("导出中进度条可见", bool(app.prog.winfo_ismapped()))
    check("进度表已展开", bool(app.panel.winfo_ismapped()))
    check("阶段指示四项", len(app.phase_labels) == 4)
    shot(f"界面-导出中-{ROUND}.png")

    app.prog.stop()
    app.prog.grid_remove()
    app.busy = False
    app.hide_panel()
    app.set_state("完成 · 完整 6 · 缩略图 0 · 未定位 0 · export-2026-11-15_2026-11-15-test", "#1a7f37")
    app.open_btn.grid()
    root.update()
    check("完成后打开按钮可用", bool(app.open_btn.winfo_ismapped()))
    shot(f"界面-常态-{ROUND}.png")

    # 用含失败项的本地复检区间渲染进度表；换机器时按本机数据调整
    import engine
    dest = os.path.join(OUT, "进度表验证")
    os.makedirs(dest, exist_ok=True)
    r = engine.safe_call(engine.export, "2016-08-11", "2016-08-11", dest)
    check("真实失败区间导出成功", r.get("ok") is True, r.get("error", ""))
    if r.get("ok"):
        app.stats = r["by_kind"]
        app.failed = r["failed_items"]
        c = r.get("counts", {})
        app.set_state(
            f"完成 · 完整 {c.get('完整', 0)} · 缩略图 {c.get('本地仅缩略图', 0)} · "
            f"未定位 {c.get('未定位本体', 0)} · {r['batch']}", "#1a7f37")
        app.set_phase("done")
        app.show_panel()
        app.dirty = True
        app.refresh_table()
        app.busy = False
        app.resize()
        root.update()
        check("失败清单渲染条数", len(app.tree.get_children()) == r["failed_count"],
              f"树 {len(app.tree.get_children())} / 结果 {r['failed_count']}")
        check("分类文件数渲染",
              app.cell[("video", "failed")].cget("text") == str(r["by_kind"]["video"]["failed"])
              and app.cell[("video", "total")].cget("text") == str(r["by_kind"]["video"]["total"]),
              str(r["by_kind"]))
        check("失败标题带数量", str(r["failed_count"]) in app.fail_title.cget("text"),
              app.fail_title.cget("text"))
        shot(f"界面-进度表-{ROUND}.png")

    check("回车绑定导出", bool(root.bind("<Return>")))
    check("关闭协议已接管", bool(root.protocol("WM_DELETE_WINDOW")))
    app.close()   # 走正常关闭路径，验证定时器被取消、无残留回调

    # 安装界面
    root2 = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    fm2, fs2, family2 = fonts(root2)
    ui = installer.SetupUI(root2, fm2, fs2, family2, no_shortcut=True)
    root2.update_idletasks()
    root2.update()
    check("安装窗口标题", "安装 微信收藏导出" in root2.title(), root2.title())
    check("安装窗口水平居中", abs(root2.winfo_x() - (root2.winfo_screenwidth() - 600) // 2) <= 4,
          f"x={root2.winfo_x()}")
    check("安装按钮绑定回车", bool(root2.bind("<Return>")))
    shot(f"安装界面-{ROUND}.png")
    root2.destroy()

    path = os.path.join(OUT, f"界面复检-{ROUND}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"round": ROUND, "time": dt.datetime.now().isoformat(),
                   "items": RESULTS}, f, ensure_ascii=False, indent=2)
    fails = [x for x in RESULTS if not x["ok"]]
    print(f"\n合计 {len(RESULTS)} 项 · FAIL {len(fails)}", flush=True)
    for x in fails:
        print(f"  - {x['name']}: {x['detail']}", flush=True)
    print("结果文件:", path, flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
