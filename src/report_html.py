# -*- coding: utf-8 -*-
"""把一条「多消息打包收藏」（type=14）渲染成一个可离线打开的单页 HTML。

设计约束（来自需求，不要擅自改）：
  · 一包一目录，目录里放 index.html 与附件本体，**不用总索引**，入口就是《导出报告.md》；
  · 全部走**相对路径**，整个目录拷给别人也能看，不做 base64 内联；
  · 语音/视频/位置**尽量补齐**：能拿到本体就嵌播放器，拿不到就画等价占位
    （语音给时长条、视频给封面帧、位置给地址 + 地图链接），不留空白；
  · 正文用气泡流，自己发的靠右、别人靠左；
  · 顶部把四态（完整 / 缩略 / 未缓存 / 仅元数据）写清楚。
"""
import datetime as dt
import html
import os
import urllib.parse

from common import VERSION

STATE_FULL = "完整"
STATE_THUMB = "本地仅缩略图"
STATE_MISS = "未定位本体"
STATE_META = "仅元数据"

JS = """<script>
(function(){
  var all=[].slice.call(document.querySelectorAll('audio'));
  [].slice.call(document.querySelectorAll('.voice[data-audio]')).forEach(function(b){
    var a=b.nextElementSibling;
    if(!a||a.tagName!=='AUDIO')return;
    b.addEventListener('click',function(){
      if(a.paused){all.forEach(function(x){if(x!==a){x.pause();}});a.play();}
      else{a.pause();}
    });
    a.addEventListener('play',function(){b.classList.add('playing');});
    ['pause','ended'].forEach(function(ev){
      a.addEventListener(ev,function(){b.classList.remove('playing');});
    });
  });
  [].slice.call(document.querySelectorAll('.voice.na')).forEach(function(b){
    b.addEventListener('click',function(){b.classList.toggle('show');});
  });
})();
</script>"""


BODY_KIND_ZH = {
    "text": "文本", "image": "图片", "video": "视频", "voice": "语音",
    "file": "文件", "link": "链接", "location": "位置", "other": "其他",
}

