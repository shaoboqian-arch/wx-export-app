# -*- coding: utf-8 -*-
"""导出引擎 —— 封装 V0.0.0 基线的解密/导出逻辑，不改写任何一行解密代码。

对外暴露 status / preflight / export / retry_missing / version。
日期区间口径：**起止都含当天**（内部把结束日 +1 天后传给基线脚本，
因为 fav_export.query() 的 --to 是半开区间）。

绝不覆盖：每次导出在目标文件夹下新建一个带时间戳的批次目录，
目录已存在就继续加序号，绝不复用、绝不往旧批次里写。
补导（retry_missing）是明确例外：写回同一批次目录，只处理未取到原件的条目。

默认短路径（kinds 省略）导出图片、视频文件，并把文本（type=1）和
链接卡片（type=5）写入清单，不另落单文件。文本/链接不走 resolve_item。
kinds=link 在核心里仍表示 type=14 聊天记录打包，不是公众号文章。
"""
import argparse
import contextlib
import csv
import ctypes
import datetime as dt
import glob
import io
import json
import os
import re
import shutil
import sys
import traceback

import export_list as el
import chatrecord as cr
import report_html
import report_md
import sender_archive as sa
from common import core_dir, DEFAULT_KINDS, VERSION, ensure_vendor


CHAT_DIR = "聊天记录"

# 只用于「是不是可读名字」的判断：全角空格 / 韩文填充符等，微信里常被当昵称
_INVISIBLE = "\u3164\u115f\u1160\u2800 \t\r\n"


def _visible(s):
    return bool((s or "").strip(_INVISIBLE))


def _self_of(content):
    """这条收藏的归属人（<tousr>），用于气泡左右分栏。认不出就返回空。"""
    m = re.search(r"<tousr>(.*?)</tousr>", content or "", re.S)
    return (m.group(1).strip() if m else "")


def _pack_title(rec, name):
    """聊天记录包的标题。

    多数条目在库里没有可用的 <title>，其余是微信在**渲染时**拼的，
    规则就是「"<显示名>"的聊天记录」（与微信展示规则一致）。
    另外一部分库里的 <title> 长得像「ㅤ的聊天记录」——名字是不可见字符，
    对人没有信息量，这种也按上面的规则重拼。
    """
    raw = (rec.get("title") or "").strip()
    usable = _visible(raw) and not raw.lstrip(_INVISIBLE).startswith("的聊天记录")
    if usable:
        return raw
    who = name if _visible(name) and name != sa.UNKNOWN else ""
    if who:
        return f'"{who}"的聊天记录'
    return f"聊天记录（{len(rec.get('items') or [])} 条）"


def _iso(stime, fallback=""):
    """聊天记录子项的时间是「2015-1-14 上午11:37」这种中文格式，转成可排序的 ISO。

    认不出就退回整条收藏的时间，绝不留空 —— 清单按时间排序，空值会把行吹到最前。
    """
    s = (stime or "").strip()
    m = re.match(r"(\d{4})\D(\d{1,2})\D(\d{1,2})\s*(上午|下午|凌晨|晚上|中午)?\s*(\d{1,2}):(\d{2})", s)
    if not m:
        return fallback
    y, mo, d, half, hh, mi = m.groups()
    try:
        h = int(hh)
        if half in ("下午", "晚上") and h < 12:
            h += 12
        elif half == "凌晨" and h == 12:
            h = 0
        elif half == "中午" and h < 11:
            h += 12
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d} {h:02d}:{int(mi):02d}"
    except (TypeError, ValueError):
        return fallback


def _chat_rows(fe, frm, query_to):
    conn = fe.open_fav_db()
    try:
        cur = conn.execute(
            "SELECT local_id, update_time, content, fromusr FROM fav_db_item "
            "WHERE update_time >= ? AND update_time < ? AND type = 14 "
            "ORDER BY update_time",
            (fe.ts(frm), fe.ts(query_to)))
        return cur.fetchall()
    finally:
        conn.close()


def _chat_indices(fe, parsed):
    """聊天记录子项的媒体索引。只在 cmd_export 没留下现成索引时才单独建。"""
    want = set()
    for rec in parsed:
        for it in rec["items"]:
            if cr.is_media(it) or cr.is_audio(it):
                if it["size"]:
                    want.add(it["size"])
                if it["thumb_size"]:
                    want.add(it["thumb_size"])
    idx = fe.build_size_index()
    pidx = fe.build_plain_index(want)
    hidx = fe.build_head_index(fe.KEY)
    return idx, pidx, hidx


def _chat_manifest_items(fe, frm, query_to, batch_dir=None):
    """补导场景用：只重建聊天记录的清单行，不碰任何文件。

    媒体子项的行已经在批次状态里（补导会重写），这里只补
    父行 + 正文行 + 仅元数据行，否则补导一次聊天记录里的正文就没了。
    已生成的 index.html 会被复用（不重写页面），父行的「输出路径」据此回填。
    """
    out = []
    for lid, ut, content, fromusr in _chat_rows(fe, frm, query_to):
        when = dt.datetime.fromtimestamp(ut).strftime("%Y-%m-%d %H:%M:%S")
        rec = cr.expand(content)
        folder, html_path = _find_pack_html(batch_dir, lid)
        name = sa.raw_display_name(fromusr) if _visible(fromusr) else ""
        title = _pack_title(rec, folder or name)
        out.append({
            "when": when, "type_zh": "聊天记录", "title": title,
            "state": el.STATE_CHAT_HTML if html_path else el.STATE_CHAT_HTML_FAIL,
            "summary": f"共 {len(rec['items'])} 条", "fromusr": fromusr or "",
            "parent": lid, "path": html_path,
        })
        for it in rec["items"]:
            item_when = _iso(it["when"], when)
            sender = it["fromusr"] or fromusr or ""
            if it["text"]:
                out.append({
                    "when": item_when, "type_zh": "聊天记录", "title": "",
                    "body": it["text"],
                    "summary": " · ".join(x for x in (it["sender"], it["when"]) if x),
                    "fromusr": sender, "parent": lid, "path": "",
                })
            elif cr.is_meta_only(it):
                out.append({
                    "when": item_when, "type_zh": "聊天记录",
                    "title": it["title"] or f"{it['name']}（仅元数据）",
                    "summary": " · ".join(x for x in (it["name"], it["sender"], it["when"]) if x)
                               + "；本机无本体",
                    "fromusr": sender, "parent": lid, "path": "",
                })
    return out


def _find_pack_html(batch_dir, lid):
    """在已有批次里找某个聊天记录包的 index.html，返回 (发送人目录名, 路径)。"""
    if not batch_dir:
        return "", ""
    try:
        base = os.path.join(batch_dir, "*", CHAT_DIR, f"{int(lid):06d}_*", "index.html")
        hits = sorted(glob.glob(base))
    except (TypeError, ValueError, OSError):
        return "", ""
    if not hits:
        return "", ""
    p = hits[0]
    return os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(p)))), p


