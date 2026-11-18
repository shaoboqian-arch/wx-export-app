# -*- coding: utf-8 -*-
"""微信收藏导出 · 单一入口

    （双击）              主界面：选目标文件夹 + 日期范围 → 导出
    Setup.exe / --setup   安装向导
    --mcp                 MCP stdio 服务端（供其他 WorkBuddy 接入）
    --cli --preflight     只读预检（输出 JSON）
    --cli --export        命令行导出（输出 JSON）
    --install             静默安装/覆盖升级：写文件 + 快捷方式 + 卸载项（[--dir 目标目录]）
    --install-headless    构建验证用：静默释放到指定目录，不写快捷方式/注册表
    --uninstall           卸载快捷方式与注册表项（不动导出数据）
    --version             版本号
"""
import argparse
import ctypes
import json
import os
import sys

import engine
from common import VERSION


def hide_console():
    """隐藏本进程的控制台窗口。

    只在控制台被本进程**独占**时才隐藏：从已有终端手动启动时（--mcp 调试、
    终端里跑 GUI），控制台是与父终端共享的，隐藏它等于把用户的终端藏掉。
    GetConsoleProcessList 返回挂在该控制台上的进程数，独占时为 1。
    """
    if os.name != "nt":
        return
    try:
        h = ctypes.windll.kernel32.GetConsoleWindow()
        if not h:
            return
        buf = (ctypes.c_uint * 8)()
        n = ctypes.windll.kernel32.GetConsoleProcessList(buf, 8)
        if n <= 1:
            ctypes.windll.user32.ShowWindow(h, 0)
    except Exception:
        pass


_SINGLE_INSTANCE_MUTEX = "Local\\wx-fav-export-gui-singleton"
_mutex_handle = None


def acquire_gui_mutex():
    """主界面单实例守卫：已有主窗口时返回 False（调 gui.show_already_running() 弹提示）。

    命名互斥体按登录会话隔离（Local\\ 前缀）。**只管主界面**：--mcp 与 --cli 不参与——
    连接器会话、命令行导出与主界面共存是合法场景（README 有对应用法）。
    互斥体 API 异常时按可启动处理，不让守卫本身挡住主流程。
    """
    global _mutex_handle
    if os.name != "nt":
        return True
    try:
        error_already_exists = 183
        h = ctypes.windll.kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_MUTEX)
        if not h:
            return True
        if ctypes.windll.kernel32.GetLastError() == error_already_exists:
            # 句柄指向已存在的互斥体，用完即关；持有权归第一个实例，进程退出由系统回收。
            ctypes.windll.kernel32.CloseHandle(h)
            return False
        _mutex_handle = h
        return True
    except Exception:
        return True


def _ensure_streams():
    """窗口子系统构建下，双击启动没有控制台，stdin/stdout/stderr 可能为 None。

    CLI / MCP 走的是继承句柄（终端或管道），照常有效；这里只做兜底，
    让残留的 print / write 落到 devnull，而不是抛 AttributeError。
    """
    for name, mode in (("stdin", "r"), ("stdout", "w"), ("stderr", "w")):
        if getattr(sys, name, None) is not None:
            continue
        try:
            setattr(sys, name, open(os.devnull, mode, encoding="utf-8"))
        except OSError:
            pass


