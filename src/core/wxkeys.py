# -*- coding: utf-8 -*-
"""wxkeys.py —— 本机微信导出环境初始化（账号目录 / 数据库密钥 / 图片密钥）。

放在 core/ 下与基线引擎同层，冻结时由 --add-data 释放到 _MEIPASS 并被
engine 以源码方式加载。只用标准库；图片密钥校验才用 pycryptodome。

设计约束：
  · 不要求管理员权限。实测普通用户即可 OpenProcess 读取同账号微信进程内存。
  · 写配置前先备份，临时文件原子替换；解析失败不覆盖。
  · 只增改自己需要的键：all_keys.json 整体重写（本就是本程序的产物），
    config.json 只更新 db_dir / image_aes_key，保留其它键。
  · 任何耗时操作都接受 on_step / should_stop，便于界面显示进度与取消。
"""
from __future__ import annotations

import ctypes
import hashlib
import hmac as hmac_mod
import json
import os
import re
import shutil
import struct
import string
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
CFG_DIR = os.path.join(HOME, ".wechat-cli")
ALL_KEYS = os.path.join(CFG_DIR, "all_keys.json")
CONFIG = os.path.join(CFG_DIR, "config.json")

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
SQLITE_HDR = b"SQLite format 3\x00"

MAGIC = b"\x07\x08\x56\x32"
IMG_MAGICS = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"BM", b"RIFF", b"II*\x00",
              b"MM\x00*", b"\x00\x00\x00\x18ftyp", b"\x00\x00\x00\x20ftyp",
              b"\x00\x00\x00\x1cftyp", b"\x00\x00\x00\x14ftyp", b"\x1aE\xdf\xa3",
              b"%PDF", b"PK\x03\x04", b"\xd0\xcf\x11\xe0", b"{\\rtf")

WINDOWS_CONFIG_CIPHER_NAME = b"com.Tencent.WCDB.Config.Cipher"
WINDOWS_CONFIG_XOR_MASK = bytes.fromhex(
    "d2c7442458020000004889442450488b"
    "450048844c2448488944254048584c24"
)
WINDOWS_MAX_USER_ADDRESS = 0x0000_8000_0000_0000
WINDOWS_CONFIG_BLOB_MAX = 1024

MEM_COMMIT = 0x1000
READABLE = {0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80}

_is_win = os.name == "nt"
if _is_win:
    import ctypes.wintypes as wt

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_uint64,
                                           ctypes.c_void_p, ctypes.c_size_t,
                                           ctypes.POINTER(ctypes.c_size_t)]

    _bcrypt = ctypes.WinDLL("bcrypt")
    _bcrypt.BCryptOpenAlgorithmProvider.argtypes = [
        ctypes.POINTER(wt.HANDLE), ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
    _bcrypt.BCryptSetProperty.argtypes = [
        wt.HANDLE, ctypes.c_wchar_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_ulong]
    _bcrypt.BCryptGenerateSymmetricKey.argtypes = [
        wt.HANDLE, ctypes.POINTER(wt.HANDLE), ctypes.c_char_p, ctypes.c_ulong,
        ctypes.c_char_p, ctypes.c_ulong, ctypes.c_ulong]
    _bcrypt.BCryptDecrypt.argtypes = [
        wt.HANDLE, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_void_p,
        ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_uint64), ("AllocationBase", ctypes.c_uint64),
        ("AllocationProtect", wt.DWORD), ("_pad1", wt.DWORD),
        ("RegionSize", ctypes.c_uint64), ("State", wt.DWORD),
        ("Protect", wt.DWORD), ("Type", wt.DWORD), ("_pad2", wt.DWORD),
    ]


# ---------------------------------------------------------------- 配置读写
def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _atomic_write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    if os.path.isfile(path):
        try:
            shutil.copy2(path, path + ".bak")
        except OSError:
            pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def save_db_keys(db_dir, result):
    """写 all_keys.json 与 config.json 的 db_dir，保留 config 其它键。"""
    _atomic_write_json(ALL_KEYS, result)
    cfg = read_json(CONFIG)
    cfg["db_dir"] = db_dir
    _atomic_write_json(CONFIG, cfg)