def _upgrade_chat_subitem(fe, row, data):
    """把聊天记录里的一个子项从缩略图升成原件。

    与顶层媒体的 difference：目标是**包目录**（<发送人>/聊天记录/<lid>_<日期>/），
    不是发送人根目录；文件名保留序号与类型，只去掉「_缩略」。
    已有同目录不同内容的完整件时不覆盖，返回空串。
    """
    old = (row.get("输出路径") or "").strip()
    if not data or not old:
        return ""
    d = os.path.dirname(old)
    stem, old_ext = os.path.splitext(os.path.basename(old))
    if stem.endswith("_缩略"):
        stem = stem[:-len("_缩略")]
    ext = (fe.sniff_tail(data) or old_ext.lstrip(".") or "bin").lower()
    dest = os.path.join(d, f"{stem}.{ext}")
    if os.path.abspath(dest) == os.path.abspath(old):
        dest = os.path.join(d, f"{stem}_1.{ext}")
    if os.path.isfile(dest):
        try:
            if sa.md5_of(dest) != sa.md5_of_bytes(data):
                return ""
        except OSError:
            return ""
    else:
        os.makedirs(d, exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
    if os.path.isfile(old) and os.path.abspath(old) != os.path.abspath(dest):
        if sa.is_own_filename(old):
            try:
                os.remove(old)
            except OSError:
                pass
    row["状态"] = "完整"
    row["输出路径"] = dest
    return dest


def _export_chat_records(out, fe, frm, query_to, names, start_seq,
                         idx=None, pidx=None, hidx=None, emit=None,
                         only_lids=None):
    """把 type=14 聊天记录展开落地。

    媒体子项：复用 fe.resolve_item 认领本地容器，写到
              <批次>/<发送人>/聊天记录/<lid>_<日期>/ 下，单独一层避免与图片视频混放。
    文本子项：正文进批次元数据与《导出报告.md》，不落单文件（避免上千条零碎文件）。
    语音/位置/未知：只记来源与时间，标「仅元数据」——本机没有它们的本体。

    **每个包另出一份 index.html**（V0.3.1）：一包一目录，附件与 html 同目录、
    相对路径引用，可离线打开。语音/视频/位置尽量补齐：语音画时长条、视频给封面帧、
    位置给地址 + 地图链接，都没有就写清楚「本机未缓存」，不留空白。

    返回 (报告行, 清单项, 统计)。报告行保留「所属收藏」，
    补导逻辑据此按附件体积与 md5 认领（不能按整包 lid 重取）。
    """
    raw = _chat_rows(fe, frm, query_to)
    if only_lids:
        raw = [r for r in raw if r[0] in only_lids]
    stat = {"records": len(raw), "items": 0, "text": 0, "media": 0,
            "files": 0, "完整": 0, "本地仅缩略图": 0, "未定位本体": 0,
            "仅元数据": 0, "packs_ok": 0, "packs_failed": 0, "html": 0}
    if not raw:
        return [], [], stat

    parsed = [cr.expand(c) for _lid, _ut, c, _f in raw]
    if idx is None or pidx is None or hidx is None:
        idx, pidx, hidx = _chat_indices(fe, parsed)
    else:
        # cmd_export 建的明文索引只按整条收藏的体积，包内语音子项的体积不在里面。
        # 有语音候选就按需补建一次，否则「被当文件发过的语音」永远认领不到。
        audio_sizes = {it["size"] for rec in parsed for it in rec["items"]
                       if cr.is_audio(it) and it["size"]}
        if audio_sizes:
            want = set(audio_sizes)
            for rec in parsed:
                for it in rec["items"]:
                    if cr.is_media(it) and it["size"]:
                        want.add(it["size"])
            with contextlib.redirect_stdout(io.StringIO()):
                pidx = fe.build_plain_index(want)

    report_rows, chat_items, failed_packs = [], [], []
    seq = int(start_seq or 0)

    for (lid, ut, content, fromusr), rec in zip(raw, parsed):
        when = dt.datetime.fromtimestamp(ut).strftime("%Y-%m-%d %H:%M:%S")
        who = fromusr or ""
        self_id = _self_of(content)
        folder = names.get(who) or sa.raw_display_name(who) or sa.UNKNOWN
        sub_dir = os.path.join(out, folder, CHAT_DIR, f"{lid:06d}_{when[:10]}")
        title = _pack_title(rec, folder)
        entries = []
        pstat = {"完整": 0, "本地仅缩略图": 0, "未定位本体": 0, "仅元数据": 0}

        for n, it in enumerate(rec["items"], 1):
            stat["items"] += 1
            item_when = _iso(it["when"], when)
            sender = it["fromusr"] or who
            # 子项的 datasrcname 有时是不可见字符（对方昵称就是这样），
            # 那种情况退回收藏来源的显示名，否则页面上会出现一片空白。
            shown = it["sender"] if _visible(it["sender"]) else (
                folder if _visible(folder) else (sender or ""))
            base = {"when": item_when, "sender": shown,
                    "me": bool(self_id) and sender == self_id}

            if it["text"]:
                stat["text"] += 1
                chat_items.append({
                    "when": item_when, "type_zh": "聊天记录",
                    "title": "", "body": it["text"],
                    "summary": " · ".join(x for x in (it["sender"], it["when"]) if x),
                    "fromusr": sender, "parent": lid, "path": "",
                })
                entries.append({**base, "kind": "text", "text": it["text"]})
                continue

            if cr.is_audio(it):
                # 语音：先试本地音频（被当文件发过的 dt8 常常就在 msg/file 里），
                # 拿不到再退回「仅元数据」占位。PC 端从不保留 silk 语音本体。
                stat["语音候选"] = stat.get("语音候选", 0) + 1
                vstate, vdata, _vtier = "", None, ""
                if it["md5"]:
                    probe = {"lid": lid, "type": 14, "kind": "voice",
                             "when": item_when, "size": it["size"], "md5": it["md5"],
                             "thumb_size": it["thumb_size"], "md5t": it["md5t"],
                             "desc": it["title"] or "", "fromusr": sender}
                    with contextlib.redirect_stdout(io.StringIO()):
                        vstate, vdata, _vtier = fe.resolve_item(
                            probe, idx, pidx, fe.KEY, hidx)
                vpath = ""
                if vdata is not None:
                    ext = (it["fmt"] or "").lower() or fe.sniff_tail(vdata) or "mp3"
                    if ext in ("riff", "bin", ""):
                        ext = "mp3"
                    os.makedirs(sub_dir, exist_ok=True)
                    vpath = os.path.join(
                        sub_dir, f"{n:03d}_{item_when[:10]}_voice.{ext}")
                    with open(vpath, "wb") as f:
                        f.write(vdata)
                    stat["files"] += 1
                    stat["语音可播"] = stat.get("语音可播", 0) + 1
                else:
                    stat["仅元数据"] += 1
                    pstat["仅元数据"] += 1
                dur_txt = f"{int(it['duration'])} 秒" if it["duration"] else "语音"
                chat_items.append({
                    "when": item_when, "type_zh": "聊天记录",
                    "title": it["title"] or f"{it['name']}（仅元数据）",
                    "summary": " · ".join(x for x in (it["name"], it["sender"], it["when"]) if x)
                               + ("；已导出音频" if vpath else "；本机无本体"),
                    "fromusr": sender, "parent": lid, "path": vpath,
                })
                entries.append({**base, "kind": "voice", "duration": it["duration"],
                                "fmt": it["fmt"], "size": it["size"], "path": vpath,
                                "dur_txt": dur_txt})
                continue

            if cr.is_meta_only(it):
                stat["仅元数据"] += 1
                pstat["仅元数据"] += 1
                chat_items.append({
                    "when": item_when, "type_zh": "聊天记录",
                    "title": it["title"] or f"{it['name']}（仅元数据）",
                    "summary": " · ".join(x for x in (it["name"], it["sender"], it["when"]) if x)
                               + "；本机无本体",
                    "fromusr": sender, "parent": lid, "path": "",
                })
                if (it["lat"] and it["lng"]) or it["place"] or it["poi"]:
                    # 有坐标就给地图链接；只有地名时也走位置块（渲染成「无坐标」+ 地址），
                    # 别退化成「位置（仅元数据）」这种没信息量的文字。
                    entries.append({**base, "kind": "location", "lat": it["lat"],
                                    "lng": it["lng"], "place": it["place"],
                                    "poi": it["poi"]})
                elif it["kind"] in ("article", "link") or it.get("url"):
                    # 包内链接卡片：给生成器走 link 分支，别退化成「其他」
                    entries.append({**base, "kind": "link",
                                    "url": it.get("url") or "",
                                    "title": it["title"] or it["text"] or "链接"})
                elif it["kind"] == "voice":
                    entries.append({**base, "kind": "voice", "duration": it["duration"],
                                    "fmt": it["fmt"], "size": it["size"], "path": ""})
                else:
                    entries.append({**base, "kind": "other",
                                    "text": it["title"] or f"{it['name']}（仅元数据）"})
                continue

            if not cr.is_media(it):
                continue

            stat["media"] += 1
            seq += 1
            probe = {"lid": lid, "type": 14, "kind": it["kind"], "when": item_when,
                     "size": it["size"], "md5": it["md5"],
                     "thumb_size": it["thumb_size"], "md5t": it["md5t"],
                     "desc": it["title"] or "", "fromusr": sender}
            state, data, _tier = fe.resolve_item(probe, idx, pidx, fe.KEY, hidx)
            stat[state] = stat.get(state, 0) + 1
            pstat[state] = pstat.get(state, 0) + 1
            if _tier == "缓存还原":
                stat["缓存还原"] = stat.get("缓存还原", 0) + 1
            path = ""
            if data is not None:
                ext = fe.sniff_tail(data) or "bin"
                suffix = "_缩略" if state == "本地仅缩略图" else ""
                os.makedirs(sub_dir, exist_ok=True)
                path = os.path.join(
                    sub_dir, f"{n:03d}_{item_when[:10]}_{it['kind']}{suffix}.{ext}")
                with open(path, "wb") as f:
                    f.write(data)
                stat["files"] += 1
            entries.append({**base, "kind": it["kind"], "state": state, "path": path,
                            "thumb": path if state == "本地仅缩略图" else "",
                            "title": it["title"], "size": it["size"],
                            "duration": it["duration"], "fmt": it["fmt"]})
            row = {
                "序号": seq, "收藏时间": item_when, "类型": it["kind"],
                "体积": it["size"] or "", "时长": it["duration"] or "",
                "状态": state,
                "local_id": lid, "fullmd5": it["md5"], "head256md5": "",
                "输出路径": path,
                "描述": (it["title"] or it["text"] or "")[:60],
                "来源": sender, "所属收藏": lid, "还原": _tier or "",
            }
            report_rows.append(row)
            if emit:
                emit("item", item={
                    "i": seq, "kind": it["kind"], "group": it["kind"],
                    "state": state, "ok": state == "完整",
                    "name": os.path.basename(path), "when": item_when,
                    "desc": row["描述"], "fromusr": sender,
                    "lid": lid, "local_id": lid, "path": path,
                    "reason": "" if state == "完整" else REASONS.get(state, state),
                }, index=seq, total=stat["media"])

        # ---- 一包一 HTML ----
        html_path = os.path.join(sub_dir, "index.html")
        ok = True
        try:
            report_html.render({
                "title": title, "when": when, "who": folder, "fromusr": who,
                "stats": pstat, "entries": entries,
            }, html_path)
            stat["packs_ok"] += 1
            stat["html"] += 1
            state_zh = el.STATE_CHAT_HTML
        except Exception as e:  # noqa: BLE001 —— 单个包失败不能拖垮整批
            ok = False
            stat["packs_failed"] += 1
            state_zh = el.STATE_CHAT_HTML_FAIL
            reason = f"{type(e).__name__}: {e}"
            failed_packs.append({"local_id": lid, "title": title, "reason": reason})
            html_path = ""
            if emit:
                emit("pack", pack={"ok": False, "lid": lid, "title": title,
                                   "reason": reason})

        chat_items.append({
            "when": when, "type_zh": "聊天记录", "title": title,
            "state": state_zh,
            "summary": f"共 {len(rec['items'])} 条" + ("" if ok else "；HTML 生成失败"),
            "fromusr": who, "parent": lid, "path": html_path,
        })
        if ok and emit:
            emit("pack", pack={"ok": True, "lid": lid, "title": title, "reason": ""})

    stat["failed_packs"] = failed_packs
    return report_rows, chat_items, stat


class EngineError(Exception):
    pass


def _load():
    ensure_vendor()          # 源码态也能用 Crypto；冻结态自动跳过
    cd = core_dir()
    if cd not in sys.path:
        sys.path.insert(0, cd)
    import fav_export as fe
    import paths
    return fe, paths


def _date(s, label):
    try:
        return dt.datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise EngineError(f"{label} 必须是 YYYY-MM-DD，收到 {s!r}")


def _range(frm, to):
    a, b = _date(frm, "起始日期"), _date(to, "结束日期")
    if b < a:
        raise EngineError(f"结束日期 {to} 早于起始日期 {frm}")
    # 基线脚本的 --to 是半开区间；对外口径是「含当天」，故 +1 天
    return a, b, (b + dt.timedelta(days=1)).isoformat()


def _keys_state():
    home = os.path.expanduser("~")
    cfg = os.path.join(home, ".wechat-cli", "config.json")
    allkeys = os.path.join(home, ".wechat-cli", "all_keys.json")
    image_key = ""
    try:
        with open(cfg, encoding="utf-8") as f:
            image_key = (json.load(f).get("image_aes_key") or "").strip()
    except OSError:
        pass
    return {
        "db_keys": os.path.isfile(allkeys),
        "image_key": bool(image_key),
        "config_dir": os.path.join(home, ".wechat-cli"),
    }


def _resolve_root(explicit=None):
    """定位微信账号目录。

    优先用基线 paths.account_root()；它失配时（微信装在非默认位置、或
    config.json 的 db_dir 层级与其假设不同）退到内置候选扫描，避免用户在
    陌生电脑上卡在「找不到账号目录」。
    """
    if explicit:
        return os.path.abspath(explicit)
    _load()
    r = ""
    try:
        import paths as _paths
        r = _paths.account_root()
    except Exception:
        r = ""
    if r and os.path.isdir(os.path.join(r, "db_storage")):
        return r
    try:
        rs = _wxkeys().candidate_roots()
    except Exception:
        rs = []
    if rs:
        return rs[0]["path"]
    if r:
        return r
    raise EngineError(
        "找不到微信账号目录：请在「初始化本机环境」里点「浏览…」，"
        "选择包含 db_storage 的账号目录")


def _setup(root=None, image_key=None):
    st = _keys_state()
    if not st["db_keys"]:
        raise EngineError(
            f"缺少数据库密钥 {os.path.join(st['config_dir'], 'all_keys.json')}；"
            "请在主界面点「初始化本机环境」提取")
    fe, _paths = _load()
    r = _resolve_root(root)
    fe.R = r
    fe.FAVDB = os.path.join(r, "db_storage", "favorite", "favorite.db")
    fe.FBASE = os.path.join(r, "business", "favorite")
    if not os.path.isfile(fe.FAVDB):
        raise EngineError(f"收藏库不存在：{fe.FAVDB}")
    fe.KEY = fe.resolve_key(image_key)
    return fe


def _kinds(kinds):
    if not kinds:
        return set(DEFAULT_KINDS)
    if isinstance(kinds, str):
        return {k.strip() for k in kinds.split(",") if k.strip()}
    return set(kinds)


def _query_text_links(fe, frm, query_to):
    """按 type 读取文本和链接卡片，不走 resolve_item / kind_of。"""
    conn = fe.open_fav_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT local_id, type, update_time, content, fromusr "
            "FROM fav_db_item WHERE update_time >= ? AND update_time < ? "
            "AND type IN (1, 5) ORDER BY update_time",
            (fe.ts(frm), fe.ts(query_to)))
        texts, links = [], []
        for lid, typ, ut, content, fromusr in cur.fetchall():
            when = dt.datetime.fromtimestamp(ut).strftime("%Y-%m-%d %H:%M:%S")
            base = {"lid": lid, "type": typ, "when": when, "fromusr": fromusr or ""}
            if typ == 1:
                rec = dict(base)
                rec["kind"] = "text"
                rec["body"] = el.parse_text_content(content)
                texts.append(rec)
            elif typ == 5:
                rec = dict(base)
                rec["kind"] = "article"
                rec.update(el.parse_link_content(content))
                links.append(rec)
        return texts, links
    finally:
        conn.close()


