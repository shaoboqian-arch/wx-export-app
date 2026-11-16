# -*- coding: utf-8 -*-
"""paths.py —— 路径自解析（让这套工具离开硬编码也能跑）

为什么需要它：早期脚本把账号目录写成 `C:/Users/<用户>/Documents/xwechat_files/<账号目录>`，
交付目录写成 `某个固定的桌面文件夹`。一旦换机器 / 换账号 / 改目录名，
所有脚本会静默找不到东西（`os.path.exists` 返回 False，不报错、直接漏件）。

解析顺序（每一步都检查存在性，取第一个成立的）：
  账号根 <root>：
    1) 显式传入 / 环境变量 WV_FAV_ROOT
    2) ~/.wechat-cli/config.json 的 db_dir 去掉末尾两级（<root>/db_storage）
    3) 扫 ~/Documents/xwechat_files/*/，选「有 business/favorite 或 db_storage」且最大的那个
  交付目录 <dest>：
    1) 显式传入 / 环境变量 WV_FAV_DEST
    2) 桌面下唯一含 `_分组清单.csv` 的目录（认我们自己的产物，不猜名字）
    3) 桌面下新建 `微信收藏导出`
"""
import json
import os
import sys

HOME = os.path.expanduser("~")
CONFIG = os.path.join(HOME, ".wechat-cli", "config.json")
DEFAULT_DEST_NAME = "微信收藏导出"
HERE = os.path.dirname(os.path.abspath(__file__))


def _cfg():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _desktop():
    d = os.path.join(HOME, "Desktop")
    return d if os.path.isdir(d) else HOME


def account_root(explicit=None):
    """返回 <xwechat_files>/<账号目录> 的绝对路径。找不到抛 RuntimeError（带排查提示）。"""
    cands = []
    if explicit:
        cands.append(explicit)
    if os.environ.get("WV_FAV_ROOT"):
        cands.append(os.environ["WV_FAV_ROOT"])

    db_dir = _cfg().get("db_dir") or ""
    if db_dir:
        # <root>/db_storage -> <root>
        cands.append(os.path.dirname(os.path.dirname(os.path.normpath(db_dir))))

    for c in cands:
        if c and os.path.isdir(os.path.join(c, "db_storage")):
            return os.path.abspath(c)

    # 兜底：扫 xwechat_files
    base = os.path.join(HOME, "Documents", "xwechat_files")
    best, best_sz = None, -1
    if os.path.isdir(base):
        for n in os.listdir(base):
            p = os.path.join(base, n)
            if not os.path.isdir(os.path.join(p, "db_storage")):
                continue
            sz = 0
            for sub in ("db_storage", "business"):
                sp = os.path.join(p, sub)
                for dp, _, fn in os.walk(sp, onerror=lambda e: None):
                    for f in fn:
                        try:
                            sz += os.path.getsize(os.path.join(dp, f))
                        except OSError:
                            pass
            if sz > best_sz:
                best, best_sz = p, sz
    if best:
        return os.path.abspath(best)

    raise RuntimeError(
        "找不到微信账号目录。请任选一种方式指定：\n"
        "  · 设环境变量 WV_FAV_ROOT=<xwechat_files>/<账号目录>\n"
        "  · 或先跑 extract_keys.py --db-dir <db_storage 路径> 写好 ~/.wechat-cli/config.json\n"
        f"  · 已尝试的候选：{cands}\n"
        f"  · 已扫过的位置：{base}")


def fav_db(root=None):
    return os.path.join(account_root(root), "db_storage", "favorite", "favorite.db")


def fav_base(root=None):
    return os.path.join(account_root(root), "business", "favorite")


def _remember_file():
    return os.path.join(HERE, "_过程归档", "last_dest.txt")


def _looks_like_dest(p):
    """认我们自己的产物：目录里有 `_分组清单*.csv`。不靠猜名字，改名也能认出来。"""
    try:
        return any(f.startswith("_分组清单") and f.endswith(".csv") for f in os.listdir(p))
    except OSError:
        return False


def _recall():
    """上次成功用过、且现在仍然有效的交付目录。"""
    try:
        with open(_remember_file(), encoding="utf-8") as f:
            d = f.read().strip()
    except OSError:
        return None
    return d if d and os.path.isdir(d) and _looks_like_dest(d) else None


def _remember(d):
    try:
        p = _remember_file()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(d)
    except OSError:
        pass


def _scan_drives():
    """扫各盘符根目录**一层**，找带 `_分组清单*.csv` 的目录。

    为什么需要：交付目录可能被整体搬走（例如从桌面移到 Z 盘），
    此时桌面已找不到，光靠桌面会退回「新建一个空目录」——那是最坏的结果。
    """
    import string
    hits = []
    for ch in string.ascii_uppercase:
        root = f"{ch}:\\"
        if not os.path.isdir(root):
            continue
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for n in names:
            p = os.path.join(root, n)
            if os.path.isdir(p) and _looks_like_dest(p):
                hits.append(os.path.abspath(p))
    return hits


def default_dest(explicit=None):
    """返回交付目录（不保证存在）。

    解析顺序：
      ① 显式传入 / WV_FAV_DEST
      ② **记住的上次路径**（_过程归档/last_dest.txt，验证过存在且有清单才用）
      ③ 桌面下带 `_分组清单.csv` 的目录
      ④ **各盘符根目录**（整个目录被搬到其他盘时兜底）
      ⑤ 桌面新建 `微信收藏导出`（最后的兜底，正常不该走到）
    """
    if explicit:
        d = os.path.abspath(explicit)
        _remember(d)
        return d
    if os.environ.get("WV_FAV_DEST"):
        d = os.path.abspath(os.environ["WV_FAV_DEST"])
        _remember(d)
        return d

    d = _recall()
    if d:
        return d

    desk = _desktop()
    if os.path.isdir(desk):
        for n in sorted(os.listdir(desk)):
            p = os.path.join(desk, n)
            if os.path.isdir(p) and _looks_like_dest(p):
                _remember(p)
                return os.path.abspath(p)

    hits = _scan_drives()
    if hits:
        _remember(hits[0])
        return hits[0]

    return os.path.abspath(os.path.join(desk, "微信收藏导出"))


if __name__ == "__main__":
    try:
        r = account_root()
        print("账号目录 :", r)
        print("收藏库   :", fav_db())
        print("容器目录 :", fav_base(), "(存在)" if os.path.isdir(fav_base()) else "(不存在)")
    except RuntimeError as e:
        print("[!]", e)
    d = default_dest()
    print("交付目录 :", d, "(存在)" if os.path.isdir(d) else "(不存在)")
    sys.exit(0)
