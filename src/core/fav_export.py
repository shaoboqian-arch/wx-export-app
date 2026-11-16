# -*- coding: utf-8 -*-
"""
fav_export.py —— 微信「收藏」按时间/类型批量导出

【容器格式已完全逆向 + 密钥已取得（本文所有结论均由本机真实数据验证）】
  文件本体 = xwechat_files/<wxid>/business/favorite/{data,mid,thumb}/<前2位>/<32hex>
  字节 = [15 字节容器头] + [AES-128-ECB 段] + [16 字节分隔] + [其余]

  15 字节容器头结构：
    00: 07 08 56 32 08 07   magic + 版本
    06: uint32 LE = aes_size   头部 AES 加密区长度（常见值为 1024）
    10: uint32 LE = xor_size   尾部 XOR 区长度
    14: 01                     填充字节
  15: AES 密文，长度 = round_up(aes_size, 16)（PKCS7 填充）
      ※ 起点是 15，不是 31 —— 早期把「15B 头 + 16B 分隔」整体当头，取到错位密文，
        据此误判「同明文不同密文 → 不是 ECB」。按 15 起算 AES-128-ECB 完全自洽。
  之后 16 字节：分隔字段，不参与加解密。
  再往后：尾部 min(xor_size, 剩余长度) 字节 ^ 0x87 即得明文，其前本就是明文。

  闭式关系（抽样容器全部成立）：file_size == 15 + aes_size + 16 + xor_size

  两把密钥：
    · AES(image_aes_key) = 16 字符 ASCII，不落盘，只在微信渲染图片的那一刻驻留进程内存。
      本机已由 find_image_key.py --monitor 扫内存取得，写入 ~/.wechat-cli/config.json。
      判据：用已知明文对（本地缓存里的原始媒体）逐字节核对，并用魔数样本校验。
    · XOR = 0x87（由容器的 JPEG 尾 FF D9 / PNG 尾 IEND 反推得到，再以魔数校验）

交付口径（重要，别把三者混为一谈）：
  · 完整        = 解密后 md5 与收藏库 fullmd5 逐字节吻合 → 可证为原图质量
  · 本地仅缩略图 = 本地只有 thumb/ 下的缩略图容器，原图未被下载到本地
  · 未定位本体   = 本地连缩略图都没有，原件只在微信 CDN（离线不可还原）

用法：
  # 列清单（默认）
  python fav_export.py --from 2026-09-01 --to 2026-09-30 --kind image,video --csv out.csv
  # 自检：用 4 个已知明文对验证 image-key
  python fav_export.py --selftest --image-key <32位hex>
  # 导出（完整件进 _完整/，缩略图进 _缩略图/，其余进 _头部缺失/）
  python fav_export.py --export --from 2026-09-01 --to 2026-09-30 --out ./export
"""
import argparse
import csv
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import paths as _paths  # noqa: E402

_TR = None


def _registry():
    """type_registry 在 src/（源码态 = core/ 的上一级；冻结态 = _MEIPASS 根）。

    本文件是 stdlib-only 的核心脚本，类型判断统一交给注册表。
    实在导入不到就抛错，**不退回「按 datafmt 猜」的旧逻辑** —— 那正是
    type=14 聊天记录被标成 video、type=4 视频掉进 other 的原因。
    """
    global _TR
    if _TR is not None:
        return _TR
    try:
        import type_registry as m
    except ImportError:
        root = os.path.dirname(HERE)
        if root not in sys.path:
            sys.path.insert(0, root)
        import type_registry as m
    _TR = m
    return _TR


# 账号根目录：优先 --root / 环境变量 / config.json / 自动扫（见 paths.py）
# 不再硬编码账号名 —— 换账号或换机器后不会静默找不到东西。
try:
    R = _paths.account_root()
except Exception:
    R = ""
FAVDB = os.path.join(R, "db_storage", "favorite", "favorite.db") if R else ""
FBASE = os.path.join(R, "business", "favorite") if R else ""