def _census_types(fe, frm, query_to):
    conn = fe.open_fav_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT type, COUNT(*) FROM fav_db_item "
            "WHERE update_time >= ? AND update_time < ? GROUP BY type",
            (fe.ts(frm), fe.ts(query_to)))
        counts = {int(t): int(n) for t, n in cur.fetchall()}
    finally:
        conn.close()
    return el.census_from_types(counts)


def version():
    return {"name": "wx-fav-export", "version": VERSION}


def status(root=None):
    """只读：报告本机是否具备导出条件。任何异常都不抛出，只体现在 ready/message。"""
    out = {"version": VERSION, **_keys_state()}
    out["account_root"] = ""
    out["favorite_db"] = False
    out["favorite_base"] = False
    try:
        # 任何底层 print 都不能漏到 MCP 的 stdout（协议通道）
        with contextlib.redirect_stdout(io.StringIO()):
            fe, _paths = _load()
            r = _resolve_root(root)
            out["account_root"] = r
            out["favorite_db"] = os.path.isfile(os.path.join(r, "db_storage", "favorite", "favorite.db"))
            out["favorite_base"] = os.path.isdir(os.path.join(r, "business", "favorite"))
    except Exception as e:
        out["message"] = f"账号目录未就绪：{e}"
    out["ready"] = bool(out["favorite_db"] and out["db_keys"] and out["image_key"])
    if out["ready"]:
        out["message"] = "就绪"
    elif "message" not in out:
        missing = []
        if not out["favorite_db"]:
            missing.append("收藏库")
        if not out["db_keys"]:
            missing.append("数据库密钥 all_keys.json")
        if not out["image_key"]:
            missing.append("图片密钥 image_aes_key")
        out["message"] = "缺少：" + "、".join(missing)
    return out


