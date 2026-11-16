# -*- coding: utf-8 -*-
"""同一批次内按发送人分子文件夹，并提供补导时的落盘规则。

显示名：联系人备注 remark > 昵称 nick > fromusr；没有 fromusr 则「未知发送人」。
文件夹名清理规则与旁路 group_by_sender.clean_name 同款，但本模块独立实现，
不 import 旁路仓。名册放在 data_dir()（prefs 旁），只增不改，避免跨批次漂移。

联系人查询失败时退回 fromusr / 「未知发送人」，不让导出失败。
"""
import csv
import hashlib
import json
import os
import re
import shutil
import unicodedata

UNKNOWN = "未知发送人"
STATUS_DIRS = ("_完整", "_缩略图", "_头部缺失")
ROSTER_NAME = "sender_roster.json"
OWN_PAT = re.compile(
    r"^\d{3}_\d{4}-\d{2}-\d{2}_(image|video|file|link|chat)(_缩略)?\.[A-Za-z0-9]+$"
)
REPORT_HEADERS = [
    "序号", "收藏时间", "类型", "体积", "时长", "状态", "local_id",
    "fullmd5", "head256md5", "输出路径", "描述", "来源", "所属收藏", "还原",
]

_BAD = r'\/:*?"<>|'
_RESERVED = {"CON", "PRN", "AUX", "NUL",
             *{f"COM{i}" for i in range(1, 10)},
             *{f"LPT{i}" for i in range(1, 10)}}


def clean_name(s, fallback=""):
    """清掉控制符/零宽字符/非法字符，压空白，去尾部点与空格，限制 80 字。"""
    if not s:
        return fallback
    out = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat in ("Cf", "Cc", "Cs", "Co", "Cn"):
            continue
        if ch in _BAD:
            out.append("_")
            continue
        out.append(ch)
    t = re.sub(r"\s+", " ", "".join(out)).strip(" .")
    if not t:
        return fallback
    if t.split(".")[0].upper() in _RESERVED:
        t = "_" + t
    return t[:80]


def roster_path(path=None):
    if path:
        return path
    from common import data_dir
    return os.path.join(data_dir(), ROSTER_NAME)


def load_roster(path=None):
    p = roster_path(path)
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f) or {}
        return {str(k): str(v) for k, v in data.items() if k and v}
    except (OSError, ValueError, TypeError):
        return {}


def save_roster(roster, path=None):
    """只增不改：已有 wxid 的名字不被覆盖。"""
    p = roster_path(path)
    existing = load_roster(p)
    merged = dict(existing)
    for k, v in (roster or {}).items():
        if k and v and k not in merged:
            merged[k] = v
    os.makedirs(os.path.dirname(os.path.abspath(p)) or ".", exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, p)
    return merged


def load_contacts_safe():
    """尽最大努力读取联系人 {wxid: {nick, remark}}。任何失败都返回 {}。"""
    try:
        import sys
        from common import core_dir, ensure_vendor
        ensure_vendor()
        cd = core_dir()
        if cd not in sys.path:
            sys.path.insert(0, cd)
        import wxcli
        import paths
        keys, db_dir = wxcli.load_keys()
        if not db_dir or not os.path.isdir(db_dir):
            db_dir = os.path.join(paths.account_root(), "db_storage")
        rel = "contact/contact.db"
        path = os.path.join(db_dir, rel)
        if not os.path.isfile(path):
            return {}
        dec = wxcli.decrypt_db(path, rel, keys)
        conn = wxcli.open_db(dec)
        try:
            cols = [c[1] for c in conn.execute("PRAGMA table_info('Contact')").fetchall()]
            nick_col = "nick_name" if "nick_name" in cols else ("nick" if "nick" in cols else None)
            remark_col = "remark" if "remark" in cols else None
            username_col = "username" if "username" in cols else cols[0]
            nick_sel = nick_col or "''"
            remark_sel = remark_col or "''"
            sql = f"SELECT {username_col}, {nick_sel}, {remark_sel} FROM Contact"
            info = {}
            for u, n, r in conn.execute(sql).fetchall():
                if u:
                    info[str(u)] = {"nick": (n or "").strip(), "remark": (r or "").strip()}
            return info
        finally:
            conn.close()
    except Exception:
        return {}


def raw_display_name(wxid, contacts=None):
    """单个 wxid 的未去重显示名。"""
    if not wxid:
        return UNKNOWN
    d = (contacts or {}).get(wxid) or {}
    return clean_name(d.get("remark") or d.get("nick") or wxid, wxid or UNKNOWN)


def folder_names_for(wxids, contacts=None, roster=None, roster_file=None):
    """为一批 wxid 定文件夹名。名册里已有的沿用；新面孔撞名则加（wxid前10位）。"""
    contacts = contacts if contacts is not None else {}
    path = roster_file
    hist = dict(roster) if roster is not None else load_roster(path)
    unique = []
    seen = set()
    for u in wxids:
        u = (u or "").strip()
        if u not in seen:
            seen.add(u)
            unique.append(u)

    used = set(hist.values())
    final = {}
    newcomers = []
    for u in unique:
        if not u:
            final[u] = UNKNOWN
            used.add(UNKNOWN)
            continue
        if u in hist:
            final[u] = hist[u]
        else:
            newcomers.append(u)
    for u in sorted(newcomers):
        raw = raw_display_name(u, contacts)
        nm = raw
        if nm in used:
            nm = f"{raw}（{u[:10]}）"
            k = 2
            while nm in used:
                nm = f"{raw}（{u[:10]}-{k}）"
                k += 1
        final[u] = nm
        used.add(nm)

    additions = {}
    for u, nm in final.items():
        if u and u not in hist:
            additions[u] = nm
    if additions:
        if roster is None:
            save_roster(additions, path)
        else:
            hist.update(additions)
    return final