def save_image_key(key):
    cfg = read_json(CONFIG)
    cfg["image_aes_key"] = key
    _atomic_write_json(CONFIG, cfg)


def keys_state():
    cfg = read_json(CONFIG)
    return {
        "db_keys": os.path.isfile(ALL_KEYS),
        "image_key": bool((cfg.get("image_aes_key") or "").strip()),
        "config_dir": CFG_DIR,
    }


# ---------------------------------------------------------------- 账号目录
def _has_storage(p):
    return os.path.isdir(os.path.join(p, "db_storage"))


def _fav_info(root):
    favdb = os.path.join(root, "db_storage", "favorite", "favorite.db")
    fb = os.path.join(root, "business", "favorite")
    return os.path.isfile(favdb), os.path.isdir(fb)


def candidate_roots(config_db_dir=""):
    """返回候选账号目录，按「最近写过的收藏库」降序。

    来源：config.json 的 db_dir 上溯 → 用户文档 → 各固定盘符常见位置。
    只做一层扫描，避免在大目录上递归。
    """
    out, seen = [], set()

    def add(p, source):
        if not p:
            return
        p = os.path.abspath(p)
        if p in seen or not os.path.isdir(p):
            return
        if not _has_storage(p):
            return
        seen.add(p)
        has_fav, has_base = _fav_info(p)
        try:
            mtime = os.path.getmtime(
                os.path.join(p, "db_storage", "favorite", "favorite.db"))
        except OSError:
            mtime = 0
        out.append({"path": p, "name": os.path.basename(p.rstrip("\\/")),
                    "source": source, "has_favorite": has_fav,
                    "has_base": has_base, "mtime": mtime})

    if config_db_dir:
        add(os.path.dirname(os.path.normpath(config_db_dir)), "配置")
    db_dir = (read_json(CONFIG).get("db_dir") or "") if not config_db_dir else ""
    if db_dir:
        add(os.path.dirname(os.path.normpath(db_dir)), "配置")

    env = os.environ.get("WV_FAV_ROOT")
    if env:
        add(env, "环境变量")

    docs = os.path.join(HOME, "Documents")
    base = os.path.join(docs, "xwechat_files")
    if os.path.isdir(base):
        try:
            for n in os.listdir(base):
                add(os.path.join(base, n), "文档")
        except OSError:
            pass

    for ch in string.ascii_uppercase:
        drive = f"{ch}:\\"
        if not os.path.isdir(drive):
            continue
        for rel in ("xwechat_files", os.path.join("Documents", "xwechat_files"),
                    "WeChat Files"):
            base = os.path.join(drive, rel)
            if not os.path.isdir(base):
                continue
            try:
                for n in os.listdir(base):
                    add(os.path.join(base, n), f"{ch} 盘")
            except OSError:
                pass

    out.sort(key=lambda d: (-int(d["has_favorite"]), -d["mtime"]))
    return out


def probe(root=None):
    """环境快照，不抛异常。"""
    st = keys_state()
    out = {"ok": True, "version": "", **st, "account_root": "",
           "favorite_db": False, "favorite_base": False, "ready": False, "message": ""}
    r = ""
    if root:
        r = os.path.abspath(root)
    else:
        cands = candidate_roots()
        if cands:
            r = cands[0]["path"]
        else:
            cfg_db = read_json(CONFIG).get("db_dir") or ""
            if cfg_db:
                r = os.path.dirname(os.path.normpath(cfg_db))
    if r and _has_storage(r):
        has_fav, has_base = _fav_info(r)
        out["account_root"] = r
        out["favorite_db"] = has_fav
        out["favorite_base"] = has_base
    missing = []
    if not out["account_root"]:
        missing.append("微信账号目录")
    elif not out["favorite_db"]:
        missing.append("收藏数据库")
    if not st["db_keys"]:
        missing.append("数据库密钥")
    if not st["image_key"]:
        missing.append("图片密钥")
    out["ready"] = not missing
    out["message"] = "就绪" if not missing else "缺少" + "、".join(missing)
    return out


