import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import core  # noqa: E402
import diag  # noqa: E402


def w(path, data, mode="wb"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as f:
        f.write(data)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "笔记")

    def merge(self, opts=None, out=None):
        items, _ = core.collect([self.root])
        core.sort_items(items, "name")
        out = out or os.path.join(self.tmp, "out.md")
        res = core.merge(items, opts or core.Options(), out)
        return res

    def read(self, p):
        with open(p, encoding="utf-8") as f:
            return f.read()

    def test_basic_titles_and_separator(self):
        w(os.path.join(self.root, "b.txt"), "第二个".encode("utf-8"))
        w(os.path.join(self.root, "a.txt"), "第一个".encode("utf-8"))
        res = self.merge()
        text = self.read(res.out_paths[0])
        self.assertEqual(text, "# a\n\n第一个\n\n---\n\n# b\n\n第二个\n")

    def test_natural_sort(self):
        for n in ("10", "2", "1"):
            w(os.path.join(self.root, f"第{n}章.txt"), n.encode())
        items, _ = core.collect([self.root])
        core.sort_items(items, "name")
        self.assertEqual([i.name for i in items], ["第1章", "第2章", "第10章"])

    def test_encodings(self):
        w(os.path.join(self.root, "gbk.txt"), "中文GBK内容".encode("gbk"))
        w(os.path.join(self.root, "bom.txt"), b"\xef\xbb\xbf" + "带BOM".encode("utf-8"))
        w(os.path.join(self.root, "u16.txt"), "UTF16文本".encode("utf-16"))
        w(os.path.join(self.root, "u8.txt"), "普通UTF8".encode("utf-8"))
        text = self.read(self.merge().out_paths[0])
        for s in ("中文GBK内容", "带BOM", "UTF16文本", "普通UTF8"):
            self.assertIn(s, text)
        self.assertNotIn("﻿", text)

    def test_binary_skipped(self):
        w(os.path.join(self.root, "bin.txt"), b"abc\x00\x01\x02def")
        w(os.path.join(self.root, "ok.txt"), b"ok")
        res = self.merge()
        self.assertEqual(res.n_files, 1)
        self.assertEqual(len(res.skipped), 1)

    def test_escape_structure(self):
        body = "# 正文里的标题\n---\n```\ncode\n普通行 # 不动\n***加粗***"
        w(os.path.join(self.root, "x.txt"), body.encode())
        text = self.read(self.merge().out_paths[0])
        self.assertIn("\\# 正文里的标题", text)
        self.assertIn("\\---", text)
        self.assertIn("\\```", text)
        self.assertIn("普通行 # 不动", text)
        self.assertIn("***加粗***", text)
        # 只有文件标题是真正的一级标题
        h1 = [ln for ln in text.split("\n") if ln.startswith("# ")]
        self.assertEqual(h1, ["# x"])

    def test_squeeze(self):
        w(os.path.join(self.root, "x.txt"), b"a  \n\n\n\n\nb\r\n\r\n\r\nc")
        text = self.read(self.merge().out_paths[0])
        self.assertEqual(text, "# x\n\na\n\nb\n\nc\n")

    def test_dedupe(self):
        w(os.path.join(self.root, "a.txt"), b"same")
        w(os.path.join(self.root, "b.txt"), b"same")
        w(os.path.join(self.root, "c.txt"), b"other")
        res = self.merge(core.Options(dedupe=True))
        self.assertEqual(res.n_files, 2)
        self.assertEqual(res.dupes, 1)

    def test_subfolder_in_title(self):
        w(os.path.join(self.root, "项目A", "会议.txt"), b"x")
        text = self.read(self.merge().out_paths[0])
        self.assertIn("# 项目A / 会议", text)
        text2 = self.read(self.merge(core.Options(include_dir=False)).out_paths[0])
        self.assertIn("# 会议", text2)
        self.assertNotIn("项目A", text2)

    def test_firstline_title(self):
        w(os.path.join(self.root, "a.txt"), "\n\n  真正的标题  \n正文第一行\n".encode())
        text = self.read(self.merge(core.Options(title_mode="firstline")).out_paths[0])
        self.assertEqual(text, "# 真正的标题\n\n正文第一行\n")

    def test_toc(self):
        w(os.path.join(self.root, "a.txt"), b"1")
        w(os.path.join(self.root, "b.txt"), b"2")
        text = self.read(self.merge(core.Options(toc=True)).out_paths[0])
        self.assertTrue(text.startswith("**目录**\n\n1. a\n2. b"))

    def test_split(self):
        for i in range(6):
            w(os.path.join(self.root, f"f{i}.txt"), ("字" * 600).encode("utf-8"))  # ~1.8KB
        res = self.merge(core.Options(split_kb=4))
        self.assertGreater(len(res.out_paths), 1)
        self.assertTrue(res.out_paths[0].endswith("out-1.md"))
        total = sum(self.read(p).count("# f") for p in res.out_paths)
        self.assertEqual(total, 6)
        for p in res.out_paths:  # 不把单个文件劈开
            self.assertEqual(self.read(p).count("字") % 600, 0)

    def test_empty_all_raises(self):
        w(os.path.join(self.root, "a.txt"), b"   \n\n")
        with self.assertRaises(ValueError):
            self.merge()

    def test_add_duplicate_path(self):
        w(os.path.join(self.root, "a.txt"), b"x")
        items, _ = core.collect([self.root])
        again, skipped = core.collect([self.root], existing=[i.path for i in items])
        self.assertEqual(again, [])
        self.assertEqual(len(skipped), 1)

    def test_explicit_file_any_extension(self):
        p = os.path.join(self.tmp, "note.log")
        w(p, b"hello")
        items, _ = core.collect([p])
        self.assertEqual(len(items), 1)


class DiagTests(unittest.TestCase):
    def test_mask(self):
        s = diag.mask("联系 a.b@c.com 手机 13812345678 token=abcdef123 sk-ABCDEFGHIJKLMNOPQRST")
        self.assertNotIn("a.b@c.com", s)
        self.assertNotIn("13812345678", s)
        self.assertNotIn("abcdef123", s)
        self.assertNotIn("sk-ABCDEFGHIJKLMNOPQRST", s)

    def test_report(self):
        d = diag.Diagnostics()
        d.state_provider = lambda: {"文件数": 3}
        d.action("点击：开始合并")
        try:
            1 / 0
        except Exception:
            d.record_error("测试", *sys.exc_info())
        r = d.build_report()
        for key in ("软件版本", "已脱敏字段", "运行环境", "当前状态", "用户操作", "最近错误", "最近日志", "ZeroDivisionError"):
            self.assertIn(key, r)
        self.assertEqual(d.ring.buf.maxlen, 200)


if __name__ == "__main__":
    unittest.main()
