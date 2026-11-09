"""准备设备端 scrcpy-server.jar。

本地 ``resources/scrcpy/scrcpy-server.jar`` 存在且校验通过时直接使用。
文件缺失，或内容不是官方 v5.0.1 时，从该版本的 GitHub Release 下载：

https://github.com/Genymobile/scrcpy/releases/tag/v5.0.1
https://github.com/Genymobile/scrcpy/releases/download/v5.0.1/scrcpy-server-v5.0.1

校验值来自同一 Release 的 SHA256SUMS.txt（已固定在下面，下载后再核对一次）。
服务端启动参数里的版本号必须是 ``5.0.1``，否则设备端会拒绝连接。
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

from config import config

log = logging.getLogger(__name__)

PINNED_VERSION = "5.0.1"
RELEASE_TAG = "v5.0.1"
ASSET_NAME = "scrcpy-server-v5.0.1"
RELEASE_PAGE = f"https://github.com/Genymobile/scrcpy/releases/tag/{RELEASE_TAG}"
DOWNLOAD_URL = (
    f"https://github.com/Genymobile/scrcpy/releases/download/{RELEASE_TAG}/{ASSET_NAME}"
)
SUMS_URL = (
    f"https://github.com/Genymobile/scrcpy/releases/download/{RELEASE_TAG}/SHA256SUMS.txt"
)
# SHA256SUMS.txt @ scrcpy v5.0.1, asset scrcpy-server-v5.0.1.
PINNED_SHA256 = "764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536"

_prepare_lock = threading.Lock()


def parse_sha256_sums(text: str, filename: str) -> str | None:
    """从官方 SHA256SUMS.txt 取出某个文件名的摘要。"""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        digest, listed = parts[0], parts[-1].lstrip("*")
        if listed == filename or listed.endswith("/" + filename):
            return digest.lower()
    return None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def server_path() -> Path:
    return Path(config.SCRCPY_SERVER_PATH)


def _uses_pinned_release() -> bool:
    return str(config.SCRCPY_SERVER_VERSION) == PINNED_VERSION


def _is_pinned_jar(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        return file_sha256(path) == PINNED_SHA256
    except OSError:
        return False


def ensure_scrcpy_server(path: Path | None = None) -> Path:
    """返回可用的 scrcpy-server.jar。默认路径缺失或不是 v5.0.1 时下载。

    若通过 ``SCRCPY_SERVER_VERSION`` 换成了别的构建，只使用调用方放好的文件，
    不会用 v5.0.1 覆盖它。
    """
    dest = Path(path) if path is not None else server_path()
    with _prepare_lock:
        if _uses_pinned_release():
            if _is_pinned_jar(dest):
                return dest
            log.info("本地没有可用的 scrcpy-server %s，开始下载 %s", PINNED_VERSION, dest)
            _download_release(dest)
            if not _is_pinned_jar(dest):
                raise RuntimeError(f"已下载 {ASSET_NAME}，但校验未通过: {dest}")
            return dest

        if dest.is_file():
            log.info("使用自定义 scrcpy-server: %s (version=%s)", dest, config.SCRCPY_SERVER_VERSION)
            return dest
        raise RuntimeError(
            f"缺少 scrcpy-server.jar: {dest}。"
            f"当前版本号是 {config.SCRCPY_SERVER_VERSION}，自动下载只提供 {PINNED_VERSION}。"
            f"见 {RELEASE_PAGE}"
        )


def schedule_prepare() -> threading.Thread | None:
    """启动时准备 jar。已是 v5.0.1 则不下载，否则在后台拉取。"""
    dest = server_path()
    if not _uses_pinned_release():
        if dest.is_file():
            log.info("自定义 scrcpy-server 已存在，启动时跳过下载")
        else:
            log.warning("自定义 scrcpy-server 不存在，启动时不下载: %s", dest)
        return None
    if _is_pinned_jar(dest):
        log.info("项目内已有 scrcpy-server %s，启动时跳过下载", PINNED_VERSION)
        return None

    log.info("项目内没有 scrcpy-server %s，启动时开始下载", PINNED_VERSION)

    def _run() -> None:
        try:
            ensure_scrcpy_server(dest)
        except (OSError, RuntimeError, urllib.error.URLError, TimeoutError) as exc:
            log.exception("启动时下载 scrcpy-server 失败，推送或开流时会再试一次: %s", exc)

    thread = threading.Thread(target=_run, name="scrcpy-server-prepare", daemon=True)
    thread.start()
    return thread


def _download_release(dest: Path) -> None:
    try:
        sums_text = _read_text(SUMS_URL)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"无法下载 SHA256SUMS.txt: {exc}") from exc

    expected = parse_sha256_sums(sums_text, ASSET_NAME)
    if expected != PINNED_SHA256:
        raise RuntimeError(
            f"SHA256SUMS.txt 中的 {ASSET_NAME} 与内置校验值不一致"
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="scrcpy-server-", suffix=".part", dir=str(dest.parent))
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        log.info("正在下载 %s", DOWNLOAD_URL)
        _download_file(DOWNLOAD_URL, temp_path)
        actual = file_sha256(temp_path)
        if actual != expected:
            raise RuntimeError(f"{ASSET_NAME} 校验失败")
        os.replace(temp_path, dest)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    log.info("scrcpy-server %s 已就绪: %s", PINNED_VERSION, dest)


def _read_text(url: str) -> str:
    with _open(url) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset)


def _download_file(url: str, dest: Path) -> None:
    with _open(url) as response, dest.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)


def _open(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "WebAppFlaskscrcpy"})
    return urllib.request.urlopen(request, timeout=120)