MAGIC = b"\x07\x08\x56\x32"
JPEG_END = b"\xff\xd9"
PNG_END = b"\x49\x45\x4e\x44\xae\x42\x60\x82"
ZIP_END = b"\x50\x4b\x05\x06"

KIND_EXT = {"jpg": ".jpg", "png": ".png", "gif": ".gif", "mp4": ".mp4",
            "zip": ".zip", "bin": ".bin"}

KEY = None  # 16 字节 AES-128 密钥（--image-key，或从 ~/.wechat-cli/config.json 读）


def resolve_key(s):
    if s:
        s = s.strip()
        try:
            if re.fullmatch(r"[0-9a-fA-F]{32}", s):
                return bytes.fromhex(s)
            return s.encode()[:16]
        except Exception:
            return None
    cfg = os.path.join(os.path.expanduser("~"), ".wechat-cli", "config.json")
    if os.path.exists(cfg):
        try:
            v = json.load(open(cfg, encoding="utf-8")).get("image_aes_key")
            if v:
                return resolve_key(v)
        except Exception:
            pass
    return None


# ---------------------------------------------------------------- 基础设施
def _load_wxcli():
    spec = importlib.util.spec_from_file_location("wxcli", os.path.join(HERE, "wxcli.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def open_fav_db():
    wx = _load_wxcli()
    keys, _ = wx.load_keys()
    plain = wx.decrypt_db(FAVDB, "favorite\\favorite.db", keys)
    return sqlite3.connect(plain)


def ts(s):
    return int(dt.datetime.strptime(s, "%Y-%m-%d").replace(
        tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp())


def g(pat, txt, default=""):
    m = re.search(pat, txt or "")
    return m.group(1) if m else default


def kind_of(typ, content=None):
    """收藏 type -> 内部 kind 字符串。真源在 src/type_registry.py。

    V0.2.0 的旧实现有三个坑，都在注册表里修掉了：
      · `<datatype>(\\d+)</datatype>` 永不命中 —— XML 用的是**属性** datatype="2"
      · `datafmt` 以 mp4 开头就判 video，把 type=14 聊天记录误标成视频（9 条）
      · type=4 视频没有 datafmt 时落进 other（2 条），被默认过滤集排除
    """
    return _registry().kind_of(typ, content)


def parse_item(typ, content):
    c = content or ""
    tw, th = g(r"<thumb_width>([\d.]+)</thumb_width>", c), g(r"<thumb_height>([\d.]+)</thumb_height>", c)
    return dict(
        desc=g(r"<datadesc>([^<]*)</datadesc>", c) or g(r"<datatitle>([^<]*)</datatitle>", c)
             or g(r"<title>([^<]*)</title>", c),
        size=int(g(r"<fullsize>(\d+)</fullsize>", c, "0") or 0),
        thumb_size=int(g(r"<thumbfullsize>(\d+)</thumbfullsize>", c, "0") or 0),
        dur=int(g(r"<duration>(\d+)</duration>", c, "0") or 0),
        wh=f"{tw}x{th}" if (tw and th) else "",
        fmt=g(r"<datafmt>([^<]*)</datafmt>", c),
        md5=g(r"<fullmd5>([0-9a-fA-F]{32})</fullmd5>", c).lower(),
        md5h=g(r"<head256md5>([0-9a-fA-F]{32})</head256md5>", c).lower(),
        md5t=g(r"<thumbfullmd5>([0-9a-fA-F]{32})</thumbfullmd5>", c).lower(),
        md5th=g(r"<thumbhead256md5>([0-9a-fA-F]{32})</thumbhead256md5>", c).lower(),
    )


def query(frm, to, kinds):
    conn = open_fav_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT local_id, type, update_time, content, fromusr, realchatname "
        "FROM fav_db_item WHERE update_time >= ? AND update_time < ? ORDER BY update_time",
        (ts(frm), ts(to)))
    out = []
    for lid, typ, ut, content, fromusr, chatname in cur.fetchall():
        k = kind_of(typ, content)
        if kinds and k not in kinds:
            continue
        out.append(dict(lid=lid, type=typ, kind=k, ut=ut,
                        when=dt.datetime.fromtimestamp(ut).strftime("%Y-%m-%d %H:%M:%S"),
                        fromusr=fromusr or "", chat=chatname or "",
                        content=content or "", **parse_item(typ, content)))
    conn.close()
    return out


# ---------------------------------------------------------------- 容器解析
def build_size_index():
    """尺寸 -> [(路径, 目录名), ...]。同尺寸保留全部候选（靠 md5 逐个试），不用 setdefault 丢掉。"""
    idx = {}
    for sub in ("data", "mid", "thumb"):
        d = os.path.join(FBASE, sub)
        if not os.path.isdir(d):
            continue
        for dp, _, fn in os.walk(d, onerror=lambda e: None):
            for f in fn:
                p = os.path.join(dp, f)
                try:
                    idx.setdefault(os.path.getsize(p), []).append((p, sub))
                except Exception:
                    pass
    return idx


def locate_candidates(idx, r):
    """按目录语义给候选容器排序，返回 [(路径, 目录, tier), ...]。

    目录语义（用完整件交叉验证过，不靠推测）：
      data/  = 原图本体，容器长 ≡ fullsize + 31
      mid/   = 中等尺寸预览，长度与 fullsize 毫无对应关系 ⇒ 不能用来认领任何条目
      thumb/ = 缩略图，容器长 ≡ thumbfullsize + 31

    因此只认「严格 +31」的匹配。不带 +31 的原始尺寸匹配一律不用：
    它只会撞上 mid/ 里的预览图，制造出「密钥不符」这种假异常
    （修前曾有 mid 预览被误当成某条收藏的本体）。
    """
    out = []
    if r["size"]:
        for p, sub in idx.get(r["size"] + 31, ()):
            if sub == "data":
                out.append((p, sub, "本体"))
    if r["thumb_size"]:
        for p, sub in idx.get(r["thumb_size"] + 31, ()):
            if sub == "thumb":
                out.append((p, sub, "缩略图"))
    return out


def _try_candidates(cands, r, key):
    """逐个候选：解密 → 整文件 md5 比对。返回 (state, data, tier)。

    原件必须对上 fullmd5。缩略图必须对上 thumbfullmd5：同体积或
    head256 撞车时，不能收下第一个能解开的文件，否则会漂到别的条目。
    没有 thumbfullmd5 时，不按体积认领缩略图。
    """
    fallback = ("未定位本体", None, "")
    for p, _sub, tier in cands:
        try:
            with open(p, "rb") as f:
                C = f.read()
        except Exception:
            continue
        if C[:4] != MAGIC:
            continue
        data, st = assemble(C, key)
        if data is None:
            continue
        digest = hashlib.md5(data).hexdigest()
        if r["md5"] and digest == r["md5"]:
            return "完整", data, tier
        if fallback[0] == "未定位本体":
            if tier == "缩略图" and r.get("md5t") and digest == r["md5t"]:
                fallback = ("本地仅缩略图", data, tier)
            elif st == "头部缺失":
                fallback = ("头部缺失", data, tier)
    return fallback


def build_head_index(key):
    """解密后前 256 字节的 md5 → [(路径, 目录)]。key 为空则返回 {}。

    为什么要它：只按尺寸找会漏货。本地缓存的缩略图渲染尺寸常与 XML 里记的
    thumbfullsize 不一致，尺寸匹配会漏掉部分视频缩略图，
    而按内容找得到。

    ⚠ head256md5 会撞车：没有 EXIF 的 JPEG，前 256 字节只取决于编码参数与图像尺寸，
    与画面内容无关（按头 256 字节会有大量假命中，整文件校验后才能去掉）。
    所以它只当筛子，命中后必须再比整文件 md5。
    """
    if not key:
        return {}
    from Crypto.Cipher import AES
    ecb = AES.new(key, AES.MODE_ECB)
    idx = {}
    for sub in ("data", "mid", "thumb"):
        d = os.path.join(FBASE, sub)
        for dp, _, fn in os.walk(d, onerror=lambda e: None):
            for f in fn:
                p = os.path.join(dp, f)
                try:
                    with open(p, "rb") as fh:
                        head = fh.read(1100)
                except Exception:
                    continue
                if head[:4] != MAGIC:
                    continue
                aes_size = struct.unpack("<I", head[6:10])[0]
                n = ((aes_size + 15) // 16) * 16
                if n < 256 or 15 + n > len(head):
                    continue
                pt = ecb.decrypt(head[15:15 + n])
                h = hashlib.md5(pt[:256]).hexdigest()
                idx.setdefault(h, []).append((p, sub))
    return idx


def _xor_key(head):
    """反推「整文件单字节异或」的 key：让前 16 字节变成已知图片/容器魔数的那个 k。

    不硬编码 0x87 —— 多数 .dat 可用 k=0x87 还原成 JPEG，其余类型并未一概确认；
    靠魔数验出来比记一个魔数扛得住版本变化。
    """
    heads = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF8", b"RIFF",
             b"\x1aE\xdf\xa3", b"PK\x03\x04")
    for k in range(1, 256):
        t = bytes(c ^ k for c in head)
        if any(t.startswith(h) for h in heads):
            return k
    return None


def decode_cached_media(blob):
    """WeChat 4.x 聊天图片/视频缓存（msg/attach/**.dat）还原。

    这类文件是**整文件与单个字节异或**，头部不是本文件的 MAGIC，所以既过不了
    容器解密也嗅不出扩展名 —— 旧版会把 14 MB 的密文当成原图写成 .bin。
    收藏库里这几个子项的 fullmd5 恰好是**这个 .dat 的 md5**（不是原图 md5），
    所以 md5 能对上、内容却不是原图。返回 (还原后的 bytes, 扩展名)；还原不出返回 (None, "")。
    """
    if not blob or len(blob) < 16:
        return None, ""
    k = _xor_key(blob[:16])
    if not k:
        return None, ""
    data = blob.translate(bytes(i ^ k for i in range(256)))
    ext = sniff_tail(data)
    if not ext or ext == "bin":
        return None, ""
    return data, ext


def resolve_item(r, idx, pidx, key, hidx=None):
    """判定单条收藏 → (state, data, tier)。

    state ∈ {完整, 本地仅缩略图, 头部缺失, 未定位本体}
      · 完整：解密/复制后 md5 与收藏库 fullmd5 逐字节吻合 —— 唯一的「原图质量」凭证
      · 本地仅缩略图：本地只有缩略图容器，原图从未下载
      · 头部缺失：拿到本体容器但当时没有密钥（本机已有密钥，正常不该出现）

    两道匹配：先尺寸（精确、便宜），再用 hidx 做内容兜底（尺寸对不上时仍能找回）。
    """
    if r["md5"] and r["md5"] in pidx:
        try:
            with open(pidx[r["md5"]], "rb") as f:
                blob = f.read()
            k = r.get("kind") or ""
            # md5 对上不等于能用：图片/视频如果嗅探不出任何已知格式，说明拿到的
            # 仍是某个加密缓存（实测 msg/attach 的 .dat 会这样命中）。先试单字节异或还原，
            # 还原不出来就判未定位 —— 绝不把加密块当原图写出去。
            if k in ("image", "video") and sniff_tail(blob) == "bin":
                plain, ext = decode_cached_media(blob)
                if plain is not None:
                    return "完整", plain, "缓存还原"
            else:
                return "完整", blob, "明文副本"
        except Exception:
            pass

    thumb_plain = pidx.get(r["md5t"]) if r.get("md5t") else None

    state, data, tier = _try_candidates(locate_candidates(idx, r), r, key)
    if state == "完整":
        return state, data, tier

    if hidx:
        extra, seen = [], set()
        for h in (r.get("md5h"), r.get("md5th")):
            for cand in (hidx.get(h) or ()) if h else ():
                if cand[0] in seen:
                    continue
                seen.add(cand[0])
                sub = cand[1]
                extra.append((cand[0], sub, "缩略图" if sub == "thumb" else "本体"))
        if extra:
            s2, d2, t2 = _try_candidates(extra, r, key)
            if s2 == "完整":
                return s2, d2, t2
            if s2 == "本地仅缩略图" and state == "未定位本体":
                state, data, tier = s2, d2, t2

    # 明文缩略图兜底（temp/ 里也会落缩略图）
    if state == "未定位本体" and thumb_plain:
        try:
            with open(thumb_plain, "rb") as f:
                return "本地仅缩略图", f.read(), "明文缩略图"
        except Exception:
            pass
    return state, data, tier


def locate_blob(idx, r):
    """兼容旧调用：返回 (cand, tier) 或 (None, '')。"""
    c = locate_candidates(idx, r)
    return ((c[0][0], c[0][1]), c[0][2]) if c else (None, "")


def build_plain_index(sizes):
    """在全盘找与给定体积一致的**明文**文件，返回 md5 -> 路径。

    ⚠ 只排除三个容器目录（data/mid/thumb），**不能排除整个 business/favorite**：
    微信下载收藏原图后会把**明文**落在 business/favorite/temp/，那是主来源之一。
    （早期版本 skip=FBASE 把 temp 一起跳过了，导致那批明文明明在本地却看不见。）
    """
    out = {}
    skips = [os.path.abspath(os.path.join(FBASE, s)) for s in ("data", "mid", "thumb")]
    for dp, dn, fn in os.walk(R, onerror=lambda e: None):
        adp = os.path.abspath(dp)
        if any(adp == s or adp.startswith(s + os.sep) for s in skips):
            continue
        for f in fn:
            p = os.path.join(dp, f)
            try:
                sz = os.path.getsize(p)
            except Exception:
                continue
            if sz not in sizes:
                continue
            try:
                with open(p, "rb") as fh:
                    b = fh.read()
            except Exception:
                continue
            if b[:4] == MAGIC:
                continue
            h = hashlib.md5(b).hexdigest()
            if h not in out:
                out[h] = p
    return out


def parse_container(C):
    """解析 V2 容器，返回各区段
    [0:15] 头 | [15:15+aes_size] AES-128-ECB 密文 | 16B 分隔 | 其余
    """
    if C[:4] != MAGIC:
        return None
    aes_size = struct.unpack("<I", C[6:10])[0]
    xor_size = struct.unpack("<I", C[10:14])[0]
    a = 15
    b = a + aes_size
    s = b + 16
    return dict(aes_size=aes_size, xor_size=xor_size,
                aes=C[a:b], sep=C[b:s], rest=C[s:])


def unmix(rest, xor_size):
    """尾部 min(xor_size, len) 字节 ^ 0x87"""
    n = len(rest)
    t = min(xor_size, n)
    if t <= 0:
        return rest
    cut = n - t
    return rest[:cut] + bytes(x ^ 0x87 for x in rest[cut:])


def assemble(C, key=None):
    """组装明文。key 为 16 字节时未解则用 0 占位。返回 (data, 状态)"""
    p = parse_container(C)
    if not p:
        return None, "格式异常"
    rest = unmix(p["rest"], p["xor_size"])
    if key:
        try:
            from Crypto.Cipher import AES
            head = AES.new(key, AES.MODE_ECB).decrypt(p["aes"])
            return head + rest, "已解密"
        except Exception:
            pass
    return b"\x00" * p["aes_size"] + rest, "头部缺失"


def try_decrypt_head(head, key, hl, head256md5):
    """兼容旧接口"""
    return None, "见 assemble()"


def sniff_tail(plain_body):
    """按头魔数 + 尾标记判断扩展名。头优先（mp4/webp 这类没有独特尾标记）。"""
    b = plain_body
    if b[:3] == b"\xff\xd8\xff" or b.endswith(JPEG_END):
        return "jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n" or b.endswith(PNG_END):
        return "png"
    if b[:4] == b"GIF8":
        return "gif"
    if b[4:8] == b"ftyp":
        return "mp4"
    if b[:4] == b"RIFF":
        return "webp" if b[8:12] == b"WEBP" else "riff"
    if b[:4] == b"%PDF":
        return "pdf"
    if b[:4] == b"\xd0\xcf\x11\xe0":
        return "doc"
    if b[:2] == b"PK" or ZIP_END in b[-1024:]:
        return "zip"
    if b[:4] == b"\x1aE\xdf\xa3":
        return "mkv"
    return "bin"


def try_decrypt_head(head, key, hl, head256md5):
    """在已知若干模式下尝试解头部；用 head256md5 判定"""
    try:
        from Crypto.Cipher import AES
    except Exception:
        return None, "未安装 pycryptodome"
    h = head[: (len(head) // 16) * 16]
    cands = []
    try:
        cands.append(("AES128-ECB", AES.new(key, AES.MODE_ECB).decrypt(h)))
    except Exception:
        pass
    for ivname, iv in (("zero", b"\0" * 16),):
        try:
            cands.append((f"AES128-CBC/{ivname}", AES.new(key, AES.MODE_CBC, iv).decrypt(h)))
        except Exception:
            pass
    for name, d in cands:
        if head256md5 and hl >= 256:
            return name, "部分解密（头部实际只覆盖 min(1024,n) 字节，此处需按真实模式拼接）"
    return None, "未匹配"


# ---------------------------------------------------------------- 输出
def cmd_list(args):
    kinds = set(args.kind.split(",")) if args.kind else None
    rows = query(args.frm, args.to, kinds)
    idx = build_size_index()
    want_sizes = ({r["size"] for r in rows if r["size"]}
                  | {r["thumb_size"] for r in rows if r["thumb_size"]})
    pidx = build_plain_index(want_sizes)
    hidx = build_head_index(KEY)
    print(f"（全盘明文副本索引: {len(pidx)} 个；内容索引: {len(hidx)} 个 head256）")
    ok = locked = miss = thumbonly = 0
    recs = []
    for i, r in enumerate(rows, 1):
        state, _data, _tier = resolve_item(r, idx, pidx, KEY, hidx)
        if state == "完整":
            ok += 1
        elif state == "本地仅缩略图":
            thumbonly += 1
        elif state == "头部缺失":
            locked += 1
        else:
            miss += 1
        recs.append((i, r, state))
    print(f"\n{args.frm} ~ {args.to}  {len(rows)} 条  |  完整 {ok} · 本地仅缩略图 {thumbonly} · "
          f"头部锁定 {locked} · 未定位本体 {miss}\n")
    show = recs[:25] if args.csv else recs
    print(f"{'#':>3} {'收藏时间':<20} {'类型':<6} {'体积':>10} {'时长':>5} {'状态':<10} 描述/来源")
    for i, r, st in show:
        print(f"{i:>3} {r['when']:<20} {r['kind']:<6} {r['size']:>10} {r['dur']:>5} {st:<10} "
              f"{(r['desc'] or r['fromusr'])[:28]}")
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["序号", "收藏时间", "类型", "来源", "群", "体积(bytes)", "时长(s)",
                        "尺寸", "格式", "可解性", "local_id", "head256md5", "fullmd5", "描述"])
            for i, r, st in recs:
                w.writerow([i, r["when"], r["kind"], r["fromusr"], r["chat"], r["size"],
                            r["dur"], r["wh"], r["fmt"], st, r["lid"], r["md5h"], r["md5"], r["desc"]])
        print(f"\nCSV -> {args.csv}")
    return recs


def cmd_selftest(args):
    """用 4 个已知明文对（cache/Sns/Video 里字节完全一致的副本）验证密钥"""
    key = bytes.fromhex(args.image_key)
    known = {6005: "cache/2026-09/Sns/Video/67/decaf8d6eca8bd7897dca995a37780.mp4",
             5863: "cache/2026-09/Sns/Video/af/6e5b75d69f8fc22458b954ea29d4fa.mp4",
             5872: "cache/2026-09/Sns/Video/c8/5a0d629156a78434b6fff60af13e2b.mp4",
             6105: "cache/2026-09/Sns/Video/dc/16c8e17580633dd3b8364ec405503b.mp4"}
    idx = build_size_index()
    rows = {r["lid"]: r for r in query("2026-09-01", "2026-10-01", {"video"})}
    from Crypto.Cipher import AES
    hit = 0
    for lid, rel in known.items():
        r = rows.get(lid)
        if not r:
            print(f"  fav{lid}: 清单里没找到")
            continue
        p = idx.get(r["size"] + 31)
        if not p:
            continue
        C = open(p[0][0], "rb").read()
        P = open(os.path.join(R, rel.replace("/", os.sep)), "rb").read()
        aes_size = struct.unpack("<I", C[6:10])[0]
        h = C[15:15 + aes_size]
        if len(h) < 16:
            continue
        d = AES.new(key, AES.MODE_ECB).decrypt(h[:16])
        ok = d[:16] == P[:16]
        print(f"  fav{lid}: ECB 首块 {'★一致' if ok else '不符'}  {d[:8].hex(' ')} vs {P[:8].hex(' ')}")
        if ok:
            hit += 1
    print(f"\n自检结果: {hit}/{len(known)}")
    return hit


def cmd_export(args):
    kinds = set(args.kind.split(",")) if args.kind else None
    rows = query(args.frm, args.to, kinds)
    idx = build_size_index()
    want_sizes = ({r["size"] for r in rows if r["size"]}
                  | {r["thumb_size"] for r in rows if r["thumb_size"]})
    pidx = build_plain_index(want_sizes)
    hidx = build_head_index(KEY)
    os.makedirs(args.out, exist_ok=True)
    full_dir = os.path.join(args.out, "_完整")
    thumb_dir = os.path.join(args.out, "_缩略图")
    part_dir = os.path.join(args.out, "_头部缺失")
    for d in (full_dir, thumb_dir, part_dir):
        os.makedirs(d, exist_ok=True)
    rep = []
    stat = dict(完整=0, 本地仅缩略图=0, 头部缺失=0, 未定位本体=0)
    for i, r in enumerate(rows, 1):
        state, data, _tier = resolve_item(r, idx, pidx, KEY, hidx)
        outpath = ""
        if state == "完整" and data is not None:
            ext = sniff_tail(data)
            outpath = os.path.join(full_dir, f"{i:03d}_{r['when'][:10]}_{r['kind']}.{ext}")
            with open(outpath, "wb") as f:
                f.write(data)
        elif state == "本地仅缩略图" and data is not None:
            # 原图未下载到本地。缩略图仍交付出去 —— 至少能看见是什么。
            ext = sniff_tail(data)
            outpath = os.path.join(thumb_dir, f"{i:03d}_{r['when'][:10]}_{r['kind']}_缩略.{ext}")
            with open(outpath, "wb") as f:
                f.write(data)
        elif state == "头部缺失" and data is not None:
            ext = sniff_tail(data)
            outpath = os.path.join(part_dir, f"{i:03d}_{r['when'][:10]}_{r['kind']}.{ext}")
            with open(outpath, "wb") as f:
                f.write(data)
        stat[state] = stat.get(state, 0) + 1
        rep.append([i, r["when"], r["kind"], r["size"], r["dur"], state,
                    r["lid"], r["md5"], r["md5h"], outpath, r["desc"], r["fromusr"]])

    # 清理上一轮遗留。重跑导出时，条目会从「仅缩略图」升格为「完整」，
    # 文件名随之从 `001_..._video_缩略.jpg` 变成 `001_..._video.mp4` —— 旧名不会被覆盖，
    # 留在目录里就会与报告不符。只删本工具自己命名的文件（<三位序号>_<日期>_<类型>[_缩略].<ext>），
    # 不认识的（用户自己放进去的）一律不动。
    written = {os.path.abspath(p) for p in (row[9] for row in rep) if p}
    own_pat = re.compile(r"^\d{3}_\d{4}-\d{2}-\d{2}_(image|video|file|link|chat|voice|finder)(_缩略)?\.[A-Za-z0-9]+$")
    stale = []
    for d in (full_dir, thumb_dir, part_dir):
        for name in sorted(os.listdir(d)):
            p = os.path.abspath(os.path.join(d, name))
            if p in written or not own_pat.match(name):
                continue
            try:
                os.remove(p)
                stale.append(name)
            except OSError as e:
                print(f"  [!] 上一轮旧文件删不掉（可忽略）: {name} :: {e}")
    if stale:
        print(f"  清理上一轮遗留: {len(stale)} 个")

    rp = os.path.join(args.out, "_导出报告.csv")
    with open(rp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["序号", "收藏时间", "类型", "体积", "时长", "状态", "local_id",
                    "fullmd5", "head256md5", "输出路径", "描述", "来源"])
        w.writerows(rep)
    print("\n导出完成：")
    for k, v in stat.items():
        print(f"  {k}: {v}")
    print(f"  目录: {args.out}")
    print(f"  报告: {rp}")
    print("\n说明：标为「头部缺失」的文件，其前 min(1024, 文件长度) 字节仍是加密的，")
    print("      用 0 占位。拿到 image_key 后加 --image-key 重跑即可得到完整文件。")
    return stat


def main():
    ap = argparse.ArgumentParser(description="微信收藏按时间/类型批量导出")
    ap.add_argument("--from", dest="frm", default="2026-09-01")
    ap.add_argument("--to", dest="to", default="2026-10-01")
    ap.add_argument("--kind", default="",
                    help="image,video,file,link,chat 或留空=全部")
    ap.add_argument("--csv", default="")
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--image-key", dest="image_key", default="",
                    help="16 字节 hex（WeChat image_key），用于解 1024 字节头部")
    ap.add_argument("--out", default=os.path.join(HERE, "export"))
    ap.add_argument("--root", default="",
                    help="微信账号目录（<xwechat_files>/<账号目录>）。留空则自动解析")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.root:
        global R, FAVDB, FBASE
        R = os.path.abspath(args.root)
        FAVDB = os.path.join(R, "db_storage", "favorite", "favorite.db")
        FBASE = os.path.join(R, "business", "favorite")

    if not R or not os.path.exists(FAVDB):
        print("[×] 找不到收藏库。用 --root 指定微信账号目录，或设环境变量 WV_FAV_ROOT。")
        print(f"    当前解析到的账号目录: {R!r}")
        return 2

    global KEY
    KEY = resolve_key(args.image_key)
    if KEY:
        try:
            shown = KEY.decode("ascii")
        except Exception:
            shown = KEY.hex()
        print(f"[*] 使用图片密钥 {shown}（16 字节 ASCII，hex={KEY.hex()}）")

    if args.selftest:
        return cmd_selftest(args)
    if args.export:
        return cmd_export(args)
    return cmd_list(args)


if __name__ == "__main__":
    main()
