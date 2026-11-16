#!/usr/bin/env python3
"""wxcli — 本地微信数据库只读查询工具（Windows / 微信 4.x）

依赖：~/.wechat-cli/all_keys.json（由 wcdb_key_tool_windows.py extract 生成）
用法：
    python wxcli.py tables --db session/session.db
    python wxcli.py sessions --limit 20
    python wxcli.py history <联系人/群名> --limit 50 [--start 2026-09-01] [--end 2026-09-15]
    python wxcli.py search <关键词> [--limit 50]
    python wxcli.py contacts [--query 名字]
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import datetime as dt
import hashlib
import hmac as hmac_mod
import json
import os
import struct
import sys

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
IV_SZ = 16
HMAC_SZ = 64
RESERVE_SZ = 80
SQLITE_HDR = b"SQLite format 3\x00"

HOME = os.path.expanduser("~")
KEYS_FILE = os.path.join(HOME, ".wechat-cli", "all_keys.json")
CONFIG_FILE = os.path.join(HOME, ".wechat-cli", "config.json")
CACHE_DIR = os.path.join(HOME, ".wechat-cli", "decrypted")

# ---- AES-CBC via bcrypt.dll ----
_bcrypt = ctypes.WinDLL("bcrypt")
_bcrypt.BCryptOpenAlgorithmProvider.argtypes = [ctypes.POINTER(wt.HANDLE), ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
_bcrypt.BCryptSetProperty.argtypes = [wt.HANDLE, ctypes.c_wchar_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_ulong]
_bcrypt.BCryptGenerateSymmetricKey.argtypes = [wt.HANDLE, ctypes.POINTER(wt.HANDLE), ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_ulong]
_bcrypt.BCryptDecrypt.argtypes = [wt.HANDLE, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]


def aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    h_alg = wt.HANDLE()
    if _bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(h_alg), "AES", None, 0) != 0:
        raise RuntimeError("BCryptOpenAlgorithmProvider failed")
    try:
        mode = "ChainingModeCBC\x00".encode("utf-16-le")
        _bcrypt.BCryptSetProperty(h_alg, "ChainingMode", mode, len(mode), 0)
        h_key = wt.HANDLE()
        if _bcrypt.BCryptGenerateSymmetricKey(h_alg, ctypes.byref(h_key), None, 0, key, len(key), 0) != 0:
            raise RuntimeError("BCryptGenerateSymmetricKey failed")
        try:
            iv_buf = ctypes.create_string_buffer(iv, len(iv))
            out_buf = ctypes.create_string_buffer(len(data))
            n = ctypes.c_ulong(0)
            if _bcrypt.BCryptDecrypt(h_key, data, len(data), None, iv_buf, len(iv), out_buf, len(out_buf), ctypes.byref(n), 0) != 0:
                raise RuntimeError("BCryptDecrypt failed")
            return out_buf.raw[: n.value]
        finally:
            _bcrypt.BCryptDestroyKey(h_key)
    finally:
        _bcrypt.BCryptCloseAlgorithmProvider(h_alg, 0)


def load_keys():
    with open(KEYS_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    db_dir = raw.get("_db_dir", "")
    return {k: v for k, v in raw.items() if not k.startswith("_")}, db_dir


def get_key(keys, rel: str):
    rel = rel.replace("\\", "/")
    for cand in (rel, rel.replace("/", "\\")):
        if cand in keys:
            return keys[cand]
    # 退化为按 basename 匹配
    base = os.path.basename(rel)
    for k, v in keys.items():
        if os.path.basename(k.replace("\\", "/")) == base:
            return v
    return None


def _cache_stamp(path: str) -> str:
    st = os.stat(path)
    return f"{st.st_size}:{st.st_mtime_ns}"


def decrypt_db(db_path: str, rel: str, keys, force=False) -> str:
    key_info = get_key(keys, rel)
    if not key_info:
        raise RuntimeError(f"无密钥: {rel}")
    enc_key = bytes.fromhex(key_info["enc_key"])
    out_path = os.path.join(CACHE_DIR, rel.replace("/", os.sep))
    stamp_path = out_path + ".stamp"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # 缓存有效性：源文件的 size+mtime 都与上次解密时一致才复用
    try:
        want = _cache_stamp(db_path)
    except OSError:
        want = ""
    if not force and os.path.exists(out_path) and os.path.exists(stamp_path):
        try:
            with open(stamp_path) as f:
                if f.read().strip() == want and want:
                    return out_path
        except OSError:
            pass

    size = os.path.getsize(db_path)
    total_pages = size // PAGE_SZ + (1 if size % PAGE_SZ else 0)
    with open(db_path, "rb") as fin, open(out_path, "wb") as fout:
        for pgno in range(1, total_pages + 1):
            page = fin.read(PAGE_SZ)
            if len(page) < PAGE_SZ:
                page = page + b"\x00" * (PAGE_SZ - len(page))
            iv = page[PAGE_SZ - RESERVE_SZ: PAGE_SZ - RESERVE_SZ + IV_SZ]
            if pgno == 1:
                dec = aes_cbc_decrypt(enc_key, iv, page[SALT_SZ: PAGE_SZ - RESERVE_SZ])
                fout.write(SQLITE_HDR + dec + b"\x00" * RESERVE_SZ)
            else:
                dec = aes_cbc_decrypt(enc_key, iv, page[: PAGE_SZ - RESERVE_SZ])
                fout.write(dec + b"\x00" * RESERVE_SZ)
    try:
        with open(stamp_path, "w") as f:
            f.write(want)
    except OSError:
        pass
    return out_path


def open_db(path: str):
    import sqlite3
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def cmd_tables(args):
    keys, db_dir = load_keys()
    rel = args.db
    path = decrypt_db(os.path.join(db_dir, rel), rel, keys, force=args.force)
    conn = open_db(path)
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    for r in rows:
        cols = conn.execute(f"PRAGMA table_info('{r[0]}')").fetchall()
        print(f"{r[0]}: " + ", ".join(c[1] for c in cols))
    conn.close()


def load_contacts(db_dir, keys, force=False):
    """返回 username -> 显示名 映射"""
    mapping = {}
    rel = "contact/contact.db"
    path = os.path.join(db_dir, rel)
    if not os.path.exists(path):
        return mapping
    try:
        dec = decrypt_db(path, rel, keys, force=force)
        conn = open_db(dec)
        cols = [c[1] for c in conn.execute("PRAGMA table_info('Contact')").fetchall()]
        name_col = "nick_name" if "nick_name" in cols else cols[1]
        rows = conn.execute(f"SELECT username, {name_col}, * FROM Contact").fetchall()
        for r in rows:
            d = dict(r)
            mapping[d.get("username")] = d.get(name_col) or d.get("remark") or ""
        conn.close()
    except Exception as e:
        print(f"[WARN] 联系人加载失败: {e}")
    return mapping


def cmd_sessions(args):
    keys, db_dir = load_keys()
    rel = "session/session.db"
    path = decrypt_db(os.path.join(db_dir, rel), rel, keys, force=args.force)
    conn = open_db(path)
    names = load_contacts(db_dir, keys, args.force)
    rows = conn.execute(
        "SELECT username, summary, last_timestamp, unread_count FROM SessionTable "
        "ORDER BY sort_timestamp DESC LIMIT ?", (args.limit,)
    ).fetchall()
    print(f"{'时间':<17}{'未读':<5}{'对象'}")
    print("-" * 70)
    for r in rows:
        d = dict(r)
        ts = d.get("last_timestamp") or 0
        try:
            tstr = dt.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            tstr = str(ts)
        who = d.get("username") or ""
        disp = names.get(who) or who
        summary = (d.get("summary") or "").replace("\n", " ")[:40]
        unread = d.get("unread_count") or 0
        print(f"{tstr:<17}{str(unread):<5}{disp[:20]:<22}| {summary}")
    conn.close()


import glob
import hashlib as _hashlib

try:
    import zstandard as zstd
    _DCTX = zstd.ZstdDecompressor()
except Exception:
    _DCTX = None


import re

TYPE_LABEL = {1: "文本", 3: "图片", 34: "语音", 43: "视频", 47: "表情", 49: "分享/文件",
              10000: "系统", 244813135921: "引用", 436207665: "微信红包转账"}


def _zstd_maybe(b: bytes) -> bytes:
    if _DCTX is not None and b[:4] == b"\x28\xb5\x2f\xfd":
        try:
            return _DCTX.decompress(b, max_output_size=8 * 1024 * 1024)
        except Exception:
            return b
    return b


def decode_content(msg_content, compress_content, ctype=None, ltype=None):
    """返回 (类型标签, 文本内容)"""
    ltype = (ltype or 0) & 0xFFFFFFFF
    blob = msg_content if msg_content else compress_content
    if blob is None:
        return "", ""
    if isinstance(blob, str):
        txt = blob
    else:
        b = bytes(blob)
        b = _zstd_maybe(b)
        txt = b.decode("utf-8", "replace")
    # 群消息正文常自带 "wxid_xxx: " / "username: " 发送者前缀，去掉
    txt = re.sub(r"^\s*wxid_[0-9a-zA-Z_-]{5,}\s*[:：]\s*", "", txt)
    m_pre = re.match(r"^\s*[A-Za-z0-9_.\-@]{4,60}\s*[:：]\s*(?=<)", txt)
    if m_pre:
        txt = txt[m_pre.end():]
    if txt.lstrip().startswith("<"):
        m = re.search(r"<title>(.*?)</title>", txt, re.S)
        d = re.search(r"<des>(.*?)</des>", txt, re.S)
        if "<img" in txt:
            return "图片", "[图片]"
        if "<video" in txt:
            return "视频", "[视频]"
        if "<voicemsg" in txt:
            return "语音", "[语音]"
        if "<voipmsg" in txt:
            m2 = re.search(r"<!\[CDATA\[(.*?)\]\]>", txt, re.S)
            return "通话", f"[通话] {m2.group(1).strip() if m2 else ''}"
        if "sysmsg" in txt[:200]:
            return "系统", "[系统消息]"
        if "<emoji" in txt or "emoji" in txt[:200]:
            return "表情", "[表情]"
        if "<file" in txt:
            return "文件", f"[文件] {m.group(1) if m else ''}"
        if m:
            return TYPE_LABEL.get(ltype, "分享"), f"[链接] {(m.group(1) or '').strip()}" + (
                f" — {(d.group(1) or '').strip()[:60]}" if d else "")
        return TYPE_LABEL.get(ltype, str(ltype)), "[XML]"
    return TYPE_LABEL.get(ltype, str(ltype)), txt


def classify_session(u: str) -> str:
    if u in ("brandsessionholder", "brandservicesessionholder", "@placeholder_foldgroup"):
        return "系统占位"
    # 微信官方系统账号（通知类，非真人，不该算待回）
    if u in ("weixin", "wechatteam", "weixinteam", "fmessage", "medianote", "newsapp",
             "qqmail", "floatbottle", "tmessage", "qmessage", "weibo", "notifymessage"):
        return "公众号"
    if u == "filehelper":
        return "自己"
    if u.endswith("@chatroom"):
        return "群聊"
    if u.startswith("gh_"):
        return "公众号"
    if u.endswith("@openim"):
        return "客服"
    return "私聊"


def strip_sender_prefix(txt: str, names: dict) -> str:
    """群消息正文常带 "username: " 发送者前缀，发送者已单独显示时去掉"""
    m = re.match(r"^\s*([A-Za-z0-9_.\-@]{4,60})\s*[:：]\s*", txt)
    if m:
        tok = m.group(1).lower()
        # 命中联系人表，或本身是 wxid/@openim/@chatroom 这类账号形态
        if tok in {str(u).lower() for u in names} or "@" in tok:
            return txt[m.end():]
    return txt


def iter_msg_dbs(db_dir):
    for path in sorted(glob.glob(os.path.join(db_dir, "message", "message_*.db"))):
        base = os.path.basename(path)
        if base.startswith("message_fts") or base.startswith("message_resource"):
            continue
        yield base, path


def resolve_talker(target, names, db_dir, keys, force=False):
    """昵称/备注/username -> username 列表"""
    hits = [u for u, n in names.items() if target in (u, n) or (n and target in n)]
    if hits:
        return hits
    return [target]


def cmd_history(args):
    keys, db_dir = load_keys()
    names = load_contacts(db_dir, keys, args.force)
    talkers = resolve_talker(args.talker, names, db_dir, keys, args.force)
    start_ts = int(dt.datetime.strptime(args.start, "%Y-%m-%d").timestamp()) if args.start else 0
    end_ts = int(dt.datetime.strptime(args.end, "%Y-%m-%d").timestamp()) + 86399 if args.end else 2 ** 31

    rows_all = []
    for base, path in iter_msg_dbs(db_dir):
        rel = f"message/{base}"
        try:
            dec = decrypt_db(path, rel, keys, force=args.force)
        except Exception as e:
            print(f"[WARN] 跳过 {base}: {e}")
            continue
        conn = open_db(dec)
        try:
            for u in talkers:
                tname = "Msg_" + _hashlib.md5(u.encode()).hexdigest()
                exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tname,)
                ).fetchone()
                if not exists:
                    continue
                sql = (f"SELECT create_time, real_sender_id, local_type, "
                       f"message_content, compress_content, WCDB_CT_message_content "
                       f"FROM '{tname}' WHERE create_time BETWEEN ? AND ? "
                       f"ORDER BY create_time DESC LIMIT ?")
                for r in conn.execute(sql, (start_ts, end_ts, args.limit)).fetchall():
                    rows_all.append(dict(r))
        except Exception as e:
            print(f"[WARN] 查询 {base} 失败: {e}")
        finally:
            conn.close()

    rows_all.sort(key=lambda d: d.get("create_time") or 0)
    rows_all = rows_all[-args.limit:]
    print(f"共 {len(rows_all)} 条（talker={','.join(talkers)}）\n")
    for d in rows_all:
        ts = d.get("create_time") or 0
        tstr = dt.datetime.fromtimestamp(int(ts)).strftime("%m-%d %H:%M:%S")
        sender = d.get("real_sender_id")
        sender = "" if sender is None else str(sender)
        kind, txt = decode_content(d.get("message_content"), d.get("compress_content"),
                                   d.get("WCDB_CT_message_content"), d.get("local_type"))
        txt = txt.replace("\n", " ⏎ ")[:300]
        print(f"[{tstr}] {kind} {sender[:20]}: {txt}")


def cmd_contacts(args):
    keys, db_dir = load_keys()
    names = load_contacts(db_dir, keys, args.force)
    n = 0
    for u, disp in names.items():
        if args.query and args.query not in (u or "") and args.query not in (disp or ""):
            continue
        print(f"{disp}\t{u}")
        n += 1
        if n >= args.limit:
            break


def cmd_today(args):
    """今日（或指定日期）消息概要：按会话汇总条数与内容摘要"""
    keys, db_dir = load_keys()
    names = load_contacts(db_dir, keys, args.force)
    # session.db 里的 username 也纳入名称映射（群聊名）
    unread = {}
    try:
        rel_s = "session/session.db"
        sdb = open_db(decrypt_db(os.path.join(db_dir, rel_s), rel_s, keys, force=args.force))
        for r in sdb.execute("SELECT username, unread_count FROM SessionTable").fetchall():
            u = r[0]
            if u:
                if u not in names:
                    names[u] = u
                try:
                    unread[u] = int(r[1] or 0)
                except Exception:
                    unread[u] = 0
        sdb.close()
    except Exception as e:
        print(f"[WARN] session 读取失败: {e}")

    if args.from_date or args.to_date:
        d0 = args.from_date or args.to_date
        d1 = args.to_date or args.from_date
        start_ts = int(dt.datetime.strptime(d0, "%Y-%m-%d").timestamp())
        end_ts = int(dt.datetime.strptime(d1, "%Y-%m-%d").timestamp()) + 86399
        day = f"{d0} ~ {d1}"
    else:
        day = args.date or dt.date.today().isoformat()
        start_ts = int(dt.datetime.strptime(day, "%Y-%m-%d").timestamp())
        end_ts = start_ts + 86399
    multi_day = (end_ts - start_ts) > 86400
    fmt = "%m-%d %H:%M" if multi_day else "%H:%M"

    md5_to_name = {_hashlib.md5(u.encode()).hexdigest(): u for u in names}
    # 本人 wxid：账号目录名去掉 _hash 后缀，如 <账号>_abcd -> <账号>
    owner = os.path.basename(os.path.normpath(db_dir + os.sep + "..")).rsplit("_", 1)[0]
    owner_nick = names.get(owner) or ""

    def tag_of(sname: str) -> str:
        if not sname:
            return "?"
        if sname == owner:
            return "我"
        disp = names.get(sname)
        if disp:
            return disp
        # 库内 id 未解析出用户名的，不要显示成裸数字
        return "?" if sname.isdigit() else sname

    agg = {}
    skipped = 0
    for base, path in iter_msg_dbs(db_dir):
        # 库文件本身早于目标日期之前就没被写过 → 不可能有新消息，直接跳过
        if os.path.getmtime(path) < start_ts - 300:
            skipped += 1
            continue
        rel = f"message/{base}"
        try:
            dec = decrypt_db(path, rel, keys, force=args.force)
        except Exception as e:
            print(f"[WARN] 跳过 {base}: {e}")
            continue
        conn = open_db(dec)
        try:
            # rowid -> user_name（real_sender_id 指向 Name2Id.rowid）
            id2name = {}
            try:
                for r in conn.execute("SELECT rowid, user_name FROM Name2Id").fetchall():
                    id2name[r[0]] = r[1]
                    id2name[str(r[0])] = r[1]
            except Exception:
                pass
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'").fetchall()]
            for tname in tables:
                h = tname[4:]
                row = conn.execute(
                    f"SELECT COUNT(*) c, MAX(create_time) mx FROM '{tname}' "
                    f"WHERE create_time BETWEEN ? AND ?", (start_ts, end_ts)).fetchone()
                if not row or not row[0]:
                    continue
                who = md5_to_name.get(h, f"<{h[:8]}>")
                rec = agg.setdefault(who, {"count": 0, "recv": 0, "sent": 0, "at_me": False,
                                           "first": None, "last": None, "all": [], "senders": set()})
                q = conn.execute(
                    f"SELECT create_time, real_sender_id, local_type, message_content, "
                    f"compress_content FROM '{tname}' WHERE create_time BETWEEN ? AND ? "
                    f"ORDER BY create_time", (start_ts, end_ts)).fetchall()
                rec["count"] += len(q)
                if q:
                    rec["first"] = min(rec["first"] or 2 ** 31, q[0][0] or 2 ** 31)
                    rec["last"] = max(rec["last"] or 0, q[-1][0] or 0)
                is_group = who.endswith("@chatroom")
                for r in q:
                    d = dict(r)
                    sid = d.get("real_sender_id")
                    sname = id2name.get(sid, sid if isinstance(sid, str) else "") or ""
                    if sname == owner:
                        rec["sent"] += 1
                    else:
                        rec["recv"] += 1
                        if sname:
                            rec["senders"].add(sname)
                        # 群里是否 @我 / @所有人
                        if is_group and not rec["at_me"]:
                            _k, _t = decode_content(d.get("message_content"), d.get("compress_content"),
                                                    None, d.get("local_type"))
                            if "所有人" in _t or (owner_nick and f"@{owner_nick}" in _t):
                                rec["at_me"] = True
                    rec["all"].append((d.get("create_time"), sname, d.get("local_type"),
                                       d.get("message_content"), d.get("compress_content")))
        except Exception as e:
            print(f"[WARN] 查询 {base} 失败: {e}")
        finally:
            conn.close()

    for rec in agg.values():
        rec["all"].sort(key=lambda x: x[0] or 0)
        rec["samples"] = rec["all"][-args.samples:]
        # 单向推送群：全是收到的消息且只有一个发送者（营销/公告群），明细折叠
        rec["is_push"] = (rec["recv"] == rec["count"] and len(rec["senders"]) <= 1
                          and rec["count"] >= 3)

    if skipped:
        print(f"（跳过 {skipped} 个今日之前就没写入过的库）")
    total = sum(v["count"] for v in agg.values())
    total_recv = sum(v["recv"] for v in agg.values())
    print(f"日期 {day}  共 {total} 条（收到 {total_recv} / 我发 {total - total_recv}），"
          f"涉及 {len(agg)} 个会话   本人={owner}\n")
    if not agg:
        print("（这一天没有任何消息记录）")
        return
    items = sorted(agg.items(), key=lambda kv: kv[1]["last"] or 0, reverse=True)
    for who, rec in items:
        t0 = dt.datetime.fromtimestamp(rec["first"]).strftime(fmt) if rec["first"] else "?"
        t1 = dt.datetime.fromtimestamp(rec["last"]).strftime(fmt) if rec["last"] else "?"
        disp = names.get(who) or who
        print(f"[{t0} - {t1}] {disp}  共 {rec['count']} 条（收到 {rec['recv']} / 我发 {rec['sent']}）")
        for ts, sname, ltype, mc, cc in rec["samples"]:
            kind, txt = decode_content(mc, cc, None, ltype)
            txt = strip_sender_prefix(txt, names)
            t2 = dt.datetime.fromtimestamp(ts).strftime(fmt)
            clean = txt.replace("\n", " ")[:110]
            tag = tag_of(sname)
            print(f"      {t2} {tag}|{kind}: {clean}")
        print()

    if args.md:
        def hhmm(x):
            return dt.datetime.fromtimestamp(x).strftime(fmt) if x else "?"

        # 时间分布：单日按小时，跨天按天
        if multi_day:
            dist = {}
            for rec in agg.values():
                for ts, *_ in rec["all"]:
                    k = dt.datetime.fromtimestamp(ts).strftime("%m-%d")
                    dist[k] = dist.get(k, 0) + 1
            dist_txt = "每日条数：" + "、".join(f"{k} {v} 条" for k, v in sorted(dist.items()))
        else:
            hist = {}
            for rec in agg.values():
                for ts, *_ in rec["all"]:
                    h = dt.datetime.fromtimestamp(ts).hour
                    hist[h] = hist.get(h, 0) + 1
            dist_txt = "活跃时段：" + "、".join(
                f"{h:02d}点 {n} 条" for h, n in sorted(hist.items(), key=lambda x: -x[1])[:3])

        latest = max((r["last"] or 0) for r in agg.values()) if agg else 0
        L = [f"# 微信消息概要 · {day}", "",
             f"> 共 **{total}** 条（收到 {total_recv} / 我发 {total - total_recv}）｜"
             f" {len(agg)} 个会话｜ {dist_txt}", "",
             f"> 最新一条：{hhmm(latest)}（数据源：本地微信库，只读）", ""]

        # 待回 / 未读信号
        signals = []
        for who, rec in items:
            last = rec["all"][-1] if rec["all"] else None
            cls = classify_session(who)
            unrd_n = unread.get(who, 0)
            flag = ""
            if last and last[1] != owner:
                if cls in ("客服", "公众号", "系统占位"):
                    flag = "通知"
                elif cls == "群聊":
                    # 群聊只有 @我 才算需要回复；否则不算待办
                    flag = "群@我" if rec["at_me"] else ""
                else:
                    flag = "待回"
            # 纯推送（通知）不进待办；有未读才列进来
            if (flag and flag != "通知") or unrd_n > 0:
                kind, txt = decode_content(last[3], last[4], None, last[2]) if last else ("", "")
                txt = strip_sender_prefix(txt, names).replace("\n", " ")[:60]
                signals.append((names.get(who) or who, flag or "未读",
                                f"{unrd_n} 条（累计）" if unrd_n else "",
                                f"{hhmm(last[0])} {txt}" if last else ""))
        if signals:
            L += ["## 需要处理的（待回 / 未读）", "",
                  "| 会话 | 信号 | 未读 | 最后一条 |", "|---|---|---|---|"]
            for name, pend, unrd, tail in signals:
                L.append(f"| {name} | {pend} | {unrd} | {tail} |")
            L.append("")

        # 汇总表
        def latest_text(rec) -> str:
            """取该会话最近一条有实质文本的消息，避免只看到 [表情]/[图片]"""
            fallback = ""
            for ts, sname, ltype, mc, cc in reversed(rec["all"]):
                kind, txt = decode_content(mc, cc, None, ltype)
                txt = strip_sender_prefix(txt, names).replace("\n", " ").strip()
                if kind == "文本" and txt:
                    return f"{tag_of(sname)}：{txt[:50]}"
                if not fallback and kind not in ("系统",):
                    fallback = kind
            return fallback

        L += ["## 会话汇总", "",
              "| 会话 | 类型 | 条数 | 收/发 | 时间跨度 | 状态 | 最近内容 |",
              "|---|---|---|---|---|---|---|"]
        for who, rec in items:
            last = rec["all"][-1] if rec["all"] else None
            cls = classify_session(who)
            if not (last and last[1] != owner):
                status = "已回"
            elif cls in ("客服", "公众号", "系统占位"):
                status = "通知"
            elif cls == "群聊":
                status = "群 @我" if rec["at_me"] else "群消息"
            else:
                status = "⏳ 待回"
            L.append(f"| {names.get(who) or who} | {classify_session(who)} | {rec['count']} | "
                     f"{rec['recv']}/{rec['sent']} | {hhmm(rec['first'])}–{hhmm(rec['last'])} | "
                     f"{status} | {latest_text(rec)} |")
        L.append("")

        # 明细：按类型分组（默认不输出，需要时加 --detail）
        if args.detail:
            groups = {}
            for who, rec in items:
                groups.setdefault(classify_session(who), []).append((who, rec))
            L += ["## 明细", ""]
            for gname in ("群聊", "私聊", "客服", "公众号", "自己", "系统占位"):
                if gname not in groups:
                    continue
                L += [f"### {gname}", ""]
                for who, rec in groups[gname]:
                    suffix = " · 单向推送" if rec.get("is_push") else ""
                    L += [f"**{names.get(who) or who}**{suffix}（{rec['count']} 条，"
                          f"收 {rec['recv']} / 发 {rec['sent']}，{hhmm(rec['first'])}–{hhmm(rec['last'])}）", ""]
                    show = rec["all"][-2:] if rec.get("is_push") else rec["all"][-args.keep:]
                    for ts, sname, ltype, mc, cc in show:
                        kind, txt = decode_content(mc, cc, None, ltype)
                        txt = strip_sender_prefix(txt, names).replace("\n", " ")[:200]
                        tag = tag_of(sname)
                        L.append(f"- {dt.datetime.fromtimestamp(ts).strftime(fmt)} {tag}｜{kind}：{txt}")
                    if rec.get("is_push"):
                        L.append(f"- ……（其余 {max(0, rec['count'] - 2)} 条为同源推送，已折叠）")
                    L.append("")

        with open(args.md, "w", encoding="utf-8") as f:
            f.write("\n".join(L))
        print(f"已写入 {args.md}")


def main():
    ap = argparse.ArgumentParser(description="wxcli — 本地微信只读查询")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tables", help="列出某库的表与列")
    p.add_argument("--db", required=True, help="相对路径，如 session/session.db")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_tables)

    p = sub.add_parser("sessions", help="最近会话列表")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_sessions)

    p = sub.add_parser("history", help="聊天记录")
    p.add_argument("talker")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--start", help="起始日期 YYYY-MM-DD")
    p.add_argument("--end", help="结束日期 YYYY-MM-DD")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("today", help="某日/某时间段消息概要")
    p.add_argument("--date", help="YYYY-MM-DD，默认今天")
    p.add_argument("--from", dest="from_date", help="起始日期 YYYY-MM-DD（配合 --to 用）")
    p.add_argument("--to", dest="to_date", help="结束日期 YYYY-MM-DD")
    p.add_argument("--samples", type=int, default=3, help="每个会话在终端展示几条")
    p.add_argument("--md", help="同时输出 Markdown 文件到指定路径")
    p.add_argument("--keep", type=int, default=30, help="--detail 时每个会话最多列几条")
    p.add_argument("--detail", action="store_true", help="Markdown 里附带逐条明细（默认不输出）")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_today)

    p = sub.add_parser("contacts", help="联系人")
    p.add_argument("--query", default="")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_contacts)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
