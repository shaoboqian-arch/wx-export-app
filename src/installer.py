# -*- coding: utf-8 -*-
"""安装器：把当前 exe 安装到本机并创建快捷方式 / 卸载项。

- 默认装到 %LOCALAPPDATA%\\微信收藏导出，不需要管理员权限。
- 不碰任何导出数据；卸载只删快捷方式、卸载项与本安装器写入的文件，不递归删目录。
- headless / no_shortcut 供构建阶段在隔离目录里做释放验证，不改动用户环境。
"""
import base64
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from common import APP_NAME, EXE_NAME, VERSION, app_dir, is_frozen
from ui_kit import (
    OK,
    BG, ERR, MUTED, Tooltip, apply_icon, apply_island_style,
    center, fonts, CapsuleButton, IslandEntry,
)

# 成功态沿用软件里已有的绿色，不另造新色。
INSTALL_W, INSTALL_H = 600, 228

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\WeChatFavExport"


def default_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "微信收藏导出")


def _ps(script):
    """用 -EncodedCommand 传脚本，避开中文路径与引号转义问题。"""
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
            capture_output=True, timeout=45)
        out = r.stdout.decode("utf-8", "replace").strip()
        return r.returncode, out
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def desktop_dir():
    rc, out = _ps("[Environment]::GetFolderPath('Desktop')")
    return out if rc == 0 and out and os.path.isdir(out) else os.path.join(
        os.path.expanduser("~"), "Desktop")


def start_menu_dir():
    return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                        "Microsoft", "Windows", "Start Menu", "Programs")


def make_shortcut(lnk, target, workdir):
    s = ("$shell = New-Object -ComObject WScript.Shell\n"
         f"$lnk = $shell.CreateShortcut('{lnk}')\n"
         f"$lnk.TargetPath = '{target}'\n"
         f"$lnk.WorkingDirectory = '{workdir}'\n"
         f"$lnk.Description = '{APP_NAME}'\n"
         "$lnk.Save()\n")
    return _ps(s)[0] == 0


def write_uninstall_item(exe):
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_WRITE) as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, VERSION)
            winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, "wx-fav-export")
            winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, os.path.dirname(exe))
            winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, f'"{exe}" --uninstall')
            winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        return True
    except OSError:
        return False


