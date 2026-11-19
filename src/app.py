"""txt 合并为 Markdown —— 图形界面。"""

import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

import core
import diag
import version as V

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    HAS_DND = True
except Exception:  # 拖入组件不可用时，按钮功能照常使用
    HAS_DND = False

log = logging.getLogger("界面")
D = diag.Diagnostics()

# ---- 配色与字体（极简、浅色）
BG = "#F5F6F8"
CARD = "#FFFFFF"
TEXT = "#1F2328"
MUTED = "#6B7280"
ACCENT = "#2F6FED"
ACCENT_HOVER = "#2459C7"
BORDER = "#E3E6EA"
FONT = "Microsoft YaHei UI"


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class App:
    def __init__(self):
        try:  # 高分屏下文字不发虚
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        try:
            self.root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
            self.dnd_ok = HAS_DND
        except Exception:
            self.root = tk.Tk()
            self.dnd_ok = False
        self.root.title(f"{V.APP_NAME}  v{V.VERSION}")
        self.root.geometry("860x700")
        self.root.minsize(760, 620)
        self.root.configure(bg=BG)
        self.root.report_callback_exception = self._tk_exception

        self.items = []
        self.q = queue.Queue()
        self.busy = False
        self.last_output = ""
        self._crashing = False

        self.v_sort = tk.StringVar(value="name")
        self.v_title = tk.StringVar(value="filename")
        self.v_dir = tk.BooleanVar(value=True)
        self.v_squeeze = tk.BooleanVar(value=True)
        self.v_dedupe = tk.BooleanVar(value=False)
        self.v_escape = tk.BooleanVar(value=True)
        self.v_toc = tk.BooleanVar(value=False)
        self.v_split = tk.BooleanVar(value=False)
        self.v_split_mb = tk.StringVar(value="1.0")

        self._style()
        self._build()
        D.state_provider = self._state
        D.install_hooks(on_crash=lambda: self.root.after(0, self._crash_dialog))
        log.info("启动 v%s，拖入组件=%s", V.VERSION, "可用" if self.dnd_ok else "不可用")
        D.action("启动程序")

    # ------------------------------------------------------------ 样式
    def _style(self):
        s = ttk.Style(self.root)
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure(".", font=(FONT, 10), background=BG, foreground=TEXT)
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=CARD)
        s.configure("TLabel", background=BG, foreground=TEXT)
        s.configure("Card.TLabel", background=CARD)
        s.configure("Muted.TLabel", background=BG, foreground=MUTED, font=(FONT, 9))
        s.configure("Title.TLabel", font=(FONT, 16, "bold"))
        s.configure("Sub.TLabel", foreground=MUTED, font=(FONT, 10))
        s.configure("Head.TLabel", background=CARD, foreground=MUTED, font=(FONT, 9))
        s.configure(
            "TButton", background=CARD, foreground=TEXT, bordercolor=BORDER,
            lightcolor=CARD, darkcolor=CARD, focuscolor=CARD, padding=(12, 6), relief="flat",
        )
        s.map("TButton", background=[("active", "#EEF1F5"), ("disabled", "#F0F1F3")],
              foreground=[("disabled", "#A0A5AD")])
        s.configure(
            "Accent.TButton", background=ACCENT, foreground="#FFFFFF", bordercolor=ACCENT,
            lightcolor=ACCENT, darkcolor=ACCENT, focuscolor=ACCENT, padding=(16, 8),
            font=(FONT, 10, "bold"),
        )
        s.map("Accent.TButton", background=[("active", ACCENT_HOVER), ("disabled", "#A9C0F5")],
              foreground=[("disabled", "#FFFFFF")])
        s.configure("Big.Accent.TButton", padding=(28, 12), font=(FONT, 11, "bold"))
        for w in ("TCheckbutton", "TRadiobutton"):
            s.configure(w, background=CARD, foreground=TEXT, focuscolor=CARD)
            s.map(w, background=[("active", CARD)])
        s.configure(
            "Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
            rowheight=28, borderwidth=0, font=(FONT, 10),
        )
        s.configure("Treeview.Heading", background="#F0F2F5", foreground=MUTED,
                    font=(FONT, 9), relief="flat", padding=(8, 5))
        s.map("Treeview", background=[("selected", "#DCE7FD")], foreground=[("selected", TEXT)])
        s.configure("TProgressbar", troughcolor="#E8EAEE", background=ACCENT,
                    bordercolor="#E8EAEE", lightcolor=ACCENT, darkcolor=ACCENT, thickness=6)
        s.configure("TSpinbox", fieldbackground=CARD, arrowsize=12)

    # ------------------------------------------------------------ 界面
    def _build(self):
        r = self.root
        r.columnconfigure(0, weight=1)
        r.rowconfigure(2, weight=1)

        # 顶栏
        top = ttk.Frame(r)
        top.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 4))
        top.columnconfigure(0, weight=1)
        left = ttk.Frame(top)
        left.grid(row=0, column=0, sticky="w")
        ttk.Label(left, text=V.APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(left, text=f"  v{V.VERSION}", style="Sub.TLabel").pack(side="left", pady=(6, 0))
        right = ttk.Frame(top)
        right.grid(row=0, column=1, sticky="e")
        ttk.Button(right, text="关于", command=self.about).pack(side="left", padx=(0, 8))
        ttk.Button(right, text="📋 复制诊断报告", style="Accent.TButton",
                   command=self.copy_report).pack(side="left")

        # 工具栏
        bar = ttk.Frame(r)
        bar.grid(row=1, column=0, sticky="ew", padx=24, pady=(8, 8))
        self.btns = []
        for text, cmd in (("添加文件", self.add_files), ("添加文件夹", self.add_folder),
                          ("移除选中", self.remove_selected), ("清空", self.clear),
                          ("↑ 上移", lambda: self.move(-1)), ("↓ 下移", lambda: self.move(1))):
            b = ttk.Button(bar, text=text, command=cmd)
            b.pack(side="left", padx=(0, 8))
            self.btns.append(b)

        # 文件列表
        box = tk.Frame(r, bg=BORDER, bd=0)
        box.grid(row=2, column=0, sticky="nsew", padx=24)
        inner = tk.Frame(box, bg=CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        inner.columnconfigure(0, weight=1)
        inner.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(inner, columns=("no", "name", "size"), show="headings",
                                 selectmode="extended")
        self.tree.heading("no", text="#")
        self.tree.heading("name", text="文件（将成为一级标题）", anchor="w")
        self.tree.heading("size", text="大小", anchor="e")
        self.tree.column("no", width=50, anchor="center", stretch=False)
        self.tree.column("name", width=560, anchor="w")
        self.tree.column("size", width=90, anchor="e", stretch=False)
        sb = ttk.Scrollbar(inner, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.hint = tk.Label(inner, text="把 txt 文件或文件夹拖到这里\n或点上方“添加文件夹”",
                             bg=CARD, fg="#9AA1AB", font=(FONT, 12), justify="center")
        self.hint.place(relx=0.5, rely=0.5, anchor="center")
        if self.dnd_ok:
            for w in (self.tree, self.hint, inner):
                try:
                    w.drop_target_register(DND_FILES)
                    w.dnd_bind("<<Drop>>", self._on_drop)
                except Exception:
                    log.warning("拖入注册失败：%s", traceback.format_exc().splitlines()[-1])
                    self.dnd_ok = False
                    break
        else:
            self.hint.config(text="点上方“添加文件夹”或“添加文件”选择 txt\n（拖入功能当前不可用）")

        # 选项
        opt = tk.Frame(r, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        opt.grid(row=3, column=0, sticky="ew", padx=24, pady=(10, 0))
        opt.columnconfigure(1, weight=1)

        def row(i, label):
            tk.Label(opt, text=label, bg=CARD, fg=MUTED, font=(FONT, 9), width=7,
                     anchor="w").grid(row=i, column=0, padx=(14, 4), pady=5, sticky="w")
            f = tk.Frame(opt, bg=CARD)
            f.grid(row=i, column=1, sticky="w", pady=3)
            return f

        f = row(0, "排序")
        for t, v in (("按文件名（1、2、10 顺序正确）", "name"), ("按修改时间", "mtime"), ("手动（用 ↑↓ 调整）", "manual")):
            ttk.Radiobutton(f, text=t, value=v, variable=self.v_sort,
                            command=self.resort).pack(side="left", padx=(0, 14))
        f = row(1, "标题")
        for t, v in (("用文件名", "filename"), ("用文件第一行", "firstline")):
            ttk.Radiobutton(f, text=t, value=v, variable=self.v_title,
                            command=self.refresh).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(f, text="标题带上子文件夹名", variable=self.v_dir,
                        command=self.refresh).pack(side="left")
        f = row(2, "整理")
        for t, v in (("去掉多余空行（瘦身）", self.v_squeeze), ("去掉内容重复的文件", self.v_dedupe),
                     ("保护标题结构（推荐）", self.v_escape), ("生成目录", self.v_toc)):
            ttk.Checkbutton(f, text=t, variable=v).pack(side="left", padx=(0, 14))
        f = row(3, "拆分")
        ttk.Checkbutton(f, text="合并后超过", variable=self.v_split).pack(side="left")
        ttk.Spinbox(f, from_=0.1, to=100, increment=0.5, width=5, textvariable=self.v_split_mb,
                    format="%.1f").pack(side="left", padx=4)
        tk.Label(f, text="MB 就自动拆成多个文件（不会把一个 txt 劈开）", bg=CARD, fg=TEXT,
                 font=(FONT, 10)).pack(side="left")

        # 底部：汇总 + 开始
        bottom = ttk.Frame(r)
        bottom.grid(row=4, column=0, sticky="ew", padx=24, pady=(12, 0))
        bottom.columnconfigure(0, weight=1)
        self.lbl_sum = ttk.Label(bottom, text="还没有添加文件", style="Sub.TLabel")
        self.lbl_sum.grid(row=0, column=0, sticky="w")
        self.btn_go = ttk.Button(bottom, text="开始合并", style="Big.Accent.TButton",
                                 command=self.start_merge)
        self.btn_go.grid(row=0, column=1, rowspan=2, sticky="e")
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.grid(row=1, column=0, sticky="ew", padx=(0, 20), pady=(6, 0))
        self.lbl_status = ttk.Label(r, text="", style="Muted.TLabel")
        self.lbl_status.grid(row=5, column=0, sticky="w", padx=24, pady=(4, 0))
        ttk.Label(r, text=V.NOTICE, style="Muted.TLabel").grid(
            row=6, column=0, sticky="w", padx=24, pady=(2, 12))

    # ------------------------------------------------------------ 状态
    def _opts(self) -> core.Options:
        try:
            mb = float(self.v_split_mb.get())
        except ValueError:
            mb = 1.0
        return core.Options(
            title_mode=self.v_title.get(), include_dir=self.v_dir.get(),
            squeeze=self.v_squeeze.get(), dedupe=self.v_dedupe.get(),
            escape=self.v_escape.get(), toc=self.v_toc.get(),
            split_kb=int(max(mb, 0.1) * 1024) if self.v_split.get() else 0,
        )

    def _state(self):
        o = self._opts()
        return {
            "文件数": len(self.items),
            "总大小": human_size(sum(i.size for i in self.items)),
            "排序方式": self.v_sort.get(),
            "标题来源": o.title_mode,
            "标题带子文件夹": o.include_dir,
            "去空行/去重/转义/目录": f"{o.squeeze}/{o.dedupe}/{o.escape}/{o.toc}",
            "拆分上限KB": o.split_kb,
            "拖入可用": self.dnd_ok,
            "正在合并": self.busy,
            "上次输出文件名": os.path.basename(self.last_output) if self.last_output else "无",
        }

    def _display_name(self, it: core.Item) -> str:
        if self.v_title.get() == "firstline":
            return (it.rel_dir + "/" if it.rel_dir else "") + it.name + ".txt"
        base = it.name
        if self.v_dir.get() and it.rel_dir:
            base = it.rel_dir.replace("/", " / ") + " / " + base
        return base

    def refresh(self, keep=()):
        self.tree.delete(*self.tree.get_children())
        for n, it in enumerate(self.items):
            self.tree.insert("", "end", iid=str(n),
                             values=(n + 1, self._display_name(it), human_size(it.size)))
        for n in keep:
            if self.tree.exists(str(n)):
                self.tree.selection_add(str(n))
        if self.items:
            self.hint.place_forget()
            self.lbl_sum.config(
                text=f"共 {len(self.items)} 个文件，合计 {human_size(sum(i.size for i in self.items))}")
        else:
            self.hint.place(relx=0.5, rely=0.5, anchor="center")
            self.lbl_sum.config(text="还没有添加文件")

    # ------------------------------------------------------------ 操作
    def _guard(self):
        if self.busy:
            self.lbl_status.config(text="正在合并，请稍等…")
            return False
        return True

    def add_paths(self, paths):
        new, skipped = core.collect(paths, existing=[i.path for i in self.items])
        self.items.extend(new)
        if self.v_sort.get() != "manual":
            core.sort_items(self.items, self.v_sort.get())
        self.refresh()
        msg = f"已添加 {len(new)} 个文件"
        if skipped:
            msg += f"，跳过 {len(skipped)} 个（{skipped[0][1]}…）" if len(skipped) > 1 else f"，跳过 1 个（{skipped[0][1]}）"
        self.lbl_status.config(text=msg)
        log.info(msg)

    def add_files(self):
        if not self._guard():
            return
        D.action("点击：添加文件")
        paths = filedialog.askopenfilenames(
            title="选择 txt 文件", filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if paths:
            self.add_paths(paths)

    def add_folder(self):
        if not self._guard():
            return
        D.action("点击：添加文件夹")
        d = filedialog.askdirectory(title="选择放 txt 的文件夹（含子文件夹）")
        if d:
            self.add_paths([d])

    def _on_drop(self, event):
        if not self._guard():
            return
        try:
            paths = self.root.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        D.action(f"拖入 {len(paths)} 项")
        self.add_paths(paths)

    def remove_selected(self):
        if not self._guard():
            return
        sel = sorted((int(i) for i in self.tree.selection()), reverse=True)
        D.action(f"点击：移除选中（{len(sel)} 个）")
        for i in sel:
            del self.items[i]
        self.refresh()

    def clear(self):
        if not self._guard():
            return
        D.action("点击：清空")
        self.items.clear()
        self.refresh()
        self.lbl_status.config(text="")

    def move(self, direction):
        if not self._guard():
            return
        sel = sorted(int(i) for i in self.tree.selection())
        if not sel:
            self.lbl_status.config(text="先在列表里选中要移动的文件")
            return
        D.action(f"点击：{'上移' if direction < 0 else '下移'}（{len(sel)} 个）")
        order = sel if direction < 0 else list(reversed(sel))
        new_sel = set()  # 已处理的选中项最终所在位置
        for i in order:
            j = i + direction
            if 0 <= j < len(self.items) and j not in new_sel:
                self.items[i], self.items[j] = self.items[j], self.items[i]
                new_sel.add(j)
            else:
                new_sel.add(i)
        self.v_sort.set("manual")
        self.refresh(keep=new_sel)

    def resort(self):
        if not self._guard():
            return
        D.action(f"选择排序：{self.v_sort.get()}")
        if self.v_sort.get() != "manual":
            core.sort_items(self.items, self.v_sort.get())
        self.refresh()

    # ------------------------------------------------------------ 合并
    def start_merge(self):
        if self.busy:
            return
        D.action("点击：开始合并")
        if not self.items:
            messagebox.showinfo(V.APP_NAME, "请先添加 txt 文件或文件夹。")
            return
        init_dir = os.path.dirname(self.items[0].path)
        out = filedialog.asksaveasfilename(
            title="保存为", defaultextension=".md", initialdir=init_dir,
            initialfile="合并文档.md", filetypes=[("Markdown 文档", "*.md")])
        if not out:
            D.action("取消保存")
            return
        opts = self._opts()
        items = list(self.items)
        self._set_busy(True)
        self.progress.config(maximum=max(len(items), 1), value=0)
        log.info("开始合并 %d 个文件 → %s", len(items), os.path.basename(out))

        def work():
            try:
                res = core.merge(items, opts, out, lambda i, n: self.q.put(("p", i, n)))
                self.q.put(("done", res))
            except Exception:
                self.q.put(("err", sys.exc_info()))

        threading.Thread(target=work, name="merge", daemon=True).start()
        self.root.after(80, self._poll)

    def _poll(self):
        try:
            while True:
                m = self.q.get_nowait()
                if m[0] == "p":
                    self.progress.config(value=m[1])
                    self.lbl_status.config(text=f"正在读取 {m[1]} / {m[2]}")
                elif m[0] == "done":
                    self._finish(m[1])
                    return
                else:
                    self._fail(m[1])
                    return
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _set_busy(self, b):
        self.busy = b
        st = "disabled" if b else "normal"
        self.btn_go.config(state=st)
        for x in self.btns:
            x.config(state=st)

    def _finish(self, res: core.Result):
        self._set_busy(False)
        self.progress.config(value=self.progress["maximum"])
        self.last_output = res.out_paths[0]
        pct = ""
        if res.chars_before:
            pct = f"（{'少了' if res.chars_after <= res.chars_before else '多了'}" \
                  f" {abs(1 - res.chars_after / res.chars_before) * 100:.0f}%）"
        lines = [
            f"已合并 {res.n_files} 个文件 → {len(res.out_paths)} 个 Markdown 文件",
            f"文字量：{res.chars_before:,} → {res.chars_after:,} 字{pct}",
            f"总大小：{human_size(res.bytes_total)}",
        ]
        if res.skipped:
            lines.append(f"\n跳过 {len(res.skipped)} 个：")
            for p, why in res.skipped[:5]:
                lines.append(f"  · {os.path.basename(p)}：{why}")
            if len(res.skipped) > 5:
                lines.append(f"  · …另有 {len(res.skipped) - 5} 个")
        if len(res.out_paths) > 1:
            lines.append("\n生成的文件：\n" + "\n".join("  " + os.path.basename(p) for p in res.out_paths))
        log.info("合并完成：%d 个文件，%d 字节，跳过 %d", res.n_files, res.bytes_total, len(res.skipped))
        self.lbl_status.config(text=f"完成：{os.path.basename(res.out_paths[0])}")
        if messagebox.askyesno(V.APP_NAME, "\n".join(lines) + "\n\n要打开文件所在的文件夹吗？"):
            self._reveal(res.out_paths[0])

    def _fail(self, exc_info):
        self._set_busy(False)
        D.record_error("合并", *exc_info)
        self.lbl_status.config(text="合并失败")
        if isinstance(exc_info[1], ValueError):
            messagebox.showwarning(V.APP_NAME, str(exc_info[1]))
        else:
            self._crash_dialog(auto=True)

    def _reveal(self, path):
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except Exception:
            log.warning("无法打开文件夹", exc_info=True)

    # ------------------------------------------------------------ 诊断
    def _copy(self, text: str) -> bool:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
            return self.root.clipboard_get() == text
        except Exception:
            return False

    def copy_report(self, quiet=False):
        D.action("点击：复制诊断报告")
        text = D.build_report()
        saved = D.save_report(text)
        if self._copy(text):
            log.info("诊断报告已复制到剪贴板")
            if not quiet:
                messagebox.showinfo(V.APP_NAME, "诊断报告已复制。\n回到 Claude 对话框，直接粘贴（Ctrl+V）发送即可。")
            return True
        self._report_window(text, saved)
        return False

    def _report_window(self, text, saved):
        """剪贴板失败时的降级：弹出可全选的文本框，并已保存为文件。"""
        win = tk.Toplevel(self.root)
        win.title("诊断报告（请手动复制）")
        win.geometry("700x520")
        tip = "自动复制没有成功。请点下方文本框，按 Ctrl+A 全选，再 Ctrl+C 复制。"
        if saved:
            tip += f"\n同时已保存为文件：{saved}"
        tk.Label(win, text=tip, justify="left", anchor="w", wraplength=660).pack(fill="x", padx=12, pady=8)
        t = tk.Text(win, wrap="word")
        t.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        t.insert("1.0", text)
        t.focus_set()
        t.tag_add("sel", "1.0", "end")

    def _crash_dialog(self, auto=False):
        if self._crashing:
            return
        self._crashing = True
        try:
            text = D.build_report()
            saved = D.save_report(text)
            ok = self._copy(text)
            msg = "程序遇到了一个问题。\n"
            if ok:
                msg += "诊断报告已自动复制，请回到 Claude 对话框直接粘贴（Ctrl+V）发送。"
            elif saved:
                msg += f"诊断报告已保存为文件，请把它发给 Claude：\n{saved}"
            else:
                msg += "请点右上角“复制诊断报告”，粘贴给 Claude。"
            messagebox.showerror(V.APP_NAME, msg)
        except Exception:
            pass
        finally:
            self._crashing = False

    def _tk_exception(self, exc_type, exc, tb):
        D.record_error("界面回调", exc_type, exc, tb)
        self._crash_dialog()

    def about(self):
        D.action("点击：关于")
        messagebox.showinfo(
            "关于",
            f"{V.APP_NAME}  v{V.VERSION}\n\n{V.NOTICE}\n\n协议：{V.LICENSE_NAME}\n"
            f"日志位置：{D.log_dir or '（无法写入）'}")

    def run(self):
        self.root.mainloop()
        log.info("程序退出")


def main():
    try:
        App().run()
    except Exception:
        D.record_error("启动", *sys.exc_info())
        text = D.build_report()
        path = D.save_report(text)
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror(V.APP_NAME, f"程序启动失败。诊断报告已保存：\n{path}\n请把它发给 Claude。")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