CSS = """
:root{--bg:#f6f7f9;--card:#ffffff;--line:#d8dde6;--ink:#182026;--muted:#525d6b;
      --me:#e6f4f2;--me-line:#0d6e66;--other:#ffffff;--accent:#0d6e66;--on-mint:#ffffff;
      --mint-hi:#095952;--sand:#eef0f4;--warn:#9a4d00;--err:#b42318;--err-bg:#fef3f2;
      --link:#0d6e66;--link-on-me:#095952;--muted-on-me:#525d6b}
*{box-sizing:border-box}
html{color-scheme:light}
body{margin:0;background:var(--bg);color:var(--ink);
     font:15px/1.62 "Microsoft YaHei UI","Microsoft YaHei","PingFang SC","Hiragino Sans GB","Noto Sans CJK SC",sans-serif}
::selection{background:var(--sand);color:var(--ink)}
*{scrollbar-width:thin;scrollbar-color:var(--line) var(--bg)}
*::-webkit-scrollbar{width:10px;height:10px}
*::-webkit-scrollbar-thumb{background:var(--line);border-radius:8px;border:2px solid var(--bg)}
*::-webkit-scrollbar-track{background:var(--bg)}
.wrap{max-width:860px;margin:0 auto;padding:28px 18px 64px}
.skip{position:absolute;left:-9999px;top:0;background:var(--card);color:var(--ink);
      padding:8px 12px;border:1px solid var(--line);border-radius:8px;z-index:9}
.skip:focus{left:8px;top:8px}
h1{font-size:20px;margin:0 0 6px;font-weight:700;overflow-wrap:anywhere;word-break:normal}
.meta{color:var(--muted);font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 22px}
.chip{background:var(--card);border:1px solid var(--line);border-radius:999px;
      padding:4px 11px;font-size:12.5px;color:var(--muted)}
.chip b{color:var(--ink);font-weight:600}
.chip.full{background:var(--me);border-color:var(--me-line)}
.chip.full b{color:var(--ink)}
.chip.thumb{background:#fff8ed;border-color:#f0d9b5}
.chip.thumb b{color:var(--warn)}
.chip.miss{background:var(--err-bg);border-color:#e8c4c0}
.chip.miss b{color:var(--err)}
.row{display:flex;margin:0 0 12px;content-visibility:auto;contain-intrinsic-size:auto 72px}
.row.me{justify-content:flex-end}
.stack{max-width:78%;min-width:0}
.who{font-size:12px;color:var(--muted);margin:0 0 3px 2px}
.row.me .who{text-align:right;margin:0 2px 3px 0}
.bubble{background:var(--other);border:1px solid var(--line);border-radius:12px;
        padding:9px 12px;word-break:break-word;white-space:pre-wrap}
.row.me .bubble{background:var(--me);border-color:var(--me-line)}
.bubble img,.bubble video{display:block;max-width:100%;max-height:70vh;width:auto;height:auto;
        object-fit:contain;border-radius:8px;margin:2px 0}
.bubble audio{display:block;width:280px;max-width:100%;margin:2px 0}
.badge{display:inline-block;margin-top:6px;font-size:11.5px;padding:1px 7px;
       border-radius:6px;border:1px solid var(--line);color:var(--muted);background:var(--card);white-space:normal}
.badge.warn{color:var(--warn);border-color:#f0d9b5;background:#fff8ed}
.badge.err{color:var(--err);border-color:#e8c4c0;background:var(--err-bg)}
/* 语音条：胶囊 + 声波 + 时长；己方靠右、图标在右。色跟随海岛主/次按钮。 */
.voice{display:inline-flex;align-items:center;gap:8px;min-width:72px;max-width:100%;
       height:34px;padding:0 12px;border:0;border-radius:17px;background:var(--sand);
       color:var(--ink);font:inherit;font-size:13px;cursor:pointer;white-space:nowrap;
       transition:background-color 160ms ease-out,transform 140ms ease-out}
.row.me .voice{flex-direction:row-reverse;background:var(--accent);color:var(--on-mint)}
@media (hover:hover) and (pointer:fine){
  .voice:hover{background:#f7f1e6}
  .row.me .voice:hover{background:var(--mint-hi)}
  .voice.na:hover,.row.me .voice.na:hover{background:var(--sand)}
}
.voice:active{transform:scale(.97)}
.voice.na:active{transform:none}
.voice:focus-visible{outline:2px solid var(--ink);outline-offset:2px}
.vicon{display:inline-flex;align-items:flex-end;gap:2.5px;height:13px;flex:0 0 auto}
.vicon i{display:block;width:2.5px;border-radius:2px;background:currentColor;opacity:.85}
.vicon i:nth-child(1){height:5px}
.vicon i:nth-child(2){height:9px}
.vicon i:nth-child(3){height:13px}
.voice.playing .vicon i{animation:vbeat .7s ease-in-out infinite}
.voice.playing .vicon i:nth-child(2){animation-delay:.15s}
.voice.playing .vicon i:nth-child(3){animation-delay:.3s}
@keyframes vbeat{0%,100%{transform:scaleY(.5)}50%{transform:scaleY(1)}}
.voice.na,.row.me .voice.na{background:var(--sand);color:var(--muted);cursor:help}
.vnote{display:none;margin-top:6px;font-size:12px;color:var(--muted)}
.row.me .vnote{color:var(--muted-on-me)}
.voice.na.show + .vnote{display:block}
.loc{font-size:14px} .loc .addr{font-weight:600}
.loc .poi{color:var(--muted)} .loc a{color:var(--link);text-decoration:underline;text-underline-offset:2px}
.loc a:hover{text-decoration-thickness:2px}
.row.me .bubble a{color:var(--link-on-me)}
.row.me .bubble .poi,.row.me .bubble .miss{color:var(--muted-on-me)}
a:focus-visible,audio:focus-visible,video:focus-visible,.skip:focus-visible{outline:2px solid var(--ink);outline-offset:2px}
.miss{border:1px dashed var(--line);border-radius:8px;padding:9px 12px;color:var(--muted);font-size:13.5px}
.filelink{color:var(--link);text-decoration:underline;text-underline-offset:2px}
.filelink:hover{text-decoration-thickness:2px}
footer{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);
       color:var(--muted);font-size:12px}
.empty{color:var(--muted);padding:20px 0}
@media (prefers-reduced-motion:reduce){
  .voice{transition:none}
  .voice:active{transform:none}
  .voice.playing .vicon i{animation:none}
}
"""


def esc(s):
    return html.escape(str(s or ""), quote=True)


def rel(path, base_dir):
    """html 里可用的相对地址（正斜杠 + 百分号转义）。"""
    if not path:
        return ""
    try:
        r = os.path.relpath(os.path.abspath(path), os.path.abspath(base_dir))
    except (ValueError, OSError):
        return ""
    r = r.replace(os.sep, "/")
    return urllib.parse.quote(r, safe="/")


