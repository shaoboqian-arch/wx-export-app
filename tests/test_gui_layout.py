# -*- coding: utf-8 -*-
"""主界面布局测试。会创建短暂的 Tk 窗口，不读密钥、不导出。"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SRC = os.path.join(APP, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import tkinter as tk  # noqa: E402
import gui  # noqa: E402
from ui_kit import apply_island_style, fonts  # noqa: E402


class GuiLayoutTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        apply_island_style(self.root)
        fm, fs, family = fonts(self.root)
        self.app = gui.App(self.root, fm, fs, family)
        self.root.update_idletasks()

    def tearDown(self):
        try:
            self.app.close()
        except Exception:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def test_window_is_resizable(self):
        rw, rh = self.root.resizable()
        self.assertTrue(rw and rh)

    def test_settings_shares_export_row(self):
        export = self.app.btn.grid_info()
        settings = self.app.settings_btn.grid_info()
        browse = self.app.browse_btn.grid_info()
        self.assertEqual(int(export["row"]), 2)
        self.assertEqual(int(settings["row"]), 2)
        self.assertEqual(int(settings["column"]), int(browse["column"]))
        self.assertEqual(int(export["columnspan"]), 2)
        # 设置已离开状态行
        for child in self.app.state.master.winfo_children():
            text = child.cget("text") if "text" in child.keys() else ""
            self.assertNotEqual(text, "设置")

    def test_date_row_spans_to_browse_column(self):
        info = self.app.date_row.grid_info()
        self.assertEqual(int(info["columnspan"]), 2)
        self.assertIn("e", str(info["sticky"]))
        self.assertIn("w", str(info["sticky"]))
        self.assertEqual(int(self.app.date_row.grid_slaves(row=0, column=0)[0].grid_info()["column"]), 0)

    def test_open_folder_label_no_collapse_and_empty_state(self):
        self.assertEqual(self.app.open_btn.cget("text"), "打开文件夹")

        def collect_texts(widget):
            out = []
            for ch in widget.winfo_children():
                try:
                    t = ch.cget("text")
                    if isinstance(t, str):
                        out.append(t)
                except Exception:
                    pass
                out.extend(collect_texts(ch))
            return out

        self.assertNotIn("收起", collect_texts(self.app.panel))
        self.app.show_panel()
        self.app.failed = []
        self.app.pack_failed = []
        self.app.refresh_table()
        self.root.update()
        self.assertEqual(self.app.tree.winfo_manager(), "")
        self.assertEqual(self.app.fail_empty.winfo_manager(), "grid")
        self.app.failed = [{"display": "2026-11-20 视频：测试", "reason": "未定位本体"}]
        self.app.refresh_table()
        self.root.update()
        self.assertEqual(self.app.tree.winfo_manager(), "grid")
        self.assertEqual(self.app.fail_empty.winfo_manager(), "")


if __name__ == "__main__":
    unittest.main()
