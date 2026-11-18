# -*- coding: utf-8 -*-
"""初始化窗口验证：布局、状态联动、截图。只读，不改用户配置。

用法：python tests/verify_setup_env.py [标签]
"""
import os
import sys
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
for p in (os.path.join(APP, "src"), os.path.join(APP, "src", "core"),
          os.path.join(APP, "build", "vendor")):
    if p not in sys.path:
        sys.path.insert(0, p)

import engine          # noqa: E402
import setup_env       # noqa: E402
from ui_kit import fonts, enable_dpi  # noqa: E402

enable_dpi()

OUT = os.path.join(APP, "验证输出")
os.makedirs(OUT, exist_ok=True)
TAG = sys.argv[1] if len(sys.argv) > 1 else "本轮"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail else ""))


def shot(win, name):
    import ctypes
    from PIL import ImageGrab
    win.attributes("-topmost", True)
    win.lift()
    win.update()
    time.sleep(0.3)
    win.update()
    hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
    w, h = win.winfo_width(), win.winfo_height()
    try:
        img = ImageGrab.grab(window=hwnd)
    except Exception:
        x, y = win.winfo_rootx(), win.winfo_rooty()
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
    path = os.path.join(OUT, name)
    img.save(path)
    win.attributes("-topmost", False)
    return path, (w, h)


def main():
    t0 = time.time()
    root = tk.Tk()
    root.geometry("1x1+-3000+-3000")
    win = tk.Toplevel(root)
    fm, fs, _fam = fonts(win)
    ui = setup_env.SetupEnvUI(win, fm, fs)

    # 等根目录扫描与首轮状态回来
    deadline = time.time() + 20
    while time.time() < deadline:
        win.update()
        if ui.roots and ui.db_state.cget("text") not in ("检查中…", ""):
            break
        time.sleep(0.1)
    win.update()
    time.sleep(0.4)
    win.update()

    geo = (win.winfo_width(), win.winfo_height())
    check("窗口尺寸 640x350", geo == (640, 350), f"{geo[0]}x{geo[1]}")
    check("账号下拉已填充", len(ui.roots) > 0, f"{len(ui.roots)} 个候选")
    check("账号下拉有选中项", ui.combo.current() >= 0, f"index={ui.combo.current()}")

    probe = engine.probe_env(ui.current_root() or None)
    db_ready = bool(probe.get("db_keys"))
    img_ready = bool(probe.get("image_key"))
    check("数据库密钥状态与实际一致",
          (ui.db_state.cget("text") == "已就绪") == db_ready,
          ui.db_state.cget("text"))
    check("图片密钥状态与实际一致",
          (ui.img_state.cget("text") == "已就绪") == img_ready,
          ui.img_state.cget("text"))
    check("主按钮文案随状态", ui.db_btn.cget("text") in ("提取密钥", "重新提取"),
          ui.db_btn.cget("text"))
    check("图片按钮文案随状态", ui.img_btn.cget("text") in ("开始检测", "重新检测"),
          ui.img_btn.cget("text"))
    check("手动填写区默认隐藏", not ui.manual.winfo_ismapped())

    path, size = shot(win, f"初始化界面-{TAG}.png")
    check("截图已保存", os.path.isfile(path), f"{os.path.basename(path)} {size[0]}x{size[1]}")

    # 展开手动填写，确认布局不溢出
    ui.toggle_manual()
    win.update()
    check("手动填写区可展开", ui.manual.winfo_ismapped())
    path2, _ = shot(win, f"初始化界面-手动填写-{TAG}.png")
    check("手动填写截图已保存", os.path.isfile(path2), os.path.basename(path2))

    win.destroy()
    root.destroy()

    ok = sum(1 for _n, _o, _d in results if _o)
    print(f"\n{ok}/{len(results)} PASS   耗时 {time.time() - t0:.1f}s")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
