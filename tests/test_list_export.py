# -*- coding: utf-8 -*-
"""清单解析与批次说明的无密钥单测。只用合成 XML，不读真实收藏或密钥。"""
import csv
from pathlib import Path
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SRC = os.path.join(APP, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import export_list as el  # noqa: E402


TEXT_XML = (
    "<favitem><title>should-not-use-title</title>"
    "<desc>第一行&#10;第二行 &amp; 符号</desc></favitem>"
)
LINK_XML = (
    "<favitem><title>wrong-title</title>"
    "<pagetitle>示例标题 &amp; 说明</pagetitle>"
    "<pagedesc>这是摘要</pagedesc>"
    "<link>https://example.com/a?x=1</link>"
    "<srcdisplayname>示例来源</srcdisplayname></favitem>"
)
EMPTY_LINK_XML = "<favitem><title>only-title</title></favitem>"


class ParseTests(unittest.TestCase):
    def test_text_uses_desc_full_and_unescapes(self):
        body = el.parse_text_content(TEXT_XML)
        self.assertEqual(body, "第一行\n第二行 & 符号")

    def test_link_uses_pagetitle_not_title(self):
        info = el.parse_link_content(LINK_XML)
        self.assertEqual(info["title"], "示例标题 & 说明")
        self.assertEqual(info["summary"], "这是摘要")
        self.assertEqual(info["url"], "https://example.com/a?x=1")
        self.assertEqual(info["source"], "示例来源")

    def test_link_without_pagetitle(self):
        info = el.parse_link_content(EMPTY_LINK_XML)
        self.assertEqual(info["title"], "(无标题)")
        self.assertEqual(info["summary"], "")
        self.assertEqual(info["url"], "")


class DisplayTests(unittest.TestCase):
    def test_display_with_desc(self):
        name = el.display_name({
            "when": "2026-08-11 12:00:00",
            "kind": "video",
            "desc": "某描述",
        })
        self.assertEqual(name, "2026-08-11 视频：某描述")

    def test_display_never_blank_or_placeholder(self):
        name = el.display_name({"when": "2026-08-11 12:00:00", "kind": "image", "desc": ""})
        self.assertEqual(name, "2026-08-11 图片")
        self.assertTrue(name.strip())
        self.assertNotEqual(name, "（无文件）")

    def test_kinds_omitted(self):
        self.assertTrue(el.kinds_omitted(None))
        self.assertTrue(el.kinds_omitted(""))
        self.assertTrue(el.kinds_omitted([]))
        self.assertFalse(el.kinds_omitted("image,video"))
        self.assertFalse(el.kinds_omitted(["image"]))


class FileTests(unittest.TestCase):
    def test_manifest_and_readme_and_missing(self):
        with tempfile.TemporaryDirectory() as td:
            media = [{
                "收藏时间": "2026-08-11 10:00:00",
                "类型": "video",
                "状态": "本地仅缩略图",
                "描述": "某描述",
                "输出路径": os.path.join(td, "_缩略图", "001_2026-08-11_video_缩略.jpg"),
                "来源": "wxid_example",
            }]
            texts = [{
                "when": "2026-08-11 09:00:00",
                "body": "完整正文\n第二行",
                "fromusr": "wxid_example",
            }]
            links = [{
                "when": "2026-08-11 11:00:00",
                "title": "示例标题",
                "summary": "摘要",
                "url": "https://example.com/a",
                "source": "示例来源",
            }]
            mf = os.path.join(td, "_导出清单.csv")
            path, n = el.write_manifest_csv(mf, media, texts, links)
            self.assertEqual(n, 3)
            with open(path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual([r["类型"] for r in rows], ["文本", "视频", "链接"])
            self.assertEqual(rows[0]["内容"], "完整正文\n第二行")
            self.assertEqual(rows[0]["状态"], "已写入清单")
            self.assertEqual(rows[0]["输出路径"], "")
            self.assertEqual(rows[1]["状态"], "本地仅缩略图")
            self.assertTrue(rows[1]["输出路径"])
            self.assertEqual(rows[2]["标题或描述"], "示例标题")
            self.assertEqual(rows[2]["摘要"], "摘要")
            self.assertEqual(rows[2]["链接"], "https://example.com/a")

            failed = [{
                "when": "2026-08-11 10:00:00",
                "kind": "video",
                "state": "本地仅缩略图",
                "reason": "本地只有缩略图，未取到原件",
                "desc": "某描述",
                "fromusr": "wxid_example",
                "path": os.path.join(td, "张三", "001_2026-08-11_video_缩略.jpg"),
                "name": "001_2026-08-11_video_缩略.jpg",
                "local_id": 4242,
            }]
            import report_md as rm
            mrows = [
                [1, "2026-08-11 09:00:00", "文本", "已写入清单", "",
                 "第一行内容\n第二行内容", "", "", "", "wxid_example", ""],
                [2, "2026-08-11 10:00:00", "视频", "本地仅缩略图", "某描述",
                 "", "", "", os.path.join(td, "张三", "001_2026-08-11_video_缩略.jpg"),
                 "wxid_example", ""],
                [3, "2026-08-11 11:00:00", "链接", "已写入清单", "示例文章",
                 "", "文章摘要说明", "https://example.com/a", "", "wxid_example", ""],
                [4, "2026-08-11 12:00:00", "聊天记录", "已生成HTML", "\"张三\"的聊天记录",
                 "", "共 2 条", "", "张三/聊天记录/4242_2026-08-11/index.html", "wxid_example", "4242"],
                [5, "2026-08-11 12:01:00", "聊天记录", "已写入清单", "",
                 "群里一句正文", "张三 · 12:01", "", "", "wxid_example", "4242"],
            ]
            rp = rm.write_report(
                td,
                frm="2026-08-11", to="2026-08-11",
                include_text_links=True, resolve_kinds=("image", "video"),
                counts={"完整": 0, "本地仅缩略图": 1, "头部缺失": 0, "未定位本体": 0, "缓存还原": 1},
                written=1, media_total=1, text_count=1, link_count=1,
                census={"语音": 0, "视频号": 2, "其他": 0},
                failed_items=failed,
                report_rows=[{"序号": "1", "收藏时间": "2026-08-11 10:00:00",
                              "类型": "video", "体积": "764251", "时长": "17",
                              "状态": "本地仅缩略图", "local_id": "4242",
                              "fullmd5": "abc",
                              "输出路径": os.path.join(td, "张三", "001_2026-08-11_video_缩略.jpg"),
                              "描述": "某描述", "来源": "wxid_example",
                              "所属收藏": "", "还原": "缓存还原"}],
                manifest_rows=mrows,
                pending_lids=[4242],
                sender_names={"wxid_example": "张三"})
            self.assertEqual(os.path.basename(rp), "导出报告.md")
            text = Path(rp).read_text(encoding="utf-8")
            self.assertIn("日期范围：2026-08-11 至 2026-08-11（起止均含当天）", text)
            self.assertIn("本批导出内容：图片、视频、语音、文本、链接", text)
            self.assertIn("文本：1 条", text)
            self.assertIn("链接：1 条", text)
            self.assertNotIn("_完整/", text)
            self.assertNotIn("_缩略图/", text)
            self.assertNotIn("_头部缺失/", text)
            self.assertIn("按发送人分文件夹", text)
            self.assertIn("未知发送人", text)
            self.assertIn("_缩略", text)
            self.assertIn("点开大图", text)
            self.assertIn("真正播放", text)
            self.assertIn("补导", text)
            self.assertIn("视频号：2", text)
            self.assertNotIn("语音：0", text)
            self.assertNotIn("其他：0", text)
            self.assertEqual(failed[0]["name"], "001_2026-08-11_video_缩略.jpg")
            # 不得残留旧 CSV 文件名与 11~13 列宽表
            self.assertNotIn("_未取到原件.csv", text)
            self.assertNotIn("_导出报告.csv", text)
            self.assertNotIn("_导出清单.csv", text)
            self.assertNotIn("| 序号 | 收藏时间 | 类型 | 状态 | 标题或描述 |", text)
            self.assertNotIn("| 序号 | 收藏时间 | 类型 | 体积 | 时长 |", text)
            # 未取到原件改为结构化列表
            self.assertIn("未取到原件（1 条）", text)
            self.assertIn("2026-08-11 视频：某描述", text)
            self.assertIn("`local_id: 4242`", text)
            # 导出明细按发送人分组，含易读体积/时长、文本引用块、链接、聊天记录打开网页与子项缩进
            self.assertIn("## 导出明细（4 条）", text)
            self.assertIn("### 张三（4 条）", text)
            self.assertIn("746 KB · 17 秒 · 缓存还原", text)
            self.assertIn("  > 第一行内容", text)
            self.assertIn("  > 第二行内容", text)
            self.assertIn("[示例文章](<https://example.com/a>)", text)
            self.assertIn("[打开网页](<张三/聊天记录/4242_2026-08-11/index.html>)", text)
            self.assertIn("  - [聊天记录] 张三 · 12:01：群里一句正文", text)
            # 对账表/明细里的绝对路径相对化到批次根：报告随目录拷走转发时不泄露本机路径
            self.assertIn("[某描述](<张三/001_2026-08-11_video_缩略.jpg>)", text)
            self.assertNotIn(td, text)
            # 补导摘要与 _补导状态.json 口径一致
            self.assertIn("待补条目：1", text)
            self.assertIn("同期还有、本次未纳入的类型", text)

            # 无任何导出条目、未取到原件为 0 时仍应正常输出
            rp0 = rm.write_report(
                td,
                frm="2026-08-11", to="2026-08-11",
                include_text_links=True, resolve_kinds=("image", "video"),
                counts={"完整": 1, "本地仅缩略图": 0, "头部缺失": 0, "未定位本体": 0},
                written=1, media_total=1, text_count=0, link_count=0,
                census={"语音": 0, "视频号": 0, "其他": 0},
                failed_items=[], report_rows=[], manifest_rows=[], pending_lids=[])
            text0 = Path(rp0).read_text(encoding="utf-8")
            self.assertIn("本批无未取到原件", text0)
            self.assertNotIn("同期还有、本次未纳入的类型", text0)

    def test_report_groups_unknown_sender_last_and_collapses_long_chat(self):
        import report_md as rm
        with tempfile.TemporaryDirectory() as td:
            mrows = [
                [1, "2026-08-11 08:00:00", "图片", "完整", "",
                 "", "", "", os.path.join(td, "未知发送人", "001_2026-08-11_image.jpg"), "", ""],
                [2, "2026-08-11 09:00:00", "视频", "完整", "一段视频",
                 "", "", "", os.path.join(td, "好友甲", "002_2026-08-11_video.mp4"), "wxid_alpha", ""],
                [3, "2026-08-11 10:00:00", "聊天记录", "已生成HTML", "\"好友甲\"的聊天记录",
                 "", "共 10 条", "", "好友甲/聊天记录/009999_2026-08-11/index.html", "wxid_alpha", "9999"],
            ]
            for n in range(1, 11):
                mrows.append([
                    3 + n, f"2026-08-11 10:{n:02d}:00", "聊天记录", "已写入清单", "",
                    f"第{n}条子消息", f"好友甲 · 10:{n:02d}", "", "", "wxid_alpha", "9999",
                ])
            rp = rm.write_report(
                td,
                frm="2026-08-11", to="2026-08-11",
                include_text_links=True, resolve_kinds=("image", "video"),
                counts={"完整": 2, "本地仅缩略图": 0, "头部缺失": 0, "未定位本体": 0},
                written=2, media_total=2, text_count=0, link_count=0,
                manifest_rows=mrows,
                report_rows=[
                    {"序号": "1", "收藏时间": "2026-08-11 08:00:00", "类型": "image",
                     "体积": "2048", "时长": "", "状态": "完整", "local_id": "10",
                     "输出路径": os.path.join(td, "未知发送人", "001_2026-08-11_image.jpg"),
                     "描述": "", "来源": "", "所属收藏": "", "还原": ""},
                    {"序号": "2", "收藏时间": "2026-08-11 09:00:00", "类型": "video",
                     "体积": "5242880", "时长": "125", "状态": "完整", "local_id": "11",
                     "输出路径": os.path.join(td, "好友甲", "002_2026-08-11_video.mp4"),
                     "描述": "一段视频", "来源": "wxid_alpha", "所属收藏": "", "还原": "本体"},
                ],
                failed_items=[
                    {"when": "2026-08-11 10:00:00", "kind": "video", "state": "未定位本体",
                     "reason": "本机无本体（原件只在微信 CDN）", "desc": "", "fromusr": "wxid_alpha",
                     "path": "", "name": "", "local_id": 9999},
                    {"when": "2026-08-11 10:00:00", "kind": "video", "state": "未定位本体",
                     "reason": "本机无本体（原件只在微信 CDN）", "desc": "", "fromusr": "wxid_alpha",
                     "path": "", "name": "", "local_id": 9999},
                ],
                pending_lids=[],
                sender_names={"wxid_alpha": "好友甲"},
            )
            txt = Path(rp).read_text(encoding="utf-8")
            self.assertIn("### 好友甲（2 条）", txt)
            self.assertIn("### 未知发送人（1 条）", txt)
            self.assertLess(txt.index("### 好友甲"), txt.index("### 未知发送人"))
            self.assertIn("5.00 MB · 2 分 05 秒", txt)
            self.assertNotIn("· 本体", txt)
            self.assertIn("未取到原件（2 条）", txt)
            self.assertIn("× 2 件", txt)
            self.assertIn("共 10 条子项，完整图文与语音请点上方「打开网页」查看", txt)
            self.assertIn("[001_2026-08-11_image.jpg](<未知发送人/001_2026-08-11_image.jpg>)", txt)

    def test_census_and_status(self):
        # 3/8 语音现在也进默认范围（V0.4.3 起），所以不再出现在普查里；
        # 14 已被纳入默认范围；18 未登记 -> 绝不并进「其他」
        c = el.census_from_types({1: 3, 2: 10, 5: 2, 16: 4, 3: 1, 8: 2, 14: 7, 18: 1})
        self.assertEqual(c, {"未知类型 18": 1})
        status = el.format_done_status({
            "include_text_links": True,
            "written": 12,
            "total": 30,
            "counts": {"完整": 12, "本地仅缩略图": 8, "头部缺失": 1, "未定位本体": 9},
            "text_count": 5,
            "link_count": 3,
            "batch": "export-2026-08-11_2026-08-11-test",
        })
        self.assertIn("本批含图片、视频、文本和链接", status)
        self.assertIn("写出 12/30 个文件", status)
        self.assertIn("头部缺失 1", status)
        self.assertIn("文本 5", status)
        self.assertIn("链接 3", status)
        self.assertTrue(len(status) > 50)


if __name__ == "__main__":
    unittest.main()