# ---------------------------------------------------------------- 进程与内存
def weixin_pids():
    if not _is_win:
        return []
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, errors="replace", encoding="mbcs",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        return []
    pids = []
    for line in (r.stdout or "").strip().splitlines():
        p = line.strip().strip('"').split('","')
        if len(p) >= 5:
            try:
                pids.append((int(p[1]), int(p[4].replace(",", "").replace(" K", "").strip() or 0)))
            except ValueError:
                pass
    pids.sort(key=lambda x: -x[1])
    return pids


def _read_mem(h, addr, size):
    buf = ctypes.create_string_buffer(size)
    n = ctypes.c_size_t(0)
    if kernel32.ReadProcessMemory(h, ctypes.c_uint64(addr), buf, size, ctypes.byref(n)):
        return buf.raw[: n.value]
    return None


def _enum_regions(h):
    regs, addr, mbi = [], 0, MBI()
    while addr < 0x7FFFFFFFFFFF:
        if kernel32.VirtualQueryEx(h, ctypes.c_uint64(addr), ctypes.byref(mbi),
                                   ctypes.sizeof(mbi)) == 0:
            break
        if (mbi.State == MEM_COMMIT and mbi.Protect in READABLE
                and 0 < mbi.RegionSize < 500 * 1024 * 1024):
            regs.append((mbi.BaseAddress, mbi.RegionSize, mbi.Protect))
        nxt = mbi.BaseAddress + mbi.RegionSize
        if nxt <= addr:
            break
        addr = nxt
    return regs


def _iter_chunks(regions, read, chunk_size=4 * 1024 * 1024, overlap=0):
    for base, size in regions:
        offset, tail, tail_base = 0, b"", base
        while offset < size:
            cur = min(chunk_size, size - offset)
            chunk = read(base + offset, cur) or b""
            data_base = tail_base if tail else base + offset
            data = tail + chunk
            if data:
                yield data_base, data
                if overlap:
                    tail = data[-overlap:]
                    tail_base = data_base + max(0, len(data) - len(tail))
                else:
                    tail, tail_base = b"", base + offset + cur
            else:
                tail, tail_base = b"", base + offset + cur
            offset += cur


# ---------------------------------------------------------------- 数据库密钥
def _aes_cbc_decrypt(key, iv, data):
    h_alg = wt.HANDLE()
    if _bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(h_alg), "AES", None, 0) != 0:
        raise RuntimeError("BCryptOpenAlgorithmProvider failed")
    try:
        mode = "ChainingModeCBC\x00".encode("utf-16-le")
        _bcrypt.BCryptSetProperty(h_alg, "ChainingMode", mode, len(mode), 0)
        h_key = wt.HANDLE()
        if _bcrypt.BCryptGenerateSymmetricKey(h_alg, ctypes.byref(h_key), None, 0,
                                              key, len(key), 0) != 0:
            raise RuntimeError("BCryptGenerateSymmetricKey failed")
        try:
            iv_buf = ctypes.create_string_buffer(iv, len(iv))
            out = ctypes.create_string_buffer(len(data))
            n = ctypes.c_ulong(0)
            if _bcrypt.BCryptDecrypt(h_key, data, len(data), None, iv_buf, len(iv),
                                     out, len(out), ctypes.byref(n), 0) != 0:
                raise RuntimeError("BCryptDecrypt failed")
            return out.raw[: n.value]
        finally:
            _bcrypt.BCryptDestroyKey(h_key)
    finally:
        _bcrypt.BCryptCloseAlgorithmProvider(h_alg, 0)


def verify_db_key(enc_key, page1):
    salt = page1[:SALT_SZ]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)
    hmac_data = page1[SALT_SZ: PAGE_SZ - 80 + 16]
    stored = page1[PAGE_SZ - 64: PAGE_SZ]
    hm = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    return hm.digest() == stored


