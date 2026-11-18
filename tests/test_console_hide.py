# -*- coding: utf-8 -*-
"""MCP 控制台黑窗整改的回归钉：--mcp 分支隐藏独占控制台、共享终端不误藏、测试连接不闪窗。

行为面（协议不受窗口隐藏影响）由 verify_matrix.py 的 MCP 协议边界项兜住，
这里用源码断言钉住三个调用点，防止回潮。
"""
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SRC = os.path.join(APP, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


def read_src(name):
    with io.open(os.path.join(SRC, name), encoding="utf-8") as f:
        return f.read()


class ConsoleHideTests(unittest.TestCase):
    def test_mcp_branch_hides_console(self):
        """--mcp 分支必须在 serve() 之前调 hide_console()，否则连接器拉起的
        实例全程顶着黑窗挂在任务栏（app.py 的 GUI 分支早已如此，--mcp 曾漏）。"""
        src = read_src("app.py")
        mcp_pos = src.find('"--mcp" in argv')
        self.assertGreater(mcp_pos, 0, "--mcp 分支应存在")
        branch = src[mcp_pos:src.find("\n    if ", mcp_pos)]
        self.assertIn("hide_console()", branch, "--mcp 分支应调 hide_console()")
        self.assertLess(branch.index("hide_console()"), branch.index("mcp_server.serve()"),
                        "应在 serve() 之前隐藏")

    def test_hide_console_only_hides_exclusive_console(self):
        """hide_console 必须有独占守卫：从终端手动启动时控制台与父终端共享，
        无脑 ShowWindow 会把用户自己的终端藏掉。"""
        src = read_src("app.py")
        self.assertIn("GetConsoleProcessList", src, "应有独占性检查")
        self.assertIn("n <= 1", src, "共享控制台（进程数 > 1）不应隐藏")

    def test_test_connection_no_window(self):
        """设置页「测试连接」的子进程要带 CREATE_NO_WINDOW，不闪多余控制台。"""
        src = read_src("mcp_config.py")
        self.assertIn("CREATE_NO_WINDOW", src, "Popen 应带 CREATE_NO_WINDOW")
        self.assertIn("creationflags=flags", src, "Popen 应把 flags 传下去")


if __name__ == "__main__":
    unittest.main()
