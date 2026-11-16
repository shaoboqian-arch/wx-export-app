# -*- coding: utf-8 -*-
"""启动冻结 exe 的 GUI，截取窗口并核对标题/尺寸（源码态也能跑）。

用法:
    python tests/verify_gui.py "<exe 绝对路径>"
    python tests/verify_gui.py                 # 源码 app.py

截图落在 验证输出/GUI-截图.png；程序在截图后被终止，不留后台进程。
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SRC = os.path.join(APP, "src")
OUT = os.path.join(APP, "验证输出")
os.makedirs(OUT, exist_ok=True)

user32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.SetForegroundWindow.argtypes = [wt.HWND]


def all_windows():
    """PyInstaller onefile 会派生子进程，窗口不属于父进程 pid，故按标题定位。"""
    found = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        found.append((hwnd, buf.value, (r.left, r.top, r.right, r.bottom)))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else None
    shot = sys.argv[2] if len(sys.argv) > 2 else "GUI-截图.png"
    cmd = [exe] if exe else [sys.executable, os.path.join(SRC, "app.py")]
    before = {w[0] for w in all_windows() if "微信收藏导出" in w[1]}
    # onefile 会派生子进程并继承管道；用 DEVNULL 避免父进程退出后读管道卡死
    p = subprocess.Popen(cmd, cwd=SRC, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # Tk 根窗口会先以约 200x200 的默认尺寸出现，之后才应用 geometry。
        # 必须等尺寸稳定且达到主窗口宽度，否则会把创建初期的小窗当成成品（假阳性）。
        target = None
        prev, stable = None, 0
        for _ in range(60):
            time.sleep(0.25)
            hits = [w for w in all_windows()
                    if "微信收藏导出" in w[1] and w[0] not in before
                    and (w[2][2] - w[2][0]) >= 500 and (w[2][3] - w[2][1]) >= 180]
            if not hits:
                continue
            rect = hits[0][2]
            stable = stable + 1 if prev == rect else 0
            prev = rect
            if stable >= 2:
                target = hits[0]
                break
        if not target:
            print("FAIL 未找到尺寸稳定的主窗口")
            print("候选窗口:", [(w[1], w[2]) for w in all_windows() if w[1]][:12])
            return 1
        hwnd, title, rect = target
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        print(f"PASS 窗口标题 : {title}")
        if w < 500 or h < 180:
            print(f"FAIL 窗口尺寸异常 : {w} x {h}")
            return 1
        print(f"PASS 窗口尺寸 : {w} x {h}")
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.5)
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab(bbox=rect, all_screens=True)
            path = os.path.join(OUT, shot)
            img.save(path)
            print(f"PASS 截图     : {path}  ({img.width}x{img.height})")
        except Exception as e:
            print(f"WARN 截图失败（不影响窗口验证）: {e}")
        return 0
    finally:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


if __name__ == "__main__":
    sys.exit(main())
