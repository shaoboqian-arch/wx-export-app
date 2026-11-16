# -*- coding: utf-8 -*-
"""发送人命名、归档移动与补导升格的无密钥单测。只用临时目录和伪文件，不读真实收藏。"""
import csv
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SRC = os.path.join(APP, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import sender_archive as sa  # noqa: E402
import export_list as el  # noqa: E402
import engine  # noqa: E402


class CleanNameTests(unittest.TestCase):
    def test_illegal_chars_and_whitespace(self):
        self.assertEqual(sa.clean_name(r'张/三:李*四?', "x"), "张_三_李_四_")
        self.assertEqual(sa.clean_name("  阿  里  ", "x"), "阿 里")
        self.assertEqual(sa.clean_name("name.\t", "x"), "name")

    def test_reserved_and_length_and_empty(self):
        self.assertEqual(sa.clean_name("CON", "x"), "_CON")
        self.assertEqual(sa.clean_name("lpt9.txt", "x"), "_lpt9.txt")
        self.assertEqual(len(sa.clean_name("哈" * 100, "x")), 80)
        self.assertEqual(sa.clean_name("", "fallback"), "fallback")
        self.assertEqual(sa.clean_name("\u200b\u200b", "fallback"), "fallback")


class FolderNameTests(unittest.TestCase):
    def test_remark_over_nick_over_wxid(self):
        contacts = {
            "wxid_a": {"remark": "备注甲", "nick": "昵称甲"},
            "wxid_b": {"remark": "", "nick": "昵称乙"},
        }
        names = sa.folder_names_for(
            ["wxid_a", "wxid_b", "wxid_c", ""],
            contacts=contacts, roster={},
        )
        self.assertEqual(names["wxid_a"], "备注甲")
        self.assertEqual(names["wxid_b"], "昵称乙")
        self.assertEqual(names["wxid_c"], "wxid_c")
        self.assertEqual(names[""], "未知发送人")

    def test_same_name_different_wxid_gets_suffix(self):
        contacts = {
            "wxid_aaa00001": {"remark": "张三", "nick": ""},
            "wxid_bbb00002": {"remark": "张三", "nick": ""},
        }
        names = sa.folder_names_for(
            ["wxid_aaa00001", "wxid_bbb00002"],
            contacts=contacts, roster={},
        )
        self.assertEqual(names["wxid_aaa00001"], "张三")
        self.assertEqual(names["wxid_bbb00002"], "张三（wxid_bbb00）")

    def test_roster_only_adds_never_changes(self):
        with tempfile.TemporaryDirectory() as td:
            roster_file = os.path.join(td, "sender_roster.json")
            contacts = {
                "wxid_old": {"remark": "旧名", "nick": ""},
                "wxid_new": {"remark": "张三", "nick": ""},
            }
            first = sa.folder_names_for(
                ["wxid_old"], contacts=contacts, roster_file=roster_file)
            self.assertEqual(first["wxid_old"], "旧名")
            # 联系人改了备注，名册仍沿用旧文件夹名
            contacts["wxid_old"]["remark"] = "新备注"
            contacts["wxid_dup"] = {"remark": "旧名", "nick": ""}
            second = sa.folder_names_for(
                ["wxid_old", "wxid_dup", "wxid_new"],
                contacts=contacts, roster_file=roster_file)
            self.assertEqual(second["wxid_old"], "旧名")
            self.assertEqual(second["wxid_dup"], "旧名（wxid_dup）")
            self.assertEqual(second["wxid_new"], "张三")
            roster = sa.load_roster(roster_file)
            self.assertEqual(roster["wxid_old"], "旧名")
            self.assertNotIn("新备注", roster.values())


class ArchiveMoveTests(unittest.TestCase):
    def test_move_into_sender_folders_and_drop_status_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            full = os.path.join(td, "_完整")
            thumb = os.path.join(td, "_缩略图")
            part = os.path.join(td, "_头部缺失")
            os.makedirs(full)
            os.makedirs(thumb)
            os.makedirs(part)
            f1 = os.path.join(full, "001_2026-11-01_image.jpg")
            f2 = os.path.join(thumb, "002_2026-11-02_image_缩略.jpg")
            Path(f1).write_bytes(b"full-bytes")
            Path(f2).write_bytes(b"thumb-bytes")
            rows = [
                {
                    "序号": "1", "收藏时间": "2026-11-01 10:00:00", "类型": "image",
                    "体积": "10", "时长": "", "状态": "完整", "local_id": "11",
                    "fullmd5": "", "head256md5": "", "输出路径": f1,
                    "描述": "图一", "来源": "wxid_zhang",
                },
                {
                    "序号": "2", "收藏时间": "2026-11-02 10:00:00", "类型": "image",
                    "体积": "8", "时长": "", "状态": "本地仅缩略图", "local_id": "22",
                    "fullmd5": "", "head256md5": "", "输出路径": f2,
                    "描述": "图二", "来源": "wxid_li",
                },
            ]
            contacts = {
                "wxid_zhang": {"remark": "张三", "nick": "z"},
                "wxid_li": {"remark": "", "nick": "李四"},
            }
            roster_file = os.path.join(td, "roster.json")
            names = sa.archive_batch(td, rows, contacts=contacts, roster_file=roster_file)
            self.assertEqual(names["wxid_zhang"], "张三")
            self.assertEqual(names["wxid_li"], "李四")
            dest1 = os.path.join(td, "张三", "001_2026-11-01_image.jpg")
            dest2 = os.path.join(td, "李四", "002_2026-11-02_image_缩略.jpg")
            self.assertTrue(os.path.isfile(dest1))
            self.assertTrue(os.path.isfile(dest2))
            self.assertFalse(os.path.exists(f1))
            self.assertFalse(os.path.exists(f2))
            self.assertFalse(os.path.isdir(full))
            self.assertFalse(os.path.isdir(thumb))
            self.assertFalse(os.path.isdir(part))
            self.assertEqual(rows[0]["输出路径"], dest1)
            self.assertEqual(rows[1]["输出路径"], dest2)
            # 不留双份
            self.assertEqual(len(list(Path(td).rglob("001_2026-11-01_image.jpg"))), 1)

    def test_does_not_overwrite_different_existing_file(self):
        with tempfile.TemporaryDirectory() as td:
            full = os.path.join(td, "_完整")
            os.makedirs(full)
            src = os.path.join(full, "001_2026-11-01_image.jpg")
            dest_dir = os.path.join(td, "张三")
            os.makedirs(dest_dir)
            dest = os.path.join(dest_dir, "001_2026-11-01_image.jpg")
            Path(src).write_bytes(b"new-bytes")
            Path(dest).write_bytes(b"old-bytes-different")
            rows = [{
                "序号": "1", "收藏时间": "2026-11-01 10:00:00", "类型": "image",
                "体积": "9", "时长": "", "状态": "完整", "local_id": "11",
                "fullmd5": "", "head256md5": "", "输出路径": src,
                "描述": "", "来源": "wxid_zhang",
            }]
            sa.archive_batch(td, rows, contacts={"wxid_zhang": {"remark": "张三"}},
                             roster_file=os.path.join(td, "r.json"))
            self.assertTrue(os.path.isfile(src))
            self.assertEqual(Path(dest).read_bytes(), b"old-bytes-different")
            self.assertEqual(rows[0]["输出路径"], src)


class UpgradeTests(unittest.TestCase):
    def test_upgrade_writes_complete_and_deletes_thumb(self):
        with tempfile.TemporaryDirectory() as td:
            folder = os.path.join(td, "张三")
            os.makedirs(folder)
            thumb = os.path.join(folder, "001_2026-11-01_image_缩略.jpg")
            Path(thumb).write_bytes(b"thumb")
            row = {
                "序号": "1", "收藏时间": "2026-11-01 10:00:00", "类型": "image",
                "状态": "本地仅缩略图", "输出路径": thumb, "来源": "wxid_zhang",
                "local_id": "11", "描述": "",
            }
            dest = sa.apply_upgrade(td, row, b"complete-jpeg", "jpg", folder_name="张三")
            self.assertTrue(dest)
            self.assertTrue(os.path.isfile(dest))
            self.assertFalse(os.path.isfile(thumb))
            self.assertEqual(os.path.basename(dest), "001_2026-11-01_image.jpg")
            self.assertEqual(row["状态"], "完整")
            self.assertEqual(Path(dest).read_bytes(), b"complete-jpeg")

    def test_upgrade_skips_different_complete_and_keeps_thumb(self):
        with tempfile.TemporaryDirectory() as td:
            folder = os.path.join(td, "张三")
            os.makedirs(folder)
            thumb = os.path.join(folder, "001_2026-11-01_image_缩略.jpg")
            complete = os.path.join(folder, "001_2026-11-01_image.jpg")
            Path(thumb).write_bytes(b"thumb")
            Path(complete).write_bytes(b"already-different")
            row = {
                "序号": "1", "收藏时间": "2026-11-01 10:00:00", "类型": "image",
                "状态": "本地仅缩略图", "输出路径": thumb, "来源": "wxid_zhang",
            }
            dest = sa.apply_upgrade(td, row, b"new-complete", "jpg", folder_name="张三")
            self.assertIsNone(dest)
            self.assertTrue(os.path.isfile(thumb))
            self.assertEqual(Path(complete).read_bytes(), b"already-different")
            self.assertEqual(row["状态"], "本地仅缩略图")

    def test_leave_unresolved_does_not_rewrite(self):
        with tempfile.TemporaryDirectory() as td:
            folder = os.path.join(td, "张三")
            os.makedirs(folder)
            thumb = os.path.join(folder, "001_2026-11-01_image_缩略.jpg")
            Path(thumb).write_bytes(b"thumb")
            mtime = os.path.getmtime(thumb)
            time.sleep(0.05)
            row = {"输出路径": thumb, "状态": "本地仅缩略图"}
            sa.leave_unresolved(row)
            self.assertEqual(Path(thumb).read_bytes(), b"thumb")
            self.assertEqual(os.path.getmtime(thumb), mtime)


class RetryStateAndReadmeTests(unittest.TestCase):
    def test_retry_state_has_no_secrets(self):
        with tempfile.TemporaryDirectory() as td:
            path = engine.write_retry_state(
                td, frm="2026-11-01", to="2026-11-15",
                kinds=["image", "video"], include_text_links=True,
                pending_lids=[11, 22],
            )
            self.assertEqual(os.path.basename(path), "_补导状态.json")
            text = Path(path).read_text(encoding="utf-8")
            self.assertNotIn("image_key", text)
            self.assertNotIn("image_aes", text)
            self.assertNotIn("account", text.lower())
            st = engine.read_retry_state(td)
            self.assertEqual(st["from"], "2026-11-01")
            self.assertEqual(st["to"], "2026-11-15")
            self.assertEqual(st["pending_lids"], [11, 22])
            self.assertTrue(st["include_text_links"])
            self.assertEqual(st["kinds"], ["image", "video"])

    def test_readme_and_items_carry_local_id(self):
        with tempfile.TemporaryDirectory() as td:
            rp = el.write_readme(
                os.path.join(td, "_导出说明.txt"),
                frm="2026-11-01", to="2026-11-02",
                include_text_links=True, resolve_kinds=("image", "video"),
                counts={"完整": 1, "本地仅缩略图": 1, "头部缺失": 0, "未定位本体": 0},
                written=2, media_total=2, text_count=0, link_count=0,
            )
            text = Path(rp).read_text(encoding="utf-8")
            self.assertIn("按发送人分文件夹", text)
            self.assertIn("_缩略", text)
            self.assertIn("补导", text)
            self.assertNotIn("_完整/", text)

            rows = [
                {"序号": "1", "收藏时间": "2026-11-01 10:00:00", "类型": "image",
                 "状态": "完整", "local_id": "11", "输出路径": "/tmp/a.jpg",
                 "描述": "图", "来源": "wxid_a"},
                {"序号": "2", "收藏时间": "2026-11-02 10:00:00", "类型": "video",
                 "状态": "本地仅缩略图", "local_id": "22", "输出路径": "/tmp/b.jpg",
                 "描述": "视", "来源": "wxid_b"},
            ]
            items, failed, by_kind = engine._items_from_report(rows)
            self.assertEqual(items[0]["lid"], "11")
            self.assertEqual(failed[0]["local_id"], "22")
            self.assertEqual(by_kind["image"]["ok"], 1)
            self.assertEqual(by_kind["video"]["failed"], 1)
            self.assertEqual(_pending := engine._pending_lids(rows), [22])


if __name__ == "__main__":
    unittest.main()