def _size_zh(n):
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    for unit, div in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} B"


def _bubble_open(me):
    return '<div class="row me">' if me else '<div class="row">'


def _media_body(e, base_dir):
    """图片/视频/文件：有本体就嵌，没本体给出等价占位。"""
    kind = e.get("kind") or ""
    state = e.get("state") or ""
    path = e.get("path") or ""
    thumb = e.get("thumb") or ""
    has = bool(path) and os.path.isfile(path)

    if kind == "image":
        if has:
            src = rel(path, base_dir)
            badge = ('<span class="badge warn">本地仅缩略图</span>'
                     if state == STATE_THUMB else "")
            alt = f'{e.get("sender") or ""} {e.get("when") or ""} 的图片'.strip()
            return (f'<a href="{src}" target="_blank" rel="noopener">'
                    f'<img loading="lazy" src="{src}" alt="{esc(alt)}"></a>{badge}')
        return ('<div class="miss">图片：本机未缓存（微信从没把这件原图下载到这台电脑）</div>')

    if kind == "video":
        dur = e.get("duration") or 0
        dur_txt = f" · {int(dur)} 秒" if dur else ""
        # 「本地仅缩略图」时 path 指向的是封面 jpg，不能塞进 <video src>
        playable = has and state != STATE_THUMB
        if playable:
            src = rel(path, base_dir)
            poster = f' poster="{rel(thumb, base_dir)}"' if thumb and os.path.isfile(thumb) else ""
            return (f'<video controls preload="metadata"{poster} src="{src}"></video>'
                    f'<span class="badge">视频{dur_txt}</span>')
        v_alt = f'{e.get("sender") or ""} {e.get("when") or ""} 的视频封面'.strip()
        if has:
            return (f'<img loading="lazy" src="{rel(path, base_dir)}" alt="{esc(v_alt)}">'
                    f'<span class="badge err">本机只有封面{dur_txt}，原片未缓存</span>')
        if thumb and os.path.isfile(thumb):
            return (f'<img loading="lazy" src="{rel(thumb, base_dir)}" alt="{esc(v_alt)}">'
                    f'<span class="badge err">本机只有封面{dur_txt}，原片未缓存</span>')
        return f'<div class="miss">视频：本机未缓存{dur_txt}</div>'

    if kind == "file":
        name = e.get("title") or e.get("fmt") or "文件"
        if has:
            return (f'<a class="filelink" href="{rel(path, base_dir)}" '
                    f'target="_blank" rel="noopener">{esc(name)}</a>'
                    f'<span class="badge">{esc(_size_zh(e.get("size")))}</span>')
        return f'<div class="miss">文件 {esc(name)}：本机未缓存</div>'

    return ""


def _voice_body(e, base_dir):
    """语音块：按微信的样子画成可点的胶囊（声波 + 时长）。

    能拿到本体（被当文件发过的 dt8 常常在 msg/file 里）就挂一个隐藏 `<audio>`，
    点一下真播；拿不到就哑掉，点一下就地说明原因，不留空白也不假装能播。
    """
    dur = e.get("duration") or 0
    path = e.get("path") or ""
    has = bool(path) and os.path.isfile(path)
    width = 72 + min(148, int(dur or 0) * 3)
    label = f"{int(dur)}″" if dur else "语音"
    if has:
        aria = f"播放语音，{int(dur)} 秒" if dur else "播放语音"
        btn = (f'<button type="button" class="voice" data-audio="1" '
               f'style="width:{width}px" aria-label="{esc(aria)}">'
               f'<span class="vicon" aria-hidden="true"><i></i><i></i><i></i></span>'
               f'<span class="vtime">{esc(label)}</span></button>')
        return (f'{btn}<audio preload="none" src="{rel(path, base_dir)}" hidden></audio>')
    aria = (f"语音，{int(dur)} 秒，本体不在本机" if dur else "语音，本体不在本机")
    btn = (f'<button type="button" class="voice na" style="width:{width}px" '
           f'aria-label="{esc(aria)}" aria-disabled="true">'
           f'<span class="vicon" aria-hidden="true"><i></i><i></i><i></i></span>'
           f'<span class="vtime">{esc(label)}</span></button>')
    return f'{btn}<div class="vnote">本体不在本机（PC 端不保留语音文件），无法播放。</div>'