def _collect_db_files(db_dir):
    files, salt_to_dbs = [], {}
    for root, _dirs, names in os.walk(db_dir):
        for name in names:
            if not name.endswith(".db") or name.endswith("-wal") or name.endswith("-shm"):
                continue
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size < PAGE_SZ:
                continue
            try:
                with open(path, "rb") as f:
                    page1 = f.read(PAGE_SZ)
            except OSError:
                continue
            rel = os.path.relpath(path, db_dir)
            salt = page1[:SALT_SZ].hex()
            files.append((rel, path, size, salt, page1))
            salt_to_dbs.setdefault(salt, []).append(rel)
    return files, salt_to_dbs


def _xor_repeat(data, mask):
    return bytes(v ^ mask[i % len(mask)] for i, v in enumerate(data))


def _u64(data, off):
    if off < 0 or off + 8 > len(data):
        return 0
    return struct.unpack_from("<Q", data, off)[0]


def _dump_blobs(on_step=None, max_seconds=90):
    """从 Weixin.exe 进程内存里取出 Config.Cipher 的 blob。"""
    blobs = []
    pids = weixin_pids()
    if not pids:
        return blobs
    t0 = time.time()
    for pid, mem_kb in pids:
        if time.time() - t0 > max_seconds:
            break
        h = kernel32.OpenProcess(0x0010 | 0x0400, False, pid)
        if not h:
            continue
        try:
            regs = [(b, s) for b, s, _p in _enum_regions(h)]
            needles = set()
            for base, data in _iter_chunks(regs, lambda b, s, _h=h: _read_mem(_h, b, s),
                                           overlap=len(WINDOWS_CONFIG_CIPHER_NAME)):
                pos = data.find(WINDOWS_CONFIG_CIPHER_NAME)
                while pos >= 0:
                    needles.add(base + pos)
                    pos = data.find(WINDOWS_CONFIG_CIPHER_NAME, pos + 1)
            if not needles:
                continue
            if on_step:
                on_step(f"发现 {len(needles)} 处配置节点，正在校验…")
            patterns = [struct.pack("<Q", a) + struct.pack("<Q", len(WINDOWS_CONFIG_CIPHER_NAME))
                        for a in needles]
            for base, data in _iter_chunks(regs, lambda b, s, _h=h: _read_mem(_h, b, s),
                                           overlap=0x80):
                for pat in patterns:
                    pos = data.find(pat)
                    while pos >= 0:
                        node = _read_mem(h, base + pos - 0x10, 0x50)
                        if (node and len(node) >= 0x40
                                and _u64(node, 0x10) in needles
                                and _u64(node, 0x18) == len(WINDOWS_CONFIG_CIPHER_NAME)):
                            cfg = _u64(node, 0x28)
                            if 0x10000 <= cfg < WINDOWS_MAX_USER_ADDRESS:
                                obj = _read_mem(h, cfg + 0x88, 0x28)
                                if obj and len(obj) >= 0x18:
                                    dptr, dlen = _u64(obj, 0x8), _u64(obj, 0x10)
                                    if (0 < dlen <= WINDOWS_CONFIG_BLOB_MAX
                                            and 0x10000 <= dptr < WINDOWS_MAX_USER_ADDRESS):
                                        blob = _read_mem(h, dptr, int(dlen))
                                        if blob and len(blob) == dlen:
                                            blobs.append(blob)
                        pos = data.find(pat, pos + 1)
                if time.time() - t0 > max_seconds:
                    break
        finally:
            kernel32.CloseHandle(h)
        if blobs:
            break
    return blobs


HEX_RUN = re.compile(rb"[0-9a-fA-F]{48,200}")


def _candidates_from_blob(blob):
    outs = []
    for raw in (blob, _xor_repeat(blob, WINDOWS_CONFIG_XOR_MASK)):
        for m in HEX_RUN.finditer(raw):
            run = m.group(0).decode("ascii").lower()
            for start in range(0, max(1, len(run) - 63), 16):
                chunk = run[start:start + 64]
                if len(chunk) < 64:
                    continue
                try:
                    k = bytes.fromhex(chunk)
                except ValueError:
                    continue
                if len(set(k)) < 12 or k in (b"\x00" * 32, b"\xff" * 32):
                    continue
                outs.append(k)
    return outs