def out(obj):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cli(argv):
    ap = argparse.ArgumentParser(prog="wx-fav-export", add_help=True)
    ap.add_argument("--preflight", action="store_true", help="只读预检")
    ap.add_argument("--export", action="store_true", help="执行导出")
    ap.add_argument("--retry", action="store_true", help="对已有批次补导未取到原件的条目")
    ap.add_argument("--batch", default="", help="补导时的批次目录")
    ap.add_argument("--from", dest="frm", default="")
    ap.add_argument("--to", dest="to", default="")
    ap.add_argument("--dest", default="")
    ap.add_argument(
        "--kinds", default="",
        help="resolve_item 类型，逗号分隔：image,video,file,link,chat。link 是聊天记录打包（type=14），不是公众号文章或链接卡片。省略时与主界面相同：导出图片和视频文件，并把文本、链接卡片写入清单；指定该参数时只导出这些类型的文件，不自动附带文本和链接。")
    ap.add_argument("--root", default="")
    ap.add_argument("--image-key", default="")
    ap.add_argument("--setup-keys", action="store_true",
                    help="提取数据库密钥（初始化用，自动写 ~/.wechat-cli）")
    ap.add_argument("--capture-image-key", action="store_true",
                    help="捕获图片密钥（需先在微信里点开一张收藏图片）")
    a = ap.parse_args(argv)
    if a.setup_keys:
        root = a.root
        if not root:
            rs = engine.candidate_roots().get("roots") or []
            root = rs[0]["path"] if rs else ""
        if not root:
            out({"ok": False, "error": "未找到微信账号目录，请用 --root 指定"})
            return 1
        r = engine.setup_db_keys(os.path.join(root, "db_storage"))
        out(r)
        return 0 if r.get("ok") else 1
    if a.capture_image_key:
        r = engine.capture_image_key(a.root or None)
        out(r)
        return 0 if r.get("ok") else 1
    if a.preflight:
        out(engine.safe_call(engine.preflight, a.frm, a.to, a.kinds, a.root or None))
        return 0
    if a.export:
        r = engine.safe_call(engine.export, a.frm, a.to, a.dest, a.kinds,
                             a.root or None, a.image_key or None)
        out(r)
        return 0 if r.get("ok") else 1
    if a.retry:
        r = engine.safe_call(engine.retry_missing, a.batch,
                             a.root or None, a.image_key or None)
        out(r)
        return 0 if r.get("ok") else 1
    out({"ok": False, "error": "需要 --preflight、--export 或 --retry"})
    return 2


def main():
    _ensure_streams()
    argv = sys.argv[1:]
    if "--version" in argv:
        print(VERSION)
        return 0

    if "--mcp" in argv:
        # MCP 走 stdio 管道，控制台窗口只是 --console 构建的副产品；
        # 被连接器拉起时独占一个新控制台，藏掉它，任务栏不再挂黑窗。
        # 手动在终端里调试时控制台是共享的，hide_console 的独占守卫会放过它。
        hide_console()
        import mcp_server
        return mcp_server.serve()

    if "--install-headless" in argv:
        import installer
        ap = argparse.ArgumentParser()
        ap.add_argument("--dir", required=True)
        a, _ = ap.parse_known_args(argv)
        ok, msg, dest = installer.install(a.dir, no_shortcut=True)
        out({"ok": ok, "message": msg, "dir": dest})
        return 0 if ok else 1

    if "--install" in argv:
        # 静默安装 / 覆盖升级：与安装向导走同一条 installer.install()，只是不弹窗。
        # 用于「装到默认位置 + 建快捷方式 + 写卸载项」，也可 --dir 指定目录。
        import installer
        ap = argparse.ArgumentParser()
        ap.add_argument("--dir", default="")
        a, _ = ap.parse_known_args(argv)
        ok, msg, dest = installer.install(a.dir.strip() or installer.default_dir())
        out({"ok": ok, "message": msg, "dir": dest, "version": VERSION})
        return 0 if ok else 1

    if "--uninstall" in argv:
        import installer
        ok, msg = installer.uninstall()
        out({"ok": ok, "message": msg})
        return 0

    if "--cli" in argv:
        rest = [x for x in argv if x != "--cli"]
        return cli(rest)

    hide_console()
    from ui_kit import enable_dpi   # 延迟加载，MCP/CLI 模式不碰 tkinter
    enable_dpi()
    if "--settings" in argv:
        import settings
        return settings.open(None)
    basename = os.path.basename(sys.executable)
    if "--setup" in argv or "Setup" in basename or "安装" in basename:
        import installer
        return installer.run()
    # 主界面单实例：重复启动不再开第二个窗口，弹海岛风格提示（--setup 安装向导不受限，
    # 升级流程需要能在应用运行时再次拉起安装器）。
    if not acquire_gui_mutex():
        import gui
        gui.show_already_running()
        return 0
    import gui
    return gui.run()


if __name__ == "__main__":
    sys.exit(main())
