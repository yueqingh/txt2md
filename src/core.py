"""核心逻辑：把多个 txt 合并成 Markdown。不依赖界面，可单独测试。"""

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

log = logging.getLogger("core")

SEP = "\n\n---\n\n"  # 文档之间的分隔线


@dataclass
class Item:
    path: str
    rel_dir: str  # 相对于所添加文件夹的子文件夹（用 / 连接），没有则为 ""
    name: str  # 不含扩展名的文件名
    size: int
    mtime: float


@dataclass
class Options:
    title_mode: str = "filename"  # "filename"：用文件名；"firstline"：用文件第一行
    include_dir: bool = True  # 标题里带上子文件夹名
    squeeze: bool = True  # 去掉多余空行与行尾空格
    dedupe: bool = False  # 内容完全相同的文件只保留一份
    escape: bool = True  # 转义正文里会破坏结构的符号
    toc: bool = False  # 生成目录
    split_kb: int = 0  # >0 时，超过这个大小（KB）就拆成多个文件


@dataclass
class Section:
    title: str
    body: str
    path: str
    encoding: str


@dataclass
class Result:
    out_paths: List[str] = field(default_factory=list)
    n_files: int = 0
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    chars_before: int = 0
    chars_after: int = 0
    bytes_total: int = 0
    dupes: int = 0


# ---------------------------------------------------------------- 收集文件
def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def item_from_path(path: str, rel_dir: str = "") -> Item:
    st = os.stat(path)
    name = os.path.splitext(os.path.basename(path))[0]
    return Item(os.path.abspath(path), rel_dir, name, st.st_size, st.st_mtime)


def collect(paths, existing=()) -> Tuple[List[Item], List[Tuple[str, str]]]:
    """把用户选的文件/文件夹展开成 Item 列表。文件夹只收 .txt，子文件夹递归。"""
    seen = {os.path.normcase(os.path.abspath(p)) for p in existing}
    items: List[Item] = []
    skipped: List[Tuple[str, str]] = []

    def add(path, rel_dir):
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            skipped.append((path, "已在列表中"))
            return
        try:
            items.append(item_from_path(path, rel_dir))
            seen.add(key)
        except OSError as e:
            skipped.append((path, f"无法访问：{e}"))

    for p in paths:
        p = os.path.abspath(p)
        if os.path.isdir(p):
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames.sort(key=natural_key)
                rel = os.path.relpath(dirpath, p)
                rel_dir = "" if rel == "." else rel.replace(os.sep, "/")
                for fn in sorted(filenames, key=natural_key):
                    if fn.lower().endswith(".txt"):
                        add(os.path.join(dirpath, fn), rel_dir)
        elif os.path.isfile(p):
            add(p, "")
        else:
            skipped.append((p, "路径不存在"))
    return items, skipped


def sort_items(items: List[Item], mode: str) -> None:
    if mode == "name":
        items.sort(key=lambda i: natural_key(i.rel_dir + "/" + i.name))
    elif mode == "mtime":
        items.sort(key=lambda i: i.mtime)
    # "manual"：保持现有顺序


# ---------------------------------------------------------------- 读取与整理
def read_text(path: str) -> Tuple[str, str]:
    """读取 txt，自动识别 UTF-8 / UTF-8 带 BOM / UTF-16 / GBK(GB18030)。返回 (文本, 编码名)。"""
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace"), "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace"), "utf-16"
    if b"\x00" in raw[:4096]:
        raise ValueError("看起来不是文本文件（含二进制内容）")
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace"), "utf-8(部分乱码)"


_HEAD = re.compile(r"^\s{0,3}#{1,6}(\s|$)")
_RULE = re.compile(r"^\s{0,3}([-=*_])(\s*\1){2,}\s*$")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")


def escape_structure(text: str) -> str:
    """正文里以 # 开头、纯横线、``` 围栏的行，加反斜杠，避免被当成标题/分隔线/代码块。"""
    out = []
    for line in text.split("\n"):
        if _HEAD.match(line) or _RULE.match(line) or _FENCE.match(line):
            idx = len(line) - len(line.lstrip())
            line = line[:idx] + "\\" + line[idx:]
        out.append(line)
    return "\n".join(out)