def extract_db_keys(db_dir, on_step=None, max_seconds=120):
    """提取数据库密钥并写入 all_keys.json + config.json。"""
    if not _is_win:
        return {"ok": False, "error": "仅支持 Windows"}
    db_dir = os.path.normpath(db_dir)
    if not os.path.isdir(db_dir):
        return {"ok": False, "error": f"db_storage 不存在：{db_dir}"}
    if not weixin_pids():
        return {"ok": False, "error": "微信未运行，请先登录微信再提取"}

    if on_step:
        on_step("正在读取数据库…")
    db_files, salt_to_dbs = _collect_db_files(db_dir)
    if not db_files:
        return {"ok": False, "error": "该目录下没有可识别的微信数据库"}
    page1_by_salt = {s: p1 for _r, _p, _z, s, p1 in db_files}
    rel_by_salt = {}
    for rel, _p, _z, s, _p1 in db_files:
        rel_by_salt.setdefault(s, rel)

    if on_step:
        on_step("正在读取微信进程内存…")
    blobs = _dump_blobs(on_step, max_seconds=max_seconds)
    if not blobs:
        return {"ok": False,
                "error": "未能从微信进程读到配置。请确认微信已登录；若仍失败，"
                         "可重启微信后再试"}

    if on_step:
        on_step("正在校验密钥…")
    found, seen, tried = {}, set(), 0
    for blob in blobs:
        for k in _candidates_from_blob(blob):
            hx = k.hex()
            if hx in seen:
                continue
            seen.add(hx)
            tried += 1
            for salt in salt_to_dbs:
                if salt in found:
                    continue
                if verify_db_key(k, page1_by_salt[salt]):
                    found[salt] = hx
                    break
            if len(found) == len(salt_to_dbs):
                break
        if len(found) == len(salt_to_dbs):
            break

    if not found:
        return {"ok": False,
                "error": f"试了 {tried} 个候选仍未匹配。内存里的密钥可能属于另一个"
                         f"已登录账号，请确认微信当前登录的是这个账号"}

    result = {}
    for rel, _p, size, salt, _p1 in db_files:
        if salt in found:
            result[rel] = {"enc_key": found[salt], "salt": salt,
                           "size_mb": round(size / 1024 / 1024, 1)}
    result["_db_dir"] = db_dir
    if on_step:
        on_step("正在写入配置…")
    save_db_keys(db_dir, result)
    return {"ok": True, "found": len(found), "total": len(salt_to_dbs),
            "path": ALL_KEYS, "message": f"已提取 {len(found)}/{len(salt_to_dbs)} 个库的密钥"}


