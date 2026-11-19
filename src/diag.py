"""诊断层：日志（内存环形缓冲 + 文件轮转）、全局错误捕获、脱敏、诊断报告。
本模块自身的所有逻辑都用 try/except 保护，绝不能因为日志问题让程序出错。"""

import collections
import datetime
import faulthandler
import logging
import logging.handlers
import os
import platform
import re
import sys
import threading
import traceback

import version as V

MASKED_FIELDS = "邮箱、手机号、API key / token / 密码 / cookie 字样的值、本机用户目录（显示为 ~）；不记录任何 txt 文件的正文内容"

_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[邮箱]"),
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[手机号]"),
    (
        re.compile(
            r"(?i)(api[_-]?key|token|password|passwd|secret|cookie|authorization)(\s*[=:：]\s*)\S+"
        ),
        r"\1\2[已屏蔽]",
    ),
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,})"), "[密钥]"),
]

try:
    _HOME = os.path.expanduser("~")
except Exception:
    _HOME = ""


def mask(text) -> str:
    try:
        s = str(text)
        if _HOME and len(_HOME) > 3:
            s = re.sub(re.escape(_HOME), "~", s, flags=re.IGNORECASE)
        for pat, rep in _PATTERNS:
            s = pat.sub(rep, s)
        return s
    except Exception:
        return "[脱敏失败，内容已省略]"


class _RingHandler(logging.Handler):
    def __init__(self, capacity=200):
        super().__init__()
        self.buf = collections.deque(maxlen=capacity)

    def emit(self, record):
        try:
            self.buf.append(mask(self.format(record)))
        except Exception:
            pass


def _app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _pick_dir() -> str:
    """优先放在程序同目录；不可写时退到 %LOCALAPPDATA% 或临时目录。"""
    cands = [
        _app_dir(),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), V.APP_ID) if os.environ.get("LOCALAPPDATA") else "",
        os.path.join(os.path.expanduser("~"), "." + V.APP_ID),
    ]
    import tempfile

    cands.append(os.path.join(tempfile.gettempdir(), V.APP_ID))
    for d in cands:
        if not d:
            continue
        try:
            os.makedirs(d, exist_ok=True)
            t = os.path.join(d, ".w_test")
            with open(t, "w") as f:
                f.write("1")
            os.remove(t)
            return d
        except Exception:
            continue
    return ""


