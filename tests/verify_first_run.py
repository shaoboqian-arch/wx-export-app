# -*- coding: utf-8 -*-
"""主界面首启形态验证：环境未就绪时主按钮应为「初始化本机环境」。

用法：
    python tests/verify_first_run.py ready     # 本机正常环境
    python tests/verify_first_run.py empty     # 空 USERPROFILE，模拟陌生电脑
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

import gui                      # noqa: E402
from ui_kit import apply_island_style, enable_dpi, fonts  # noqa: E402

OUT = os.path.join(APP, "验证输出")
os.makedirs(OUT, exist_ok=True)
MODE = sys.argv[1] if len(sys.argv) > 1 else "ready"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail else ""))


def shot(win, name):
    from PIL import ImageGrab
    win.attributes("-topmost", True)
    win.lift()
    win.update()
    time.sleep(0.35)
    win.update()
    x, y = win.winfo_rootx(), win.winfo_rooty()
    w, h = win.winfo_width(), win.winfo_height()
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
    path = os.path.join(OUT, name)
    img.save(path)
    win.attributes("-topmost", False)
    return path


def main():
    # empty 模式靠「改 USERPROFILE 指向空目录」来模拟陌生电脑；若忘了改，
    # 本机环境本来就是就绪的，断言必然失败 —— 那不是回归，先明确拒绝跑。
    if MODE == "empty" and os.path.isdir(os.path.join(os.path.expanduser("~"), ".wechat-cli")):
        print("拒绝执行：HOME 仍是本机真实目录（~/.wechat-cli 存在）。\n"
              "empty 模式需要空环境，例如：\n"
              '  T=$(mktemp -d); USERPROFILE="$T" HOME="$T" python tests/verify_first_run.py empty')
        return 2
    enable_dpi()
    root = tk.Tk()
    apply_island_style(root)
    fm, fs, fam = fonts(root)
    app = gui.App(root, fm, fs, fam)

    deadline = time.time() + 20
    while time.time() < deadline:
        root.update()
        if app.btn.cget("text") == "初始化本机环境":
            break
        if app.env_ready:
            break
        time.sleep(0.1)
    root.update()
    time.sleep(0.3)
    root.update()

    text = app.btn.cget("text")
    geo = (root.winfo_width(), root.winfo_height())
    if MODE == "empty":
        check("未就绪时主按钮为初始化", text == "初始化本机环境", text)
        check("环境提示已出现", bool(app.env_message), (app.env_message or "")[:60])
    else:
        check("就绪时主按钮为导出", text == "导出", text)
        check("无环境警告", not app.env_message, app.env_message)
    check("主界面尺寸", geo[0] >= 600, f"{geo[0]}x{geo[1]}")

    p = shot(root, f"首启主界面-{MODE}.png")
    check("截图已保存", os.path.isfile(p), os.path.basename(p))

    if MODE == "empty":
        app.open_setup_env()
        root.update()
        time.sleep(0.5)
        root.update()
        tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        check("初始化窗口可打开", len(tops) == 1, f"{len(tops)} 个 Toplevel")
        if tops:
            p2 = shot(tops[0], f"首启初始化窗口-{MODE}.png")
            check("初始化窗口截图", os.path.isfile(p2), os.path.basename(p2))
            tops[0].destroy()

    root.destroy()
    ok = sum(1 for _n, _o, _d in results if _o)
    print(f"\n{ok}/{len(results)} PASS")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
