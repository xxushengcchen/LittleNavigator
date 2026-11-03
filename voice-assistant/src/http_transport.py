"""受限的外部 HTTP 取数：不跟随重定向、限字节数、按时返回。

为什么不用裸 ``urllib.request.urlopen``：
- 默认 opener **会跟随 30x**，而且跳转后仍带着 ``Authorization`` /
  ``X-Api-Key`` 这类头。服务商或中间人回一个 302 到自己的域名，用户的
  密钥就被送到那边去了（审计 S02）。
- ``timeout=`` 只约束单次 socket 操作，不管请求总时长；也不限制响应体
  大小。一个慢速大响应能把一次问答拖死（S04/A13/A14）。

所以这里只做三件事：**拒跳转、限字节、按墙钟返回**。
"""
from __future__ import annotations

import threading
import urllib.error
import urllib.parse
import urllib.request

# 响应体上限：JSON 1 MiB、音频 8 MiB、错误体 4 KiB。
DEFAULT_JSON_LIMIT = 1 << 20
DEFAULT_AUDIO_LIMIT = 8 << 20
DEFAULT_ERROR_LIMIT = 4096

# 真正卡住（已经超时返回、但线程还没退）的请求最多允许 2 个。
# 标准库没法强杀阻塞中的 DNS/read，只能限制同时卡住的数量，让
# 后续请求有界失败而不是无限建线程。
_MAX_STRANDED = 2
_stranded_lock = threading.Lock()
_stranded = [0]


class Cancelled(RuntimeError):
    """调用方主动取消。不算网络失败，不计熔断。"""


class HttpFailure(OSError):
    """HTTP 层失败。message 已脱敏，可直接给用户看。"""

    def __init__(self, status: int | None, message: str):
        super().__init__(message)
        self.status = status
        self.message = message

    def __str__(self) -> str:      # pragma: no cover - 只影响显示
        return self.message


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """所有 30x 都不跟随——不给凭据离开原地址的机会。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _stranded_count() -> int:
    with _stranded_lock:
        return _stranded[0]


def _take_slot() -> bool:
    with _stranded_lock:
        if _stranded[0] >= _MAX_STRANDED:
            return False
        _stranded[0] += 1
        return True


def _drop_slot() -> None:
    with _stranded_lock:
        if _stranded[0] > 0:
            _stranded[0] -= 1


def _status_reason(status: int) -> str:
    if 300 <= status < 400:
        return "服务端要求跳转到别的地址；为避免把密钥发给第三方，这里不跟随跳转，请直接填写最终接口地址"
    if status == 401:
        return "鉴权失败：检查密钥是否有效"
    if status == 403:
        return "服务端拒绝访问：检查密钥权限或所在地区"
    if status == 404:
        return "接口地址不存在：检查 base_url 是否写全（通常以 /v1 结尾）"
    if status == 429:
        return "请求太频繁或额度用尽，稍后再试"
    if status >= 500:
        return f"服务端错误（HTTP {status}），稍后再试"
    return f"请求失败（HTTP {status}）"


def fetch_bytes(method: str, url: str, headers: dict | None = None,
                body: bytes | None = None, *, deadline_s: float = 8.0,
                max_bytes: int = DEFAULT_JSON_LIMIT,
                error_max_bytes: int = DEFAULT_ERROR_LIMIT,
                cancelled=None) -> bytes:
    """取回响应体字节；超时/超限/跳转都抛 HttpFailure(或 TimeoutError)。

    ``deadline_s`` 是**整次请求**的墙上时间预算，不是单次 socket 超时。
    """
    if method not in ("GET", "POST"):
        raise ValueError(f"不支持的方法：{method}")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HttpFailure(None, "接口地址必须以 http:// 或 https:// 开头")
    if parsed.username or parsed.password:
        raise HttpFailure(None, "接口地址里不能带账号密码")
    if not parsed.hostname:
        raise HttpFailure(None, "接口地址缺少主机名")

    if deadline_s is None or deadline_s <= 0:
        raise HttpFailure(None, "请求超时预算无效")
    if not _take_slot():
        raise HttpFailure(None, "有网络请求卡住还没返回，请稍后再试")

    box: dict = {}

    def work() -> None:
        try:
            req = urllib.request.Request(url, data=body, method=method)
            for key, value in (headers or {}).items():
                req.add_header(key, value)
            try:
                with _OPENER.open(req, timeout=deadline_s) as resp:
                    box["data"] = _read_capped(resp, max_bytes, cancelled)
            except urllib.error.HTTPError as exc:
                status = int(getattr(exc, "code", 0) or 0)
                try:
                    exc.read(error_max_bytes)
                except Exception:      # noqa: BLE001
                    pass
                raise HttpFailure(status, _status_reason(status)) from None
        except BaseException as exc:   # noqa: BLE001
            box["error"] = exc
        finally:
            _drop_slot()

    worker = threading.Thread(target=work, name="http-fetch", daemon=True)
    worker.start()
    worker.join(deadline_s)
    if worker.is_alive():
        # 线程还活着：不 pretend 成功，也不无限等。它自己退出时会归还槽位。
        raise TimeoutError(f"网络请求超过 {deadline_s:.1f} 秒没有返回")
    error = box.get("error")
    if error is not None:
        raise error
    return box.get("data", b"")


def _read_capped(resp, max_bytes: int, cancelled=None) -> bytes:  # noqa: ANN001
    chunks: list[bytes] = []
    total = 0
    declared = resp.headers.get("Content-Length") if resp.headers else None
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise HttpFailure(None, f"响应体过大（{declared} 字节），已拒绝")
    while True:
        if cancelled is not None and cancelled():
            raise Cancelled("请求已取消")
        chunk = resp.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HttpFailure(None, "响应体超过上限，已拒绝")
        chunks.append(chunk)
    return b"".join(chunks)