def _location_body(e):
    lat, lng = e.get("lat") or "", e.get("lng") or ""
    place, poi = e.get("place") or "", e.get("poi") or ""
    title = place or poi or "位置"
    sub = poi if (poi and poi != place) else ""
    if lat and lng:
        url = ("https://uri.amap.com/marker?position=%s,%s&name=%s"
               % (esc(lng), esc(lat), urllib.parse.quote(title or lat)))
        link = (f'<a href="{url}" target="_blank" rel="noopener">在地图里打开 · '
                f'{esc(lat)}, {esc(lng)}</a>')
    else:
        link = '<span class="badge">无坐标</span>'
    sub_html = '<div class="poi">%s</div>' % esc(sub) if sub else ""
    return ('<div class="loc"><div class="addr">%s</div>%s%s</div>'
            % (esc(title), sub_html, link))


def entry_html(e, base_dir):
    """单个子项 -> 气泡。"""
    kind = e.get("kind") or "other"
    me = bool(e.get("me"))
    who = e.get("sender") or ""
    when = (e.get("when") or "")[11:16] or (e.get("when") or "")
    label = " · ".join(x for x in (who, when) if x)

    if kind == "text":
        body = esc(e.get("text") or "")
    elif kind == "link":
        # 别复用 label —— 那是「发送人 · 时间」，覆盖掉会在灰字行里再显示一遍标题
        link_text = esc(e.get("title") or e.get("text") or "链接")
        url = (e.get("url") or "").strip()
        if url.startswith(("http://", "https://")):
            body = (f'<a class="filelink" href="{esc(url)}" target="_blank" '
                    f'rel="noopener">{link_text}</a>')
        else:
            body = link_text
    elif kind == "voice":
        body = _voice_body(e, base_dir)
    elif kind == "location":
        body = _location_body(e)
    elif kind == "other":
        # 链接卡片、无坐标位置、未知子类型：正文就在 text 里，照原样显示。
        # 之前一律走媒体分支得到空串，被占位符「本机无可显示内容」顶掉。
        body = esc(e.get("text") or e.get("title") or "")
    else:
        body = _media_body(e, base_dir)
        if not body and (e.get("text") or e.get("title")):
            body = esc(e.get("text") or e.get("title"))

    if not body:
        body = f'<div class="miss">{esc(BODY_KIND_ZH.get(kind, "内容"))}：本机无可显示内容</div>'

    return (f'{_bubble_open(me)}<div class="stack">'
            f'<div class="who">{esc(label)}</div>'
            f'<div class="bubble">{body}</div></div></div>')


def render(pack, html_path):
    """写出单包 HTML，返回路径。pack 结构见模块文档与 engine._export_chat_records。"""
    base_dir = os.path.dirname(os.path.abspath(html_path))
    entries = pack.get("entries") or []
    stats = pack.get("stats") or {}
    title = pack.get("title") or "聊天记录"

    chips = [f'<span class="chip">共 <b>{len(entries)}</b> 条</span>']
    for key, cls, label in ((STATE_FULL, "full", "完整"),
                            (STATE_THUMB, "thumb", "仅缩略图"),
                            (STATE_MISS, "miss", "本机未缓存"),
                            (STATE_META, "", "仅元数据")):
        n = int(stats.get(key) or 0)
        if n:
            chips.append(f'<span class="chip {cls}">{label} <b>{n}</b></span>')

    body = "\n".join(entry_html(e, base_dir) for e in entries) or \
        '<div class="empty">这个包里没有可展开的子项。</div>'

    src_line = " · ".join(x for x in (
        f'收藏于 {esc(pack.get("when") or "")}',
        f'来源 {esc(pack.get("who") or pack.get("fromusr") or "未知")}',
    ) if x)

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body>
<a class="skip" href="#msgs">跳到聊天内容</a>
<div class="wrap">
<header>
<h1>{esc(title)}</h1>
<div class="meta">{src_line}</div>
<div class="chips">{"".join(chips)}</div>
</header>
<main id="msgs">
{body}
</main>
<footer>由 微信收藏导出 V{VERSION} 生成于 {dt.datetime.now():%Y-%m-%d %H:%M} ·
相对路径引用同目录附件，整个目录可直接拷走或打包发送。</footer>
</div>
{JS}
</body>
</html>
"""
    os.makedirs(base_dir, exist_ok=True)
    with open(html_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    return html_path


__all__ = ["render", "entry_html", "STATE_FULL", "STATE_THUMB", "STATE_MISS",
           "STATE_META"]