def preflight(frm, to, kinds=None, root=None, image_key=None):
    """只读预检：区间内真实条数与类型分布，不写任何文件。

    total / by_kind 仍是 resolve_item 类型（默认 image,video），以保持既有对账。
    省略 kinds 时额外给出 text_count / link_count（文本与链接卡片）。
    """
    a, b, query_to = _range(frm, to)
    include_tl = el.kinds_omitted(kinds)
    ks = _kinds(kinds)
    fe = _setup(root, image_key)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rows = fe.query(a.isoformat(), query_to, ks)
    by_kind = {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    out = {
        "ok": True,
        "from": a.isoformat(),
        "to": b.isoformat(),
        "range_inclusive": True,
        "kinds": sorted(ks),
        "total": len(rows),
        "by_kind": by_kind,
        "earliest": min((r["when"] for r in rows), default=""),
        "latest": max((r["when"] for r in rows), default=""),
        "senders": len({r["fromusr"] for r in rows if r["fromusr"]}),
        "account_root": fe.R,
        "include_text_links": include_tl,
        "text_count": 0,
        "link_count": 0,
    }
    if include_tl:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                texts, links = _query_text_links(fe, a.isoformat(), query_to)
            out["text_count"] = len(texts)
            out["link_count"] = len(links)
        except Exception:
            out["text_count"] = None
            out["link_count"] = None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                out["census"] = _census_types(fe, a.isoformat(), query_to)
        except Exception:
            out["census"] = None
    return out


def _unique_batch(dest, base):
    p = os.path.join(dest, base)
    n = 1
    while os.path.exists(p):
        p = os.path.join(dest, f"{base}-{n}")
        n += 1
    return p


REASONS = {
    "本地仅缩略图": "本地只有缩略图，未取到原件",
    "未定位本体": "本机无本体（原件只在微信 CDN）",
    "头部缺失": "拿到容器但缺图片密钥",
}

RETRY_STATE = "_补导状态.json"


def _hide_file(path):
    """Windows 下把批次内部状态文件设为隐藏属性。"""
    if os.name != "nt":
        return
    try:
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)
    except Exception:
        pass


def _unhide_file(path):
    """覆盖写入前解除隐藏属性，避免 Windows 上 os.replace 触发 PermissionError。"""
    if os.name != "nt" or not os.path.exists(path):
        return
    try:
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x80)
    except Exception:
        pass


def _rel_in_batch(batch_path, path):
    """把批次目录内的绝对路径转为 POSIX 相对路径，避免把本机路径写进状态文件。"""
    s = ("" if path is None else str(path)).strip()
    if not s:
        return ""
    base = os.path.abspath(batch_path)
    p = os.path.abspath(s) if os.path.isabs(s) else os.path.abspath(os.path.join(base, s))
    try:
        if os.path.commonpath([base, p]) == base:
            return os.path.relpath(p, base).replace("\\", "/")
    except ValueError:
        pass
    return s


def _abs_in_batch(batch_path, path):
    """把状态文件里保存的相对路径还原为规范绝对路径。"""
    s = ("" if path is None else str(path)).strip()
    if not s:
        return ""
    if os.path.isabs(s):
        return os.path.normpath(s)
    return os.path.normpath(os.path.join(os.path.abspath(batch_path), s))


