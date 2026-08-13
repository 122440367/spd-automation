#!/usr/bin/env python3
"""Upload today's CSV files to the SPD Worker."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin
from urllib.request import Request, urlopen


URL = os.getenv("SPD_URL", "").strip()
CSV_DIR = Path(os.getenv("SPD_CSV_DIR", "/root/ASNIPtest"))
API_TOKEN = os.getenv("SPD_API_TOKEN", "").strip()
UPLOAD_TIMEOUT_SECONDS = int(os.getenv("SPD_UPLOAD_TIMEOUT_SECONDS", "300"))
MAX_PENDING_IPS = int(os.getenv("SPD_MAX_PENDING_IPS", "800"))
STEP_DELAY_SECONDS = int(os.getenv("SPD_STEP_DELAY_SECONDS", "2"))
FILE_STABLE_SECONDS = int(os.getenv("SPD_FILE_STABLE_SECONDS", "20"))
FILE_STABLE_TIMEOUT_SECONDS = int(os.getenv("SPD_FILE_STABLE_TIMEOUT_SECONDS", "60"))
STATE_DIR = Path("/var/lib/spd-automation")
TELEGRAM_BOT_TOKEN = os.getenv("bot_token", "").strip()
TELEGRAM_CHAT_ID = os.getenv("chat_id", "").strip()
JOB_LOCK_PATH = STATE_DIR / "job.lock"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOG = logging.getLogger("spd-automation")


def compact_text(value: str, limit: int = 600) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def todays_csv(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise RuntimeError(f"CSV 目录不存在或不是目录: {directory}")
    today = datetime.now().date()
    files = [
        path
        for path in directory.iterdir()
        if path.is_file()
        and path.suffix.lower() == ".csv"
        and datetime.fromtimestamp(path.stat().st_mtime).date() == today
    ]
    if not files:
        raise RuntimeError(f"目录中没有今天的 CSV 文件: {directory}")
    return sorted(files, key=lambda path: (path.stat().st_mtime_ns, path.name))


def wait_for_stable_file(path: Path) -> None:
    if FILE_STABLE_SECONDS <= 0:
        return
    deadline = time.monotonic() + FILE_STABLE_TIMEOUT_SECONDS
    previous = None
    stable_since = time.monotonic()

    while time.monotonic() < deadline:
        stat = path.stat()
        current = (stat.st_size, stat.st_mtime_ns)
        if current != previous:
            previous = current
            stable_since = time.monotonic()
        elif time.monotonic() - stable_since >= FILE_STABLE_SECONDS:
            LOG.info("CSV 已稳定 %s 秒，大小=%s bytes", FILE_STABLE_SECONDS, stat.st_size)
            return
        time.sleep(1)

    raise RuntimeError(
        f"CSV 在 {FILE_STABLE_TIMEOUT_SECONDS} 秒内持续变化，暂不上传: {path}"
    )


def acquire_job_lock():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handle = JOB_LOCK_PATH.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError("已有自动化或清理任务正在运行")
    return handle


def api_url(path: str) -> str:
    return urljoin(URL.rstrip("/") + "/", path.lstrip("/"))


def authentication_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_TOKEN}"}


def request_json(
    path: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int,
) -> dict:
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "spd-automation/1.0",
        **authentication_headers(),
        **(headers or {}),
    }
    request = Request(
        api_url(path),
        data=body,
        headers=request_headers,
        method=method,
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if response.headers.get("cf-mitigated", "").lower() == "challenge":
                raise RuntimeError("请求被 Cloudflare Challenge 拦截")
    except HTTPError as error:
        is_challenge = error.headers.get("cf-mitigated", "").lower() == "challenge"
        try:
            raw = error.read()
        finally:
            error.close()
        if is_challenge:
            raise RuntimeError("请求被 Cloudflare Challenge 拦截") from error
        detail = compact_text(raw.decode("utf-8", errors="replace"), 800)
        raise RuntimeError(f"{method} {path} 返回 HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"{method} {path} 网络错误: {error.reason}") from error

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        preview = compact_text(raw.decode("utf-8", errors="replace"), 300)
        if "Just a moment" in preview or "challenge" in preview.lower():
            raise RuntimeError("请求疑似被 Cloudflare 人机验证拦截") from error
        raise RuntimeError(f"{method} {path} 未返回 JSON: {preview}") from error
    if not isinstance(data, dict):
        raise RuntimeError(f"{method} {path} 返回了非对象 JSON")
    return data


def require_success(data: dict, operation: str) -> dict:
    if data.get("success") is not True:
        raise RuntimeError(f"{operation}失败: {data.get('error') or data.get('message') or data}")
    return data


def multipart_file_body(csv_path: Path, *, max_pending_ips: int) -> tuple[bytes, str]:
    boundary = f"----spd-automation-{uuid.uuid4().hex}"
    content = csv_path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="ipfile"; filename="upload.csv"\r\n'
        "Content-Type: text/csv\r\n\r\n"
    ).encode("utf-8") + content + (
        f"\r\n--{boundary}\r\n"
        'Content-Disposition: form-data; name="maxIPs"\r\n\r\n'
        f"{max_pending_ips}\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    return body, boundary


def clear_uploaded_ips() -> dict:
    LOG.info("清空 Worker 待测速 IP 列表")
    return require_success(
        request_json("/clear-uploaded-ips", method="POST", body=b"", timeout=UPLOAD_TIMEOUT_SECONDS),
        "清空待测速列表",
    )


def upload_csv(csv_path: Path) -> dict:
    LOG.info("上传当天 CSV: %s", csv_path)
    body, boundary = multipart_file_body(csv_path, max_pending_ips=MAX_PENDING_IPS)
    data = require_success(
        request_json(
            "/upload-ips",
            method="POST",
            body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            timeout=UPLOAD_TIMEOUT_SECONDS,
        ),
        "上传解析",
    )
    LOG.info("已合并去重，待测速 IP 数量: %s", data.get("count", 0))
    return data


def send_telegram(message: str) -> None:
    if not TELEGRAM_BOT_TOKEN and not TELEGRAM_CHAT_ID:
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        LOG.warning("Telegram 通知未启用：bot_token 和 chat_id 必须同时配置")
        return
    try:
        token = quote(TELEGRAM_BOT_TOKEN, safe=":_-")
        payload = urlencode(
            {"chat_id": TELEGRAM_CHAT_ID, "text": message[:4000]}
        ).encode("utf-8")
        request = Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urlopen(request, timeout=20) as response:
            if response.status >= 300:
                raise RuntimeError(f"Telegram HTTP {response.status}")
        LOG.info("Telegram 执行结果已发送")
    except Exception:
        LOG.exception("Telegram 通知发送失败，但不影响主任务结果")


def main() -> int:
    started = time.monotonic()
    stage = "初始化"
    csv_paths: list[Path] = []
    upload_result = ""
    total_uploaded = 0
    job_lock = None

    try:
        if not URL:
            raise RuntimeError("必须通过 SPD_URL 环境变量配置 Worker 地址")
        if not API_TOKEN:
            raise RuntimeError("必须通过 SPD_API_TOKEN 环境变量配置 API Token")

        stage = "获取任务锁"
        job_lock = acquire_job_lock()
        stage = "选择并检查当天 CSV"
        csv_paths = todays_csv(CSV_DIR)
        for csv_path in csv_paths:
            wait_for_stable_file(csv_path)
        LOG.info("任务开始，Worker=%s", URL)

        stage = "清空待测速列表"
        clear_uploaded_ips()
        if STEP_DELAY_SECONDS > 0:
            time.sleep(STEP_DELAY_SECONDS)
        stage = "上传并解析当天 CSV"
        for index, csv_path in enumerate(csv_paths):
            upload_data = upload_csv(csv_path)
            total_uploaded = int(upload_data.get("count", 0))
            if index + 1 < len(csv_paths) and STEP_DELAY_SECONDS > 0:
                time.sleep(STEP_DELAY_SECONDS)
        upload_result = f"合并去重后已保存 {total_uploaded} 个 IP 到 Worker"
    except Exception as error:
        elapsed = round(time.monotonic() - started)
        LOG.exception("自动化任务失败，阶段=%s", stage)
        send_telegram(
            "❌ SPD 自动化执行失败\n"
            f"阶段: {stage}\n"
            f"CSV: {', '.join(path.name for path in csv_paths) if csv_paths else '未选择'}\n"
            f"错误: {compact_text(str(error))}\n"
            f"耗时: {elapsed} 秒"
        )
        if job_lock is not None:
            job_lock.close()
        return 1

    elapsed = round(time.monotonic() - started)
    LOG.info("全部步骤成功完成")
    send_telegram(
        "✅ SPD 自动化执行成功\n"
        f"CSV: {', '.join(path.name for path in csv_paths)}\n"
        f"上传: {upload_result}\n"
        f"耗时: {elapsed} 秒"
    )
    if job_lock is not None:
        job_lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