# ---------------------------------------------------------------- 图片密钥
def image_samples(root, limit=64):
    """从收藏容器目录取 AES 块用于校验候选密钥。

    容器格式：6B magic + 4B aes_size + 4B xor_size + 1B + AES 密文。
    这里只取第一个 16 字节密文块，解出来应是图片/视频文件头。
    """
    fb = os.path.join(root, "business", "favorite")
    out, seen = [], set()
    for sub in ("data", "mid", "thumb"):
        d = os.path.join(fb, sub)
        if not os.path.isdir(d):
            continue
        for dp, _dirs, names in os.walk(d, onerror=lambda e: None):
            for name in names:
                if len(out) >= limit:
                    return out
                p = os.path.join(dp, name)
                try:
                    with open(p, "rb") as f:
                        head = f.read(1100)
                except OSError:
                    continue
                if len(head) < 47 or head[:4] != MAGIC:
                    continue
                aes_size = struct.unpack("<I", head[6:10])[0]
                n = ((aes_size + 15) // 16) * 16
                if n < 256 or 15 + n > len(head):
                    continue
                blk = head[15:31]
                if blk in seen:
                    continue
                seen.add(blk)
                out.append(blk)
    return out


def verify_image_key(key, samples, need=None):
    """返回 (是否可信, 命中数)。靠多容器文件头做判据，不用本机已知明文。"""
    try:
        from Crypto.Cipher import AES
        ecb = AES.new(key, AES.MODE_ECB)
    except Exception:
        return False, 0
    hits = 0
    for blk in samples:
        if ecb.decrypt(blk).startswith(IMG_MAGICS):
            hits += 1
    if need is None:
        if len(samples) >= 8:
            need = 3
        elif len(samples) >= 3:
            need = 2
        else:
            need = 1
    return hits >= need, hits


ASCII_RUN = re.compile(rb"[0-9A-Za-z]{16,80}")
U16_RUN = re.compile(rb"(?:[\x21-\x7e]\x00){16,120}")


def _candidates_from(buf):
    for m in ASCII_RUN.finditer(buf):
        run = m.group(0)
        step = 1 if len(run) <= 80 else 4
        for i in range(0, len(run) - 15, step):
            yield run[i:i + 16]
    for m in U16_RUN.finditer(buf):
        run = m.group(0)[::2]
        step = 1 if len(run) <= 120 else 4
        for i in range(0, len(run) - 15, step):
            yield run[i:i + 16]


def _scan_once(samples, rw_only=True, on_progress=None, should_stop=None):
    """扫一轮内存，返回第一个通过校验的密钥字符串。"""
    from Crypto.Cipher import AES
    probes = samples[:2] if len(samples) >= 2 else samples[:1]
    seen = set()
    pids = weixin_pids()
    if not pids:
        return None
    for pid, mem_kb in pids:
        h = kernel32.OpenProcess(0x0010 | 0x0400, False, pid)
        if not h:
            continue
        try:
            regs = [(b, s) for b, s, p in _enum_regions(h)
                    if (not rw_only or p in {0x04, 0x08, 0x40, 0x80})]
            total = len(regs)
            for i, (base, size) in enumerate(regs):
                if should_stop and should_stop():
                    return None
                data = _read_mem(h, base, size)
                if not data:
                    continue
                for cand in _candidates_from(data):
                    if cand in seen:
                        continue
                    seen.add(cand)
                    try:
                        ecb = AES.new(cand, AES.MODE_ECB)
                    except Exception:
                        continue
                    if not any(ecb.decrypt(blk).startswith(IMG_MAGICS) for blk in probes):
                        continue
                    ok, _hits = verify_image_key(cand, samples)
                    if ok:
                        return cand.decode("ascii")
                if on_progress and i % 10 == 0:
                    on_progress(i + 1, total)
        finally:
            kernel32.CloseHandle(h)
    return None


def capture_image_key(root, rounds=3, on_tick=None, should_stop=None):
    """引导式捕获图片密钥。

    前提是用户已在微信里点开过收藏中的图片/播放过视频——密钥只在渲染那一刻
    驻留进程内存。返回 {"ok":true,"key":...} 或带 error 的失败结果。
    """
    if not _is_win:
        return {"ok": False, "error": "仅支持 Windows"}
    samples = image_samples(root)
    if not samples:
        return {"ok": False, "need_action": True,
                "error": "本机还没有可校验的图片缓存：请先在微信里打开一张收藏图片，再点开始"}
    for n in range(1, rounds + 1):
        if should_stop and should_stop():
            return {"ok": False, "cancelled": True, "error": "已取消"}
        if on_tick:
            on_tick(n, rounds, "scan")
        key = _scan_once(samples, rw_only=(n <= 2), should_stop=should_stop,
                         on_progress=(lambda i, t, _n=n: on_tick and on_tick(_n, rounds, f"{i}/{t}")))
        if key:
            save_image_key(key)
            return {"ok": True, "key": key, "rounds": n,
                    "message": f"已捕获并从 {len(samples)} 个容器校验通过"}
        if on_tick:
            on_tick(n, rounds, "miss")
    return {"ok": False, "need_action": True,
            "error": "未捕获到。请在微信里点开一张收藏图片后重试，或手动填写 16 位密钥"}