def write_retry_state(batch_path, *, frm, to, kinds, include_text_links, pending_lids,
                      rows=None, manifest_rows=None):
    """批次内机器状态（补导与对账）。严禁写入密钥、image_key 或账号完整路径。"""
    if isinstance(kinds, str):
        ks = [k.strip() for k in kinds.split(",") if k.strip()]
    else:
        ks = sorted(kinds)
    lids = []
    for x in pending_lids or []:
        try:
            lids.append(int(x))
        except (TypeError, ValueError):
            continue
    data = {
        "from": frm,
        "to": to,
        "kinds": ks,
        "include_text_links": bool(include_text_links),
        "pending_lids": lids,
    }
    if rows is not None:
        ser_rows = []
        for r in rows:
            item = {k: ("" if r.get(k) is None else str(r.get(k))) for k in sa.REPORT_HEADERS}
            item["输出路径"] = _rel_in_batch(batch_path, item.get("输出路径"))
            ser_rows.append(item)
        data["rows"] = ser_rows
    if manifest_rows is not None:
        ser_manifest = []
        for r in manifest_rows:
            if isinstance(r, dict):
                item = {k: ("" if r.get(k) is None else str(r.get(k))) for k in el.MANIFEST_HEADERS}
            else:
                item = {
                    k: ("" if idx >= len(r) or r[idx] is None else str(r[idx]))
                    for idx, k in enumerate(el.MANIFEST_HEADERS)
                }
            item["输出路径"] = _rel_in_batch(batch_path, item.get("输出路径"))
            ser_manifest.append(item)
        data["manifest"] = ser_manifest
    path = os.path.join(batch_path, RETRY_STATE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _unhide_file(path)
    os.replace(tmp, path)
    _hide_file(path)
    return path


def read_retry_state(batch_path):
    path = os.path.join(os.path.abspath(batch_path), RETRY_STATE)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    for k in list(data.keys()):
        lk = str(k).lower()
        if "key" in lk or "account" in lk or "secret" in lk or "aes" in lk:
            data.pop(k, None)
    return data


def _read_state_table(batch_path, state_key):
    """从 _补导状态.json 读取表格数组，还原输出路径并将字段统一转为 str（与 CSV 口径一致）。"""
    batch_abs = os.path.abspath(batch_path)
    st = read_retry_state(batch_abs)
    records = st.get(state_key)
    if not isinstance(records, list):
        return None
    out = []
    for r in records:
        if not isinstance(r, dict):
            continue
        item = {k: ("" if v is None else str(v)) for k, v in r.items()}
        item["输出路径"] = _abs_in_batch(batch_abs, item.get("输出路径"))
        out.append(item)
    return out


def read_batch_rows(batch_path):
    """读取批次对账行，优先走 _补导状态.json，兼容旧版含 _导出报告.csv 的批次。"""
    rows = _read_state_table(batch_path, "rows")
    if rows is not None:
        return rows
    return sa.read_report(os.path.join(os.path.abspath(batch_path), "_导出报告.csv"))


def read_batch_manifest(batch_path):
    """读取批次导出清单行，优先走 _补导状态.json，兼容旧版含 _导出清单.csv 的批次。"""
    mrows = _read_state_table(batch_path, "manifest")
    if mrows is not None:
        return mrows
    csv_path = os.path.join(os.path.abspath(batch_path), "_导出清单.csv")
    if not os.path.isfile(csv_path):
        return []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _pending_lids(rows):
    out = []
    for r in rows:
        if (r.get("状态") or "") == "完整":
            continue
        if (r.get("类型") or "") not in ("image", "video"):
            continue
        # 聊天记录的子项不能按 lid 单条重取：一个 lid 对应整包几十上百条子项，
        # 重取只会拿回包里第一条。它们不参与补导（见报告「所属收藏」列）。
        if str(r.get("所属收藏") or "").strip():
            continue
        try:
            lid = int(r.get("local_id") or 0)
        except (TypeError, ValueError):
            continue
        if lid:
            out.append(lid)
    return out


def _items_from_report(rows, existing_items=None):
    by_i = {it["i"]: it for it in (existing_items or []) if it.get("i") is not None}
    items = []
    by_kind = {g: {"total": 0, "ok": 0, "failed": 0} for g in ("image", "video", "other")}
    for r in rows:
        try:
            i = int(r.get("序号") or 0)
        except (TypeError, ValueError):
            i = 0
        kind = r.get("类型") or "other"
        g = kind if kind in ("image", "video") else "other"
        state = r.get("状态") or ""
        ok = state == "完整"
        it = dict(by_i.get(i) or {})
        lid = r.get("local_id") or it.get("local_id") or it.get("lid") or ""
        it.update({
            "i": i,
            "kind": kind,
            "group": g,
            "state": state,
            "ok": ok,
            "name": os.path.basename(r.get("输出路径") or it.get("name") or "") or it.get("name") or "",
            "when": r.get("收藏时间") or it.get("when") or "",
            "desc": (r.get("描述") or it.get("desc") or "")[:60],
            "fromusr": r.get("来源") or it.get("fromusr") or "",
            "lid": lid,
            "local_id": lid,
            "path": r.get("输出路径") or "",
            "reason": "" if ok else REASONS.get(state, state),
        })
        items.append(it)
        if state != "仅元数据":
            by_kind[g]["total"] += 1
            by_kind[g]["ok" if ok else "failed"] += 1
    # 「仅元数据」不是失败：本机从来没有这个件，不该进未取到原件清单
    failed = [it for it in items if not it["ok"] and it["state"] != "仅元数据"]
    for it in failed:
        el.enrich_failed_item(it)
    return items, failed, by_kind


def _write_sidecars(out, *, frm, to, include_tl, ks, counts, written,
                    rows, failed_items, texts, links, census, chat_items=None,
                    chat=None, pending_lids=None, sender_names=None):
    """批次内人读报告《导出报告.md》；机器状态统一落进隐藏的 _补导状态.json。

    不再在批次目录残留 _导出报告.csv / _导出清单.csv。
    """
    mrows = el.build_manifest_rows(rows, texts if include_tl else [],
                                   links if include_tl else [],
                                   chat_items if include_tl else [])
    report_md.write_report(
        out,
        frm=frm, to=to,
        include_text_links=include_tl, resolve_kinds=ks,
        counts=counts, written=written, media_total=len(rows),
        text_count=len(texts) if include_tl else 0,
        link_count=len(links) if include_tl else 0,
        census=census, chat=chat, failed_items=failed_items,
        report_rows=rows, manifest_rows=mrows, pending_lids=pending_lids,
        sender_names=sender_names,
    )
    return mrows, report_md.report_md_path(out)


def export(frm, to, dest, kinds=None, root=None, image_key=None,
           on_progress=None, on_event=None):
    """执行导出。返回结构化结果；失败抛 EngineError。

    on_event 接收 {"type":"phase"|"item", ...}，用于分类进度表与失败清单。
    逐条结果通过**观察钩子**取得：只包装 fe.resolve_item，不改写 core 里任何一行
    解密 / 命名 / 写出代码，保证与基线产物一致。

    kinds 省略时与主界面相同：图片/视频走 resolve_item，文本和链接卡片写入清单。
    显式指定 kinds 时仍只选择 resolve_item 类型，不自动附带文本和链接。
    """
    def say(msg):
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    def emit(kind, **kw):
        if on_event:
            try:
                on_event({"type": kind, **kw})
            except Exception:
                pass

    a, b, query_to = _range(frm, to)
    include_tl = el.kinds_omitted(kinds)
    ks = _kinds(kinds)
    if not dest or not str(dest).strip():
        raise EngineError("请先选择目标文件夹")
    dest = os.path.abspath(str(dest).strip())
    if os.path.exists(dest) and not os.path.isdir(dest):
        raise EngineError(f"目标路径不是文件夹：{dest}")

    emit("phase", name="index", message="正在读取收藏库")
    say("正在读取收藏库…")
    fe = _setup(root, image_key)

    # 基线口径（run_month.py）：区间 0 条就停，绝不产出一个空包
    # 这里查全量（kinds=None）而不只是 media：聊天记录（type=14）要单独展开，
    # 只在「图片+视频」里看会把它漏掉，区间只含聊天记录时会误判成 0 条。
    with contextlib.redirect_stdout(io.StringIO()):
        all_rows = fe.query(a.isoformat(), query_to, None)
        rows_in_range = [r for r in all_rows if r.get("kind") in ks]
        texts, links = ([], [])
        if include_tl:
            texts, links = _query_text_links(fe, a.isoformat(), query_to)
    has_chat = bool(include_tl and any(r.get("type") == 14 for r in all_rows))
    if not all_rows and not texts and not links:
        raise EngineError(f"{a.isoformat()} ~ {b.isoformat()} 区间 0 条，未创建导出批次")
    chat_hint = sum(1 for r in all_rows if r.get("type") == 14) if has_chat else 0
    total_hint = len(rows_in_range) + chat_hint

    base = f"export-{a.isoformat()}_{b.isoformat()}-{dt.datetime.now():%Y%m%d-%H%M%S}"
    out = _unique_batch(dest, base)
    os.makedirs(out, exist_ok=True)
    args = argparse.Namespace(
        frm=a.isoformat(), to=query_to, kind=",".join(sorted(ks)),
        csv="", export=True, selftest=False, image_key="", out=out,
        root="", limit=0,
    )

    # ---- 观察钩子：拿到每条的结果与文件名，core 代码零改写 ----
    items, counter = [], [0]
    seen, tiers = {}, {}

    orig_resolve = fe.resolve_item

    def watched(r, idx, pidx, key, hidx=None):
        if not seen:
            # 聊天记录子项要复用同一批索引，否则会为了 295 个子项再扫一遍 16 万文件
            seen.update(idx=idx, pidx=pidx, hidx=hidx)
        state, data, tier = orig_resolve(r, idx, pidx, key, hidx)
        counter[0] += 1
        i = counter[0]
        tiers[i] = tier or ""
        k = r.get("kind") or "other"
        ok = (state == "完整")
        ext = fe.sniff_tail(data) if data else ""
        suffix = "_缩略" if state == "本地仅缩略图" else ""
        # 与 fav_export.cmd_export 的命名规则一致（未定位本体不写文件）
        name = f"{i:03d}_{r['when'][:10]}_{k}{suffix}.{ext}" if (data and ext) else ""
        lid = r.get("lid", "")
        item = {
            "i": i, "kind": k,
            "group": k if k in ("image", "video") else "other",
            "state": state, "ok": ok, "name": name,
            "when": r.get("when", ""),
            "desc": (r.get("desc") or r.get("fromusr") or "")[:60],
            "fromusr": r.get("fromusr", ""), "size": r.get("size", 0),
            "lid": lid, "local_id": lid,
            "reason": "" if ok else REASONS.get(state, state),
        }
        items.append(item)
        emit("item", item=item, index=i, total=total_hint)
        return state, data, tier

    emit("phase", name="export", message="正在导出文件", total=total_hint)
    say("正在还原并写出文件…")
    buf = io.StringIO()
    fe.resolve_item = watched
    try:
        with contextlib.redirect_stdout(buf):
            stat = fe.cmd_export(args)
    except Exception:
        # out 是本次新建的目录，里面只可能有本工具刚写的文件；失败不留半成品
        shutil.rmtree(out, ignore_errors=True)
        raise
    finally:
        fe.resolve_item = orig_resolve

    emit("phase", name="report", message="正在按发送人归档并生成清单")
    rp = os.path.join(out, "_导出报告.csv")
    rows = sa.read_report(rp) if os.path.isfile(rp) else []
    # fe.cmd_export 会写出临时 _导出报告.csv；读入内存后立即删除，批次只留《导出报告.md》与隐藏状态。
    if os.path.isfile(rp):
        try:
            os.remove(rp)
        except OSError:
            pass
    # 还原方式只有 resolve_item 知道，cmd_export 写的中间报告没有这一列；
    # 按序号回填，供用户与验收脚本区分「容器解出」与「缓存异或还原」。
    for r in rows:
        try:
            r["还原"] = tiers.get(int(r.get("序号") or 0), "")
        except (TypeError, ValueError):
            r["还原"] = ""
    archive_warning = ""
    names = {}
    contacts = {}
    try:
        contacts = sa.load_contacts_safe()
        names = sa.archive_batch(out, rows, contacts=contacts) or {}
    except Exception as e:
        # 归档失败不撤销已写出的批次，也不让整个导出被误删
        archive_warning = f"按发送人归档未完成：{type(e).__name__}"

    # ---- 聊天记录（type=14）：展开 datalist，媒体落文件、正文进清单 ----
    chat_items, chat_stat = [], {}
    if has_chat:
        emit("phase", name="chat", message="正在展开聊天记录")
        say("正在展开聊天记录…")
        # 聊天记录的发送人可能一条媒体都没导出，不在 archive_batch 的 names 里，
        # 不补这一下，整包会被扔进「未知发送人」。
        try:
            senders = [r[3] for r in _chat_rows(fe, a.isoformat(), query_to)]
            names.update(sa.folder_names_for(senders, contacts=contacts))
        except Exception:
            pass
        start_seq = 0
        for r in rows:
            try:
                start_seq = max(start_seq, int(r.get("序号") or 0))
            except (TypeError, ValueError):
                continue
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                chat_rows, chat_items, chat_stat = _export_chat_records(
                    out, fe, a.isoformat(), query_to, names, start_seq,
                    idx=seen.get("idx"), pidx=seen.get("pidx"), hidx=seen.get("hidx"),
                    emit=emit)
            rows.extend(chat_rows)
        except Exception as e:
            archive_warning = (archive_warning + "；" if archive_warning else "") + \
                f"聊天记录展开未完成：{type(e).__name__}"

    # 文本 / 链接等未走 resolve_item 的发送人也补进 names，确保人读报告按真名分组
    if include_tl:
        extra_senders = [
            (t.get("fromusr") or "").strip()
            for t in (texts + links)
            if (t.get("fromusr") or "").strip() and (t.get("fromusr") or "").strip() not in names
        ]
        if extra_senders:
            try:
                names.update(sa.folder_names_for(extra_senders, contacts=contacts))
            except Exception:
                pass

    # 语音归一：必须在写报告与状态前完成，确保人读报告与状态里的「仅元数据」一致。
    for r in rows:
        if (r.get("类型") or "") != "voice":
            continue
        if (r.get("状态") or "") == "未定位本体":
            r["状态"] = "仅元数据"
            r["输出路径"] = ""
        try:  # type=3 的 <duration> 是毫秒，换回秒
            ms = int(r.get("时长") or 0)
        except (TypeError, ValueError):
            ms = 0
        if ms > 600:
            r["时长"] = int(round(ms / 1000.0))

    written = sum(1 for r in rows if (r.get("输出路径") or "").strip())
    counts = {k: int(v) for k, v in (stat or {}).items()} if isinstance(stat, dict) else {}
    for k in ("完整", "本地仅缩略图", "头部缺失", "未定位本体", "缓存还原"):
        if chat_stat.get(k):
            counts[k] = int(counts.get(k, 0)) + int(chat_stat[k])
    counts["缓存还原"] = sum(1 for r in rows if (r.get("还原") or "") == "缓存还原")
    # 语音从「未定位本体」改记「仅元数据」后，要把 std 计数里的那一份扣回来，
    # 否则同一批语音既算未定位又算仅元数据，两个数字都虚高。
    voice_meta = sum(1 for r in rows
                     if (r.get("类型") or "") == "voice"
                     and (r.get("状态") or "") == "仅元数据")
    if voice_meta:
        counts["未定位本体"] = max(0, int(counts.get("未定位本体", 0)) - voice_meta)
    meta_only = sum(1 for r in rows if (r.get("状态") or "") == "仅元数据")
    if meta_only or chat_stat.get("仅元数据"):
        counts["仅元数据"] = meta_only + int(chat_stat.get("仅元数据") or 0)
    items, failed_items, by_kind = _items_from_report(rows, items)

    census = None
    if include_tl:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                census = _census_types(fe, a.isoformat(), query_to)
        except Exception:
            census = None
    plids = _pending_lids(rows)
    mrows, report_md_path = _write_sidecars(
        out, frm=a.isoformat(), to=b.isoformat(), include_tl=include_tl, ks=ks,
        counts=counts, written=written, rows=rows, failed_items=failed_items,
        texts=texts, links=links, census=census, chat_items=chat_items,
        chat=chat_stat, pending_lids=plids, sender_names=names)
    state_path = write_retry_state(
        out, frm=a.isoformat(), to=b.isoformat(), kinds=ks,
        include_text_links=include_tl, pending_lids=plids,
        rows=rows, manifest_rows=mrows)

    emit("phase", name="done", message="完成")
    say("完成")
    warning = "" if fe.KEY else "图片密钥缺失：可能只能导出缩略图或头部缺失件"
    if archive_warning:
        warning = (warning + "；" if warning else "") + archive_warning
    return {
        "ok": True,
        "from": a.isoformat(),
        "to": b.isoformat(),
        "range_inclusive": True,
        "kinds": sorted(ks),
        "dest": dest,
        "batch": os.path.basename(out),
        "batch_path": out,
        "report": report_md_path,
        "total": len(rows),
        "written": written,
        "counts": counts,
        "by_kind": by_kind,
        "failed_count": len(failed_items),
        "failed_items": failed_items,
        "account_root": fe.R,
        "warning": warning,
        "log_tail": "\n".join(buf.getvalue().strip().splitlines()[-8:]),
        "include_text_links": include_tl,
        "text_count": len(texts) if include_tl else 0,
        "link_count": len(links) if include_tl else 0,
        "chat": chat_stat or {},
        "manifest_rows": len(mrows),
        "list": "",
        "report_md": report_md_path,
        "state": state_path,
        "census": census or {},
    }


def retry_missing(batch_path, root=None, image_key=None,
                  on_progress=None, on_event=None):
    """对同一批次里未取到原件的图片/视频再导一次，不新建批次。

    中途失败不会 shutil.rmtree 整个批次。仍未取到则保留原文件，
    不重写一份相同缩略图。升格为完整时写入发送人文件夹并去掉 _缩略。
    """
    def say(msg):
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    def emit(kind, **kw):
        if on_event:
            try:
                on_event({"type": kind, **kw})
            except Exception:
                pass

    if not batch_path or not str(batch_path).strip():
        raise EngineError("请指定要补导的批次目录")
    out = os.path.abspath(str(batch_path).strip())
    if not os.path.isdir(out):
        raise EngineError(f"找不到批次目录：{os.path.basename(out)}")

    st = read_retry_state(out)
    if not st.get("from") or not st.get("to"):
        raise EngineError("找不到补导状态，请先用本程序完成一次导出")

    a, b, query_to = _range(st["from"], st["to"])
    include_tl = bool(st.get("include_text_links"))
    ks = _kinds(st.get("kinds") or None)
    rows = read_batch_rows(out)
    if not rows:
        raise EngineError("批次里没有导出记录，无法补导")

    pending_rows = [
        r for r in rows
        if (r.get("状态") or "") != "完整"
        and (r.get("类型") or "") in ("image", "video")
    ]
    if not pending_rows:
        items, failed_items, by_kind = _items_from_report(rows)
        written = sum(1 for r in rows if (r.get("输出路径") or "").strip())
        counts = {}
        for r in rows:
            s = r.get("状态") or ""
            if s:
                counts[s] = counts.get(s, 0) + 1
        say("没有需要补导的条目")
        emit("phase", name="done", message="没有需要补导的条目")
        rep_md = report_md.report_md_path(out)
        return {
            "ok": True,
            "retried": True,
            "from": a.isoformat(),
            "to": b.isoformat(),
            "range_inclusive": True,
            "kinds": sorted(ks),
            "batch": os.path.basename(out),
            "batch_path": out,
            "report": rep_md,
            "total": len(rows),
            "written": written,
            "counts": counts,
            "by_kind": by_kind,
            "failed_count": len(failed_items),
            "failed_items": failed_items,
            "upgraded": 0,
            "still_missing": 0,
            "warning": "",
            "include_text_links": include_tl,
            "list": "",
            "report_md": rep_md,
            "state": os.path.join(out, RETRY_STATE),
            "census": {},
        }

    emit("phase", name="index", message="正在读取收藏库")
    say("正在读取收藏库…")
    fe = _setup(root, image_key)

    want_lids = set()
    row_by_lid = {}
    for r in pending_rows:
        try:
            lid = int(r.get("local_id") or 0)
        except (TypeError, ValueError):
            lid = 0
        if lid:
            want_lids.add(lid)
            row_by_lid[lid] = r

    with contextlib.redirect_stdout(io.StringIO()):
        queried = fe.query(a.isoformat(), query_to, ks)
    targets = [r for r in queried if r.get("lid") in want_lids]
    by_lid = {r["lid"]: r for r in targets}
    total_hint = len(pending_rows)

    emit("phase", name="export", message="正在补导未取到的原件", total=total_hint)
    say("正在补导未取到的原件…")

    want_sizes = ({r["size"] for r in targets if r.get("size")}
                  | {r["thumb_size"] for r in targets if r.get("thumb_size")})
    # 聊天记录子项不靠 fe.query 取（一个 lid 对应整包，query 只给包级别的字段），
    # 它们的体积/ md5 就在报告行里，这里补进索引想找的体积集合。
    for rec in pending_rows:
        if str(rec.get("所属收藏") or "").strip():
            try:
                sz = int(rec.get("体积") or 0)
            except (TypeError, ValueError):
                sz = 0
            if sz:
                want_sizes.add(sz)
    with contextlib.redirect_stdout(io.StringIO()):
        idx = fe.build_size_index()
        pidx = fe.build_plain_index(want_sizes)
        hidx = fe.build_head_index(fe.KEY)

    contacts = sa.load_contacts_safe()
    names = sa.folder_names_for(
        [(r.get("来源") or r.get("fromusr") or "") for r in rows],
        contacts=contacts,
    )

    upgraded = 0
    still_missing = 0
    chat_lids = set()
    # 中途失败不得 rmtree 批次：此处不再包一层删除
    for i, rec in enumerate(pending_rows, 1):
        try:
            lid = int(rec.get("local_id") or 0)
        except (TypeError, ValueError):
            lid = 0

        # —— 聊天记录里的附件：按报告行自己的体积/md5 认领，不是按包 ——
        # 以前这类行被「所属收藏 非空」直接排除，补导对它们完全是空转，
        # 界面却照样报「补导完成，升格 0 条」，用户只能靠重新导出才能拿到原件。
        if str(rec.get("所属收藏") or "").strip():
            try:
                csize = int(rec.get("体积") or 0)
            except (TypeError, ValueError):
                csize = 0
            probe = {"lid": lid, "type": 14, "kind": rec.get("类型") or "",
                     "when": rec.get("收藏时间") or "", "size": csize,
                     "md5": (rec.get("fullmd5") or "").lower(),
                     "thumb_size": 0, "md5t": "",
                     "desc": rec.get("描述") or "", "fromusr": rec.get("来源") or ""}
            with contextlib.redirect_stdout(io.StringIO()):
                cstate, cdata, _ctier = fe.resolve_item(probe, idx, pidx, fe.KEY, hidx)
            c_ok = cstate == "完整" and cdata is not None
            if c_ok:
                if _upgrade_chat_subitem(fe, rec, cdata):
                    upgraded += 1
                    chat_lids.add(lid)
                else:
                    c_ok = False
                    still_missing += 1
            else:
                sa.leave_unresolved(rec)
                still_missing += 1
            emit("item", item={
                "i": rec.get("序号"), "kind": rec.get("类型") or "",
                "group": rec.get("类型") or "other",
                "state": rec.get("状态") or cstate, "ok": c_ok,
                "name": os.path.basename(rec.get("输出路径") or ""),
                "when": rec.get("收藏时间") or "", "desc": rec.get("描述") or "",
                "fromusr": rec.get("来源") or "",
                "lid": lid, "local_id": lid, "path": rec.get("输出路径") or "",
                "reason": "" if c_ok else REASONS.get(cstate, cstate),
            }, index=i, total=total_hint)
            continue

        src = by_lid.get(lid)
        if not src:
            still_missing += 1
            emit("item", item={
                "i": rec.get("序号"), "kind": rec.get("类型") or "",
                "group": rec.get("类型") if rec.get("类型") in ("image", "video") else "other",
                "state": rec.get("状态") or "", "ok": False,
                "name": os.path.basename(rec.get("输出路径") or ""),
                "when": rec.get("收藏时间") or "",
                "fromusr": rec.get("来源") or "",
                "lid": lid, "local_id": lid,
                "reason": REASONS.get(rec.get("状态") or "", rec.get("状态") or "未取到原件"),
            }, index=i, total=total_hint)
            continue
        with contextlib.redirect_stdout(io.StringIO()):
            state, data, _tier = fe.resolve_item(src, idx, pidx, fe.KEY, hidx)
        ok = state == "完整" and data is not None
        if ok:
            ext = fe.sniff_tail(data) if data else ""
            folder = sa.sender_folder_for_row(out, rec, names)
            new_path = sa.apply_upgrade(out, rec, data, ext, folder_name=folder, names=names)
            if new_path:
                upgraded += 1
            else:
                # 已有不同内容的完整件，不覆盖，保留原文件
                ok = False
                still_missing += 1
        else:
            sa.leave_unresolved(rec)
            still_missing += 1
            ok = False
        item = {
            "i": rec.get("序号"),
            "kind": src.get("kind") or rec.get("类型") or "",
            "group": (src.get("kind") or rec.get("类型") or "other")
            if (src.get("kind") or rec.get("类型")) in ("image", "video") else "other",
            "state": rec.get("状态") or state,
            "ok": (rec.get("状态") == "完整"),
            "name": os.path.basename(rec.get("输出路径") or ""),
            "when": rec.get("收藏时间") or src.get("when") or "",
            "desc": (rec.get("描述") or src.get("desc") or "")[:60],
            "fromusr": rec.get("来源") or src.get("fromusr") or "",
            "lid": lid, "local_id": lid,
            "path": rec.get("输出路径") or "",
            "reason": "" if rec.get("状态") == "完整" else REASONS.get(rec.get("状态") or state, state),
        }
        emit("item", item=item, index=i, total=total_hint)

    if chat_lids:
        # 附件换了，页面里的缩略图/占位符就过期了：按当前状态重渲染这几个包
        say("正在更新聊天记录页面…")
        for lid in sorted(chat_lids):
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    _export_chat_records(out, fe, a.isoformat(), query_to, names, 0,
                                         idx=idx, pidx=pidx, hidx=hidx, emit=None,
                                         only_lids={lid})
            except Exception:
                pass

    emit("phase", name="report", message="正在更新导出清单")
    say("正在更新导出清单…")
    items, failed_items, by_kind = _items_from_report(rows)
    written = sum(1 for r in rows if (r.get("输出路径") or "").strip())
    counts = {}
    for r in rows:
        s = r.get("状态") or ""
        if s:
            counts[s] = counts.get(s, 0) + 1

    texts, links, census, chat_items = [], [], None, []
    if include_tl:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                texts, links = _query_text_links(fe, a.isoformat(), query_to)
                census = _census_types(fe, a.isoformat(), query_to)
                chat_items = _chat_manifest_items(fe, a.isoformat(), query_to,
                                                  batch_dir=out)
            extra = [
                (x.get("fromusr") or "").strip()
                for x in (texts + links + chat_items)
                if (x.get("fromusr") or "").strip() and (x.get("fromusr") or "").strip() not in names
            ]
            if extra:
                names.update(sa.folder_names_for(extra, contacts=contacts))
        except Exception:
            texts, links, census, chat_items = [], [], None, []
    plids = _pending_lids(rows)
    mrows, report_md_path = _write_sidecars(
        out, frm=a.isoformat(), to=b.isoformat(), include_tl=include_tl, ks=ks,
        counts=counts, written=written, rows=rows, failed_items=failed_items,
        texts=texts, links=links, census=census, chat_items=chat_items,
        chat=None, pending_lids=plids, sender_names=names)
    state_path = write_retry_state(
        out, frm=a.isoformat(), to=b.isoformat(), kinds=ks,
        include_text_links=include_tl, pending_lids=plids,
        rows=rows, manifest_rows=mrows)

    emit("phase", name="done", message="补导完成")
    say("补导完成")
    warning = "" if fe.KEY else "图片密钥缺失：可能只能导出缩略图或头部缺失件"
    return {
        "ok": True,
        "retried": True,
        "from": a.isoformat(),
        "to": b.isoformat(),
        "range_inclusive": True,
        "kinds": sorted(ks),
        "batch": os.path.basename(out),
        "batch_path": out,
        "report": report_md_path,
        "total": len(rows),
        "written": written,
        "counts": counts,
        "by_kind": by_kind,
        "failed_count": len(failed_items),
        "failed_items": failed_items,
        "upgraded": upgraded,
        "still_missing": still_missing,
        "warning": warning,
        "include_text_links": include_tl,
        "text_count": len(texts) if include_tl else 0,
        "link_count": len(links) if include_tl else 0,
        "list": "",
        "report_md": report_md_path,
        "state": state_path,
        "census": census or {},
    }


def safe_call(fn, *a, **kw):
    """把任意异常转成结构化结果，供 GUI / MCP / CLI 共用。"""
    try:
        return fn(*a, **kw)
    except EngineError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc(limit=6)}