def normalize(text: str, squeeze: bool) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("﻿", "")
    if squeeze:
        lines = [ln.rstrip() for ln in text.split("\n")]
        out, blank = [], 0
        for ln in lines:
            if ln == "":
                blank += 1
                if blank > 1:
                    continue
            else:
                blank = 0
            out.append(ln)
        text = "\n".join(out)
    return text.strip("\n")


def _clean_title(t: str) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^#+\s*", "", t)
    return t[:120]


def make_title(item: Item, first_line: Optional[str], opts: Options) -> str:
    if opts.title_mode == "firstline" and first_line:
        base = _clean_title(first_line)
    else:
        base = _clean_title(item.name)
    if opts.include_dir and item.rel_dir:
        base = item.rel_dir.replace("/", " / ") + " / " + base
    return base or "（无标题）"


def build_sections(
    items: List[Item],
    opts: Options,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Tuple[List[Section], Result]:
    res = Result()
    sections: List[Section] = []
    seen_hash = set()
    total = len(items)
    for i, it in enumerate(items, 1):
        if progress:
            progress(i, total)
        try:
            text, enc = read_text(it.path)
        except Exception as e:  # 单个文件失败不影响其他文件
            log.warning("跳过 %s：%s", it.name, e)
            res.skipped.append((it.path, str(e)))
            continue
        res.chars_before += len(text)
        text = normalize(text, opts.squeeze)
        if not text.strip():
            res.skipped.append((it.path, "内容为空"))
            log.info("跳过空文件：%s", it.name)
            continue
        first_line = None
        if opts.title_mode == "firstline":
            lines = text.split("\n")
            while lines and not lines[0].strip():
                lines.pop(0)
            if lines:
                first_line = lines.pop(0)
                text = "\n".join(lines).strip("\n")
        if opts.dedupe:
            h = hashlib.sha1(text.encode("utf-8")).hexdigest()
            if h in seen_hash:
                res.dupes += 1
                res.skipped.append((it.path, "内容与前面的文件完全相同（已去重）"))
                log.info("去重：%s", it.name)
                continue
            seen_hash.add(h)
        body = escape_structure(text) if opts.escape else text
        sections.append(Section(make_title(it, first_line, opts), body, it.path, enc))
        res.chars_after += len(body)
        log.info("读入 %s（编码 %s，%d 字）", it.name, enc, len(body))
    res.n_files = len(sections)
    return sections, res


# ---------------------------------------------------------------- 输出
def _block(s: Section) -> str:
    return f"# {s.title}\n\n{s.body}" if s.body else f"# {s.title}"


def _toc(sections: List[Section]) -> str:
    lines = ["**目录**", ""] + [f"{i}. {s.title}" for i, s in enumerate(sections, 1)]
    return "\n".join(lines)


def render_parts(sections: List[Section], opts: Options) -> List[str]:
    """按选项渲染成一个或多个文档文本。拆分只发生在文档之间，不会把一个 txt 劈成两半。"""
    if not sections:
        return []
    groups: List[List[Section]] = []
    if opts.split_kb and opts.split_kb > 0:
        limit = opts.split_kb * 1024
        cur: List[Section] = []
        cur_size = 0
        for s in sections:
            b = len((_block(s) + SEP).encode("utf-8"))
            if cur and cur_size + b > limit:
                groups.append(cur)
                cur, cur_size = [], 0
            cur.append(s)
            cur_size += b
        if cur:
            groups.append(cur)
    else:
        groups = [sections]

    parts = []
    for g in groups:
        blocks = [_block(s) for s in g]
        if opts.toc:
            blocks.insert(0, _toc(g))
        parts.append(SEP.join(blocks) + "\n")
    return parts


def part_paths(out_path: str, n: int) -> List[str]:
    if n <= 1:
        return [out_path]
    base, ext = os.path.splitext(out_path)
    return [f"{base}-{i}{ext or '.md'}" for i in range(1, n + 1)]


def merge(
    items: List[Item],
    opts: Options,
    out_path: str,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Result:
    sections, res = build_sections(items, opts, progress)
    if not sections:
        raise ValueError("没有可合并的内容（文件都为空或读取失败）")
    parts = render_parts(sections, opts)
    paths = part_paths(out_path, len(parts))
    for p, text in zip(paths, parts):
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        res.bytes_total += len(text.encode("utf-8"))
        log.info("写出 %s（%d 字节）", os.path.basename(p), len(text.encode("utf-8")))
    res.out_paths = paths
    return res
