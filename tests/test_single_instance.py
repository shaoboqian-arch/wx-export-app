# -*- coding: utf-8 -*-
"""主界面单实例 + 重复启动提示的回归钉。

行为面：命名互斥体第二次获取必须失败（同进程内即可复现）；
提示窗用海岛控件构建、不用原生 messagebox。
守卫的接线位置（gui.run 之前、--setup 之后）用源码断言钉住。
"""
import ctypes
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SRC = os.path.join(APP, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import tkinter as tk  # noqa: E402
from ui_kit import apply_island_style, fonts  # noqa: E402


def read_src(name):
    with io.open(os.path.join(SRC, name), encoding="utf-8") as f:
        return f.read()


class SingleInstanceTests(unittest.TestCase):
    def test_second_acquire_fails_in_process(self):
        """同一进程连续获取：第一次成功，第二次必须失败——这是「重复启动弹提示」的判定源。"""
        if os.name != "nt":
            self.skipTest("仅 Windows")
        import app
        self.assertTrue(app.acquire_gui_mutex())
        try:
            self.assertFalse(app.acquire_gui_mutex())
        finally:
            if app._mutex_handle:
                ctypes.windll.kernel32.CloseHandle(app._mutex_handle)
                app._mutex_handle = None

    def test_guard_wired_before_gui_run(self):
        """守卫必须在 gui.run() 之前、安装向导分支之后——安装器不受单实例限制。"""
        src = read_src("app.py")
        guard_pos = src.find("if not acquire_gui_mutex():")
        self.assertGreater(guard_pos, 0, "app.py 应接单实例守卫")
        setup_pos = src.find('"--setup" in argv')
        self.assertGreater(guard_pos, setup_pos, "守卫应晚于安装向导分支")
        run_pos = src.find("gui.run()")
        self.assertGreater(run_pos, guard_pos, "gui.run() 应在守卫之后")
        self.assertIn("gui.show_already_running()", src, "重复启动应走海岛提示")

    def test_dialog_is_island_not_native(self):
        """提示窗必须用海岛控件（CapsuleButton 主按钮），不得退回原生 messagebox。"""
        src = read_src("gui.py")
        self.assertIn("def show_already_running()", src)
        builder = src[src.find("def _build_running_dialog"):src.find("def show_already_running")]
        self.assertIn("CapsuleButton", builder, "提示窗应用海岛主按钮")
        self.assertNotIn("messagebox", builder, "提示窗不得用原生弹窗")
        self.assertIn("bind(\"<Escape>\"", builder, "Esc 应可关闭，与主窗一致")

    def test_dialog_constructs(self):
        """内容区能在测试里构建：控件齐全、主按钮可触发。"""
        root = tk.Tk()
        root.withdraw()
        apply_island_style(root)
        try:
            fm, fs, _ = fonts(root)
            import gui
            btn = gui._build_running_dialog(root, fm, fs)
            root.update_idletasks()
            self.assertEqual(btn.cget("text"), "知道了")
            labels = [w for w in root.winfo_children()[0].winfo_children()
                      if isinstance(w, tk.Label)]
            self.assertEqual(len(labels), 2, "应有标题与说明两行文字")
        finally:
            try:
                root.destroy()
            except tk.TclError:
                pass


if __name__ == "__main__":
    unittest.main()