# ---------------------------------------------------------------- 环境初始化
def _wxkeys():
    """core/ 在 sys.path 后按源码方式加载（冻结态走 _MEIPASS）。"""
    _load()
    import wxkeys
    return wxkeys


def candidate_roots():
    return safe_call(lambda: {"ok": True, "roots": _wxkeys().candidate_roots()})


def probe_env(root=None):
    return safe_call(_wxkeys().probe, root)


def setup_db_keys(db_dir, on_step=None):
    return safe_call(_wxkeys().extract_db_keys, db_dir, on_step=on_step)


def capture_image_key(root, on_tick=None, should_stop=None):
    return safe_call(_wxkeys().capture_image_key, root,
                     on_tick=on_tick, should_stop=should_stop)


def image_sample_count(root=None):
    """本机可用于校验密钥的容器样本数；为 0 说明还没点开过图片。"""
    def _do():
        wx = _wxkeys()
        r = root
        if not r:
            rs = wx.candidate_roots()
            r = rs[0]["path"] if rs else ""
        return {"ok": True, "count": len(wx.image_samples(r)) if r else 0}
    return safe_call(_do)


def set_account_root(root):
    """只写 config.json 的 db_dir，保留已有密钥。"""
    def _do():
        wx = _wxkeys()
        p = os.path.abspath(root)
        db = os.path.join(p, "db_storage")
        if not os.path.isdir(db):
            raise EngineError("该目录不是微信账号目录（缺少 db_storage）")
        cfg = wx.read_json(wx.CONFIG)
        if cfg.get("db_dir") == db:
            return {"ok": True, "db_dir": db}
        cfg["db_dir"] = db
        wx._atomic_write_json(wx.CONFIG, cfg)
        return {"ok": True, "db_dir": db}
    return safe_call(_do)


def set_image_key_manual(key):
    def _do():
        k = (key or "").strip()
        if re.fullmatch(r"[0-9a-fA-F]{32}", k):
            k = bytes.fromhex(k).decode("latin-1")
        if len(k) != 16 or not all(33 <= ord(c) <= 126 for c in k):
            raise EngineError("密钥应为 16 个可见字符（或 32 位十六进制）")
        wx = _wxkeys()
        roots = wx.candidate_roots()
        samples = wx.image_samples(roots[0]["path"]) if roots else []
        ok, hits = wx.verify_image_key(k.encode(), samples) if samples else (True, 0)
        if samples and not ok:
            raise EngineError(f"密钥未通过本机容器校验（命中 {hits}/{len(samples)}）")
        wx.save_image_key(k)
        return {"ok": True, "message": "已写入图片密钥"}
    return safe_call(_do)