def install(dest, no_shortcut=False, progress=None):
    """执行安装。返回 (ok, message, 安装目录)。"""
    def say(m):
        if progress:
            progress(m)

    dest = os.path.abspath(dest)
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError as e:
        return False, f"无法创建目录：{e}", dest

    exe = os.path.join(dest, EXE_NAME)
    say("正在复制程序…")
    if is_frozen():
        try:
            if os.path.abspath(sys.executable) != os.path.abspath(exe):
                shutil.copy2(sys.executable, exe)
        except OSError as e:
            return False, f"复制程序失败：{e}", dest
    else:
        # 源码态（构建验证/开发）：写一个可读的占位说明，不伪造 exe
        with open(os.path.join(dest, "源码运行.txt"), "w", encoding="utf-8") as f:
            f.write("源码态验证：未生成 exe；正式安装包由 build.py 构建。\n")

    import mcp_config
    entry = dict(mcp_config.server_entry())
    entry["command"] = exe          # 安装后的 exe，不是当前 Setup.exe
    cfg = {"mcpServers": {"wx-fav-export": entry}}
    with open(os.path.join(dest, "mcp配置.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    with open(os.path.join(dest, "版本.txt"), "w", encoding="utf-8") as f:
        f.write(f"{APP_NAME} V{VERSION}\n安装时间 {dt.datetime.now():%Y-%m-%d %H:%M:%S}\n")
    with open(os.path.join(dest, "说明.md"), "w", encoding="utf-8") as f:
        f.write(
            f"# {APP_NAME} V{VERSION}\n\n"
            "界面：选目标文件夹 + 日期范围，点导出。每次导出新建带时间戳的批次目录，绝不覆盖已有文件；"
            "进度表按图片/视频/其他资料显示文件数并列出未取到原件的文件名。\n\n"
            "MCP：把 mcp配置.json 里 wx-fav-export 这一个键合并进对方 `~/.workbuddy/mcp.json` "
            "已有的 mcpServers（不要整份覆盖），保存后到连接器管理页点「信任」才会生效；"
            "接入后调用 wx_status 验证。也可在软件「设置」里一键写入并测试连接。\n\n"
            "首次运行：主按钮为「初始化本机环境」。选微信账号目录后自动提取数据库密钥；"
            "图片密钥需先在微信里打开一张收藏图片，再点「开始检测」，也可手动填写 16 位密钥。\n\n"
            "日期区间起止均含当天。默认只导出图片与视频。\n")

    shortcuts = []
    if not no_shortcut:
        say("正在创建快捷方式…")
        try:
            d = desktop_dir()
            os.makedirs(d, exist_ok=True)
            shortcuts.append(os.path.join(d, f"{APP_NAME}.lnk"))
            d2 = start_menu_dir()
            os.makedirs(d2, exist_ok=True)
            shortcuts.append(os.path.join(d2, f"{APP_NAME}.lnk"))
            for lnk in shortcuts:
                make_shortcut(lnk, exe, dest)
        except OSError:
            pass
        write_uninstall_item(exe)

    say("已安装")
    return True, "已安装", dest


def uninstall(notify=True):
    """卸载：删快捷方式、卸载项与本安装器写入的文件；绝不递归删目录，导出数据一律不动。

    正在运行的 exe 在 Windows 上无法自删，尝试失败时如实告知，不假装删干净。
    """
    for d in (desktop_dir(), start_menu_dir()):
        p = os.path.join(d, f"{APP_NAME}.lnk")
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass

    d = app_dir()
    for name in ("mcp配置.json", "版本.txt", "说明.md", "源码运行.txt",
                 "prefs.json", "sender_roster.json"):
        p = os.path.join(d, name)
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
    exe_left = False
    p = os.path.join(d, EXE_NAME)
    try:
        if os.path.isfile(p):
            os.remove(p)
    except OSError:
        exe_left = True
    try:
        if os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
    except OSError:
        pass

    msg = "已卸载，导出文件未删除"
    if exe_left:
        msg += f"；程序正在运行无法自删，退出后可手动删除 {d}"
    if notify:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, f"卸载 {APP_NAME}", 0x40)
        except Exception:
            pass
    return True, msg


class SetupUI:
    def __init__(self, root, font_main, font_small, family, preset=None, no_shortcut=False):
        self.root = root
        self.no_shortcut = no_shortcut
        self.busy = False
        self.full_state = ""
        root.title(f"安装 {APP_NAME} V{VERSION}")
        root.resizable(False, False)
        apply_icon(root)
        apply_island_style(root)
        try:
            root.configure(bg=BG)
        except Exception:
            pass
        center(root, INSTALL_W, INSTALL_H)

        f = tk.Frame(root, bg=BG, padx=20, pady=16)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        tk.Label(f, text="安装位置", font=font_main, bg=BG, fg=MUTED).grid(
            row=0, column=0, sticky="w", pady=(2, 14))
        self.dest = IslandEntry(f, font=font_main)
        self.dest.grid(row=0, column=1, sticky="ew", padx=(12, 8), pady=(2, 14))
        self.dest.insert(0, preset or default_dir())
        CapsuleButton(f, text="浏览…", width=84, height=36, font=font_small,
                      command=self.pick).grid(row=0, column=2, pady=(2, 14))

        self.btn = CapsuleButton(f, text="安装", command=self.go, primary=True,
                                 height=42, font=(family, 11, "bold"))
        self.btn.grid(row=1, column=0, columnspan=3, sticky="ew")
        self.state = tk.Label(f, text="", font=font_small, fg=MUTED, bg=BG,
                              anchor="w", wraplength=470, justify="left")
        self.state.grid(row=2, column=0, columnspan=3, sticky="w", pady=(14, 0))
        Tooltip(self.state, lambda: self.full_state, font=font_small)
        root.bind("<Return>", lambda _e: self.go())

    def set_state(self, text, color=MUTED):
        self.full_state = text
        self.state.configure(text=text if len(text) <= 76 else text[:73] + "…",
                             foreground=color)

    def pick(self):
        d = filedialog.askdirectory(title="选择安装位置")
        if d:
            self.dest.delete(0, "end")
            self.dest.insert(0, os.path.normpath(d))

    def go(self):
        if self.busy:
            return
        self.busy = True
        self.btn.configure(state="disabled", text="安装中…")
        self.set_state("正在安装…")
        dest = self.dest.get().strip()

        def work():
            ok, msg, _d = install(dest, self.no_shortcut)
            self.root.after(0, lambda: self.done(ok, msg))

        threading.Thread(target=work, daemon=True).start()

    def done(self, ok, msg):
        self.busy = False
        self.set_state(msg, OK if ok else ERR)
        self.btn.configure(text="完成", state="normal", command=self.root.destroy)


def run(preset=None, no_shortcut=False):
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    fm, fs, family = fonts(root)
    SetupUI(root, fm, fs, family, preset, no_shortcut)
    root.mainloop()
    return 0
