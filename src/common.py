# -*- coding: utf-8 -*-
"""公共常量与路径解析（源码运行 / PyInstaller 冻结两种形态都可用）。"""
import os
import sys

VERSION = "0.4.3"   # 唯一版本真源：构建 / 界面 / MCP / 测试都从这里读
APP_NAME = "微信收藏导出"
MCP_SERVER_NAME = "wx-fav-export"
EXE_NAME = "微信收藏导出.exe"
# 语音默认也登记进清单：PC 端不保留 silk 本体，但「谁在什么时候发过多长的语音」
# 本身是有效信息，漏着不提比登一条「仅元数据」更糟。
DEFAULT_KINDS = ("image", "video", "voice")


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def meipass():
    return getattr(sys, "_MEIPASS", None)


def src_dir():
    return os.path.dirname(os.path.abspath(__file__))


def core_dir():
    """core/ 里的引擎脚本（冻结时由 --add-data 释放到 _MEIPASS）。"""
    m = meipass()
    return os.path.join(m, "core") if m else os.path.join(src_dir(), "core")


def app_dir():
    """程序所在目录（冻结时 = exe 所在目录；源码时 = src/）。"""
    return os.path.dirname(sys.executable) if is_frozen() else src_dir()


def data_dir():
    """可写的程序数据目录（日志、上次目标文件夹）。"""
    if is_frozen():
        return app_dir()
    d = os.path.join(src_dir(), "_data")
    os.makedirs(d, exist_ok=True)
    return d


def prefs_path():
    return os.path.join(data_dir(), "prefs.json")


def ensure_vendor():
    """源码态把 build/vendor 加进 sys.path（冻结态依赖已打进 exe，无需处理）。

    否则从源码启动 MCP / CLI 时 `from Crypto.Cipher import AES` 会失败。
    """
    if is_frozen():
        return False
    v = os.path.join(os.path.dirname(src_dir()), "build", "vendor")
    if os.path.isdir(v) and v not in sys.path:
        sys.path.insert(0, v)
        return True
    return False
