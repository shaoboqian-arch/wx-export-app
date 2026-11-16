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
                "path": media[0]["输出路径"],
                "name": "001_2026-08-11_video_缩略.jpg",
                "local_id": 4242,
            }]
            mp = el.write_missing_csv(os.path.join(td, "_未取到原件.csv"), failed)
            with open(mp, encoding="utf-8-sig", newline="") as f:
                mrows = list(csv.DictReader(f))
            self.assertEqual(len(mrows), 1)
            self.assertEqual(mrows[0]["类型"], "视频")
            self.assertEqual(mrows[0]["显示名称"], "2026-08-11 视频：某描述")
            self.assertNotEqual(mrows[0]["显示名称"], "（无文件）")
            self.assertIn("真正播放", mrows[0]["建议"])
            self.assertIn("补导", mrows[0]["建议"])
            self.assertEqual(mrows[0]["local_id"], "4242")
            self.assertEqual(failed[0]["name"], "001_2026-08-11_video_缩略.jpg")

            rp = el.write_readme(
                os.path.join(td, "_导出说明.txt"),
                frm="2026-08-11", to="2026-08-11",
                include_text_links=True, resolve_kinds=("image", "video"),
                counts={"完整": 0, "本地仅缩略图": 1, "头部缺失": 0, "未定位本体": 0},
                written=1, media_total=1, text_count=1, link_count=1,
                census={"语音": 0, "视频号": 2, "其他": 0},
            )
            text = Path(rp).read_text(encoding='utf-8')
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
            self.assertIn("_未取到原件.csv", text)
            self.assertIn("视频号：2", text)
            self.assertNotIn("语音：0", text)
            self.assertNotIn("其他：0", text)

            rp0 = el.write_readme(
                os.path.join(td, "_导出说明-空普查.txt"),
                frm="2026-08-11", to="2026-08-11",
                include_text_links=True, resolve_kinds=("image", "video"),
                counts={"完整": 1, "本地仅缩略图": 0, "头部缺失": 0, "未定位本体": 0},
                written=1, media_total=1, text_count=0, link_count=0,
                census={"语音": 0, "视频号": 0, "其他": 0},
            )
            text0 = Path(rp0).read_text(encoding='utf-8')
            self.assertNotIn("同期还有、本次未纳入的类型", text0)

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