def is_own_filename(name):
    return bool(name and OWN_PAT.match(os.path.basename(name)))


def md5_of(path, bufsize=1 << 20):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(bufsize), b""):
            h.update(b)
    return h.hexdigest()


def md5_of_bytes(data):
    return hashlib.md5(data).hexdigest()


def write_report(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=REPORT_HEADERS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in REPORT_HEADERS})
    return path


def read_report(path):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _remove_empty_status_dirs(batch_path):
    for name in STATUS_DIRS:
        d = os.path.join(batch_path, name)
        if not os.path.isdir(d):
            continue
        try:
            leftovers = os.listdir(d)
        except OSError:
            continue
        if leftovers:
            continue
        try:
            os.rmdir(d)
        except OSError:
            pass


def sender_folder_for_row(batch_path, row, names):
    """优先沿用文件已经所在的发送人文件夹；否则按来源定名。"""
    old = (row.get("输出路径") or "").strip()
    if old:
        parent = os.path.dirname(old)
        base = os.path.basename(parent.rstrip("\\/"))
        try:
            same_batch = os.path.commonpath(
                [os.path.abspath(parent), os.path.abspath(batch_path)]
            ) == os.path.abspath(batch_path)
        except ValueError:
            same_batch = False
        if same_batch and base and base not in STATUS_DIRS and base != os.path.basename(batch_path):
            return base
    wxid = (row.get("来源") or row.get("fromusr") or "").strip()
    if not wxid:
        return UNKNOWN
    return names.get(wxid) or raw_display_name(wxid) or UNKNOWN


def archive_batch(batch_path, rows, contacts=None, roster_file=None):
    """把 cmd_export 写出的文件 move 进发送人文件夹，并就地更新行内「输出路径」。

    不 copy、不留双份。目标已存在且内容不同时不覆盖，保留原文件与原路径。
    返回 {wxid: 文件夹名}。
    """
    wxids = [(r.get("来源") or r.get("fromusr") or "").strip() for r in rows]
    names = folder_names_for(wxids, contacts=contacts or {}, roster_file=roster_file)
    batch_path = os.path.abspath(batch_path)
    for r in rows:
        old = (r.get("输出路径") or "").strip()
        if not old or not os.path.isfile(old):
            continue
        folder = names.get((r.get("来源") or "").strip()) or (
            UNKNOWN if not (r.get("来源") or "").strip() else names.get((r.get("来源") or "").strip())
        )
        if not folder:
            folder = UNKNOWN
        dest_dir = os.path.join(batch_path, folder)
        os.makedirs(dest_dir, exist_ok=True)
        fn = os.path.basename(old)
        dest = os.path.join(dest_dir, fn)
        src_abs, dst_abs = os.path.abspath(old), os.path.abspath(dest)
        if src_abs == dst_abs:
            r["输出路径"] = dest
            continue
        if os.path.exists(dest):
            try:
                same = md5_of(dest) == md5_of(old)
            except OSError:
                same = False
            if same:
                try:
                    os.remove(old)
                except OSError:
                    pass
                r["输出路径"] = dest
            # 内容不同：不覆盖，保留原路径
            continue
        shutil.move(old, dest)
        r["输出路径"] = dest
    _remove_empty_status_dirs(batch_path)
    return names


def complete_filename(seq, when, kind, ext, state="完整"):
    suffix = "_缩略" if state == "本地仅缩略图" else ""
    try:
        seq = int(seq)
    except (TypeError, ValueError):
        seq = 0
    when = (when or "")[:10]
    kind = kind or "other"
    ext = (ext or "bin").lstrip(".")
    return f"{seq:03d}_{when}_{kind}{suffix}.{ext}"


def apply_upgrade(batch_path, row, data, ext, folder_name=None, names=None):
    """把一条未完整条目升格为完整件：写入发送人文件夹、去掉 _缩略、删除本工具旧文件。

    若目标完整件已存在且内容不同，不覆盖，也不删除旧缩略图/头部缺失文件。
    成功返回新路径，跳过则返回 None。
    """
    if not data:
        return None
    folder = folder_name or sender_folder_for_row(batch_path, row, names or {})
    dest_dir = os.path.join(batch_path, folder)
    os.makedirs(dest_dir, exist_ok=True)
    fn = complete_filename(
        row.get("序号"),
        row.get("收藏时间") or row.get("when") or "",
        row.get("类型") or row.get("kind") or "",
        ext,
        state="完整",
    )
    dest = os.path.join(dest_dir, fn)
    old = (row.get("输出路径") or "").strip()
    if os.path.isfile(dest):
        try:
            same = md5_of(dest) == md5_of_bytes(data)
        except OSError:
            same = False
        if not same:
            return None
    else:
        with open(dest, "wb") as f:
            f.write(data)
    if old and os.path.isfile(old) and os.path.abspath(old) != os.path.abspath(dest):
        if is_own_filename(old):
            try:
                os.remove(old)
            except OSError:
                pass
    row["状态"] = "完整"
    row["输出路径"] = dest
    return dest


def leave_unresolved(row):
    """仍未取到原件：保留原文件，不重写一份相同缩略图。"""
    return (row.get("输出路径") or "").strip()
