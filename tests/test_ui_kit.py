# -*- coding: utf-8 -*-
"""海岛控件冒烟测试。会创建短暂的 Tk 窗口，不读密钥、不导出。"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SRC = os.path.join(APP, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import tkinter as tk  # noqa: E402
from ui_kit import (  # noqa: E402
    CapsuleButton, IslandEntry, IslandBar, PhaseBadge, apply_island_style, fonts,
)


class UiKitTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        apply_island_style(self.root)

    def tearDown(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def test_capsule_button_constructs(self):
        btn = CapsuleButton(self.root, text="浏览…", width=84, height=36)
        self.root.update_idletasks()
        self.assertEqual(btn.cget("text"), "浏览…")
        btn.configure(state="disabled", text="导出中…")
        self.root.update_idletasks()
        self.assertEqual(btn.cget("state"), "disabled")
        self.assertEqual(btn.cget("text"), "导出中…")

    def test_other_widgets_construct(self):
        fm, fs, _family = fonts(self.root)
        IslandEntry(self.root, font=fm, width=12)
        PhaseBadge(self.root, text="读取", font=fs, width=70, height=24)
        bar = IslandBar(self.root, height=10)
        bar.set_progress(3, 10)
        self.root.update_idletasks()

    def test_capsule_button_keyboard(self):
        """自绘按钮必须能 Tab 到、能用空格/回车触发、focus 时画焦点环。"""
        hits = []
        btn = CapsuleButton(self.root, text="浏览…", command=lambda: hits.append(1),
                            width=84, height=36)
        self.root.update_idletasks()
        self.assertTrue(str(btn.cget("takefocus")) not in ("", "0", "False"))
        self.assertIn("<Key-space>", btn.bind())
        self.assertIn("<Key-Return>", btn.bind())
        self.root.update_idletasks()
        before = len(btn.find_all())
        # 窗口是 withdraw 的，focus_set() 不会派发 FocusIn；直接调处理函数验效果
        btn._on_focus_in()
        self.assertTrue(btn._focus)
        self.assertGreater(len(btn.find_all()), before, "focus 时应多画一个焦点环")
        btn._on_key()
        self.root.update_idletasks()
        self.assertEqual(hits, [1])
        btn._on_focus_out()
        self.assertFalse(btn._focus)
        btn.configure(state="disabled")
        btn._on_key()
        self.assertEqual(hits, [1], "禁用后空格不应再触发")

    def test_muted_and_primary_contrast(self):
        """配色回归：辅助字与主按钮文字都要过 WCAG AA（4.5:1）。"""
        import ui_kit as uk

        def lum(h):
            h = h.lstrip("#")
            r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

            def f(c):
                return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

        def ratio(a, b):
            la, lb = lum(a), lum(b)
            hi, lo = max(la, lb), min(la, lb)
            return (hi + 0.05) / (lo + 0.05)

        self.assertGreaterEqual(ratio(uk.MUTED, uk.BG), 4.5, "MUTED on BG")
        self.assertGreaterEqual(ratio(uk.MUTED, uk.SAND), 4.5, "MUTED on SAND")
        self.assertGreaterEqual(ratio(uk.ON_MINT, uk.MINT), 4.5, "主按钮文字")
        self.assertGreaterEqual(ratio(uk.ON_MINT, uk.MINT_HI), 4.5, "主按钮悬停文字")
        self.assertGreaterEqual(ratio(uk.ERR, uk.BG), 4.5, "错误红")

    def test_two_date_entries_side_by_side(self):
        fm, _fs, _family = fonts(self.root)
        holder = tk.Frame(self.root)
        holder.pack()
        a = IslandEntry(holder, font=fm, width=10, pxwidth=160)
        b = IslandEntry(holder, font=fm, width=10, pxwidth=160)
        a.pack(side="left")
        b.pack(side="left", padx=(8, 0))
        a.insert(0, "2026-11-01")
        b.insert(0, "2026-11-16")
        self.root.update()
        self.assertEqual(a.get(), "2026-11-01")
        self.assertEqual(b.get(), "2026-11-16")
        self.assertGreaterEqual(a.winfo_reqwidth(), 150)
        self.assertGreaterEqual(b.winfo_reqwidth(), 150)
        self.assertLessEqual(abs(a.winfo_reqwidth() - b.winfo_reqwidth()), 4)

    def test_two_date_entries_expand_equally(self):
        fm, _fs, _family = fonts(self.root)
        holder = tk.Frame(self.root)
        holder.columnconfigure(0, weight=1)
        holder.columnconfigure(1, weight=1)
        a = IslandEntry(holder, font=fm, width=10, pxwidth=160)
        b = IslandEntry(holder, font=fm, width=10, pxwidth=160)
        a.grid(row=0, column=0, sticky="ew")
        b.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.root.update_idletasks()
        self.assertIn("ew", "".join(a.grid_info()["sticky"]))
        self.assertIn("ew", "".join(b.grid_info()["sticky"]))
        self.assertGreaterEqual(a.winfo_reqwidth(), 150)
        self.assertLessEqual(abs(a.winfo_reqwidth() - b.winfo_reqwidth()), 4)


if __name__ == "__main__":
    unittest.main()