class Diagnostics:
    def __init__(self):
        self.ring = _RingHandler(200)
        self.actions = collections.deque(maxlen=50)
        self.errors = collections.deque(maxlen=10)
        self.state_provider = lambda: {}
        self.log_dir = ""
        self.log_file = ""
        self.report_file = ""
        self._crash_fh = None
        self._on_crash = None
        try:
            fmt = logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"
            )
            self.ring.setFormatter(fmt)
            root = logging.getLogger()
            root.setLevel(logging.INFO)
            root.addHandler(self.ring)
            self.log_dir = _pick_dir()
            if self.log_dir:
                self.log_file = os.path.join(self.log_dir, V.APP_ID + ".log")
                self.report_file = os.path.join(self.log_dir, "diagnostic_report.txt")
                fh = logging.handlers.RotatingFileHandler(
                    self.log_file, maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
                )
                fh.setFormatter(
                    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
                )
                fh.addFilter(lambda r: True)
                root.addHandler(_MaskingWrapper(fh))
                try:
                    self._crash_fh = open(
                        os.path.join(self.log_dir, "crash_trace.txt"), "w", encoding="utf-8"
                    )
                    faulthandler.enable(self._crash_fh)
                except Exception:
                    self._crash_fh = None
        except Exception:
            pass

    # ------------------------------------------------------------ 记录
    def action(self, text: str):
        """记录用户的一次关键操作。"""
        try:
            line = f"{datetime.datetime.now():%H:%M:%S} {mask(text)}"
            self.actions.append(line)
            logging.getLogger("操作").info(text)
        except Exception:
            pass

    def record_error(self, where: str, exc_type, exc, tb):
        try:
            stack = "".join(traceback.format_exception(exc_type, exc, tb))
            self.errors.append(
                f"[{datetime.datetime.now():%H:%M:%S}] 位置：{where}\n{mask(stack)}"
            )
            logging.getLogger("错误").error("%s：%s: %s", where, exc_type.__name__, exc)
        except Exception:
            pass

    # ------------------------------------------------------------ 全局捕获
    def install_hooks(self, on_crash=None):
        self._on_crash = on_crash

        def _excepthook(t, e, tb):
            self.record_error("主线程未捕获异常", t, e, tb)
            self._crash()

        def _thread_hook(args):
            self.record_error(
                f"线程 {getattr(args.thread, 'name', '?')} 未捕获异常",
                args.exc_type,
                args.exc_value,
                args.exc_traceback,
            )
            self._crash()

        try:
            sys.excepthook = _excepthook
            threading.excepthook = _thread_hook
        except Exception:
            pass

    def _crash(self):
        try:
            if self._on_crash:
                self._on_crash()
        except Exception:
            pass

    # ------------------------------------------------------------ 报告
    def build_report(self) -> str:
        try:
            return self._build_report()
        except Exception as e:  # 连报告本身出错也要给出点什么
            return f"（生成诊断报告时出错：{e!r}）\n版本：v{V.VERSION}"

    def _build_report(self) -> str:
        try:
            import tkinter

            tkv = str(tkinter.TkVersion)
        except Exception:
            tkv = "不可用"
        try:
            import tkinterdnd2  # noqa: F401

            dnd = "已加载"
        except Exception:
            dnd = "未加载（拖入功能不可用，按钮可用）"
        try:
            state = self.state_provider() or {}
        except Exception as e:
            state = {"获取状态失败": repr(e)}
        state_txt = "\n".join(f"- {k}：{mask(v)}" for k, v in state.items()) or "- （无）"
        actions = "\n".join(self.actions) or "（无）"
        errors = "\n\n".join(self.errors) or "（无）"
        logs = "\n".join(self.ring.buf) or "（无）"
        crash = ""
        try:
            if self.log_dir:
                cp = os.path.join(self.log_dir, "crash_trace.txt")
                if os.path.exists(cp) and os.path.getsize(cp) > 0:
                    with open(cp, encoding="utf-8", errors="replace") as f:
                        crash = "\n## 程序崩溃痕迹\n```\n" + mask(f.read()[-3000:]) + "\n```\n"
        except Exception:
            pass
        return (
            f"# {V.APP_NAME} 诊断报告\n\n"
            f"- 软件版本：v{V.VERSION}\n"
            f"- 生成时间：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"- 已脱敏字段：{MASKED_FIELDS}\n\n"
            f"## 运行环境\n"
            f"- 系统：{platform.platform()}\n"
            f"- Python：{platform.python_version()}（{'打包版' if getattr(sys, 'frozen', False) else '源码运行'}）\n"
            f"- Tk 版本：{tkv}\n"
            f"- 拖入组件：{dnd}\n"
            f"- 日志文件：{mask(self.log_file) or '无法写入'}\n\n"
            f"## 当前状态与设置\n{state_txt}\n\n"
            f"## 最近用户操作（最多 50 条）\n```\n{actions}\n```\n\n"
            f"## 最近错误及堆栈\n```\n{errors}\n```\n"
            f"{crash}\n"
            f"## 最近日志（最多 200 条）\n```\n{logs}\n```\n"
        )

    def save_report(self, text: str) -> str:
        """保存为 diagnostic_report.txt，返回路径（失败返回空串）。"""
        try:
            if not self.report_file:
                return ""
            with open(self.report_file, "w", encoding="utf-8") as f:
                f.write(text)
            return self.report_file
        except Exception:
            return ""


class _MaskingWrapper(logging.Handler):
    """写文件前也做脱敏。"""

    def __init__(self, inner):
        super().__init__()
        self.inner = inner

    def emit(self, record):
        try:
            record.msg = mask(record.getMessage())
            record.args = ()
            self.inner.emit(record)
        except Exception:
            pass
