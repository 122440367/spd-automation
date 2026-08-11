#!/usr/bin/env python3
"""Telegram /cleanup bot for deleting CSV files from /root/ASNIPtest."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


BOT_TOKEN = os.getenv("bot_token", "").strip()
AUTHORIZED_CHAT_ID = os.getenv("chat_id", "").strip()
CLEANUP_DIR = Path("/root/ASNIPtest")
STATE_DIR = Path("/var/lib/spd-automation")
OFFSET_FILE = STATE_DIR / "telegram_cleanup.offset"
JOB_LOCK_FILE = STATE_DIR / "job.lock"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOG = logging.getLogger("spd-telegram-cleanup")


def telegram_request(method: str, values: dict, timeout: int = 60):
    token = quote(BOT_TOKEN, safe=":_-")
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=urlencode(values).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description", "Telegram API 请求失败"))
    return payload.get("result")


def send_message(text: str) -> None:
    telegram_request(
        "sendMessage",
        {"chat_id": AUTHORIZED_CHAT_ID, "text": text[:4000]},
        timeout=20,
    )


def load_offset() -> int | None:
    try:
        return int(OFFSET_FILE.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def save_offset(offset: int) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = OFFSET_FILE.with_suffix(".tmp")
    temp_file.write_text(str(offset), encoding="utf-8")
    temp_file.replace(OFFSET_FILE)


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def asniptest_scanner_running() -> bool:
    """Best-effort guard against deleting files while ASNIPtest is producing them."""
    proc_root = Path("/proc")
    for proc_dir in proc_root.iterdir():
        if not proc_dir.name.isdigit():
            continue
        try:
            args = (proc_dir / "cmdline").read_bytes().split(b"\0")
            decoded = [arg.decode("utf-8", errors="ignore") for arg in args if arg]
            if not any(Path(arg).name == "run.py" for arg in decoded):
                continue
            cwd = Path(os.readlink(proc_dir / "cwd"))
            if cwd == CLEANUP_DIR or any(
                arg == str(CLEANUP_DIR / "run.py") for arg in decoded
            ):
                return True
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return False


def cleanup_csv_files() -> tuple[int, int, list[str]]:
    expected = Path("/root/ASNIPtest")
    if CLEANUP_DIR != expected:
        raise RuntimeError(f"清理目录安全校验失败: {CLEANUP_DIR}")
    if not CLEANUP_DIR.is_dir():
        raise RuntimeError(f"目录不存在: {CLEANUP_DIR}")
    if asniptest_scanner_running():
        raise RuntimeError("ASNIPtest 扫描任务正在运行，请完成后重新发送 /cleanup")

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with JOB_LOCK_FILE.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("自动化任务正在使用 CSV，请稍后重新发送 /cleanup")

        files = sorted(
            path
            for path in CLEANUP_DIR.iterdir()
            if path.is_file() and path.suffix.lower() == ".csv"
        )
        deleted = 0
        freed = 0
        errors: list[str] = []
        for path in files:
            try:
                size = path.stat().st_size
                path.unlink()
                deleted += 1
                freed += size
            except OSError as error:
                errors.append(f"{path.name}: {error}")
        return deleted, freed, errors


def command_name(text: str) -> str:
    first = text.strip().split(maxsplit=1)[0].lower() if text.strip() else ""
    return first.split("@", 1)[0]


def process_update(update: dict) -> None:
    message = update.get("message")
    if not isinstance(message, dict):
        return
    chat = message.get("chat", {})
    chat_id = str(chat.get("id", ""))
    sender_id = str(message.get("from", {}).get("id", ""))
    if (
        chat.get("type") != "private"
        or chat_id != AUTHORIZED_CHAT_ID
        or sender_id != AUTHORIZED_CHAT_ID
    ):
        LOG.warning("忽略未授权或非私聊消息 chat_id=%s", chat_id or "unknown")
        return
    if command_name(str(message.get("text", ""))) != "/cleanup":
        return

    LOG.info("收到授权的 /cleanup 命令")
    try:
        deleted, freed, errors = cleanup_csv_files()
        if errors:
            details = "\n".join(errors[:10])
            send_message(
                "⚠️ CSV 清理部分完成\n"
                f"已删除: {deleted} 个\n"
                f"释放空间: {human_size(freed)}\n"
                f"失败: {len(errors)} 个\n{details}"
            )
        else:
            send_message(
                "🧹 CSV 清理完成\n"
                f"目录: {CLEANUP_DIR}\n"
                f"已删除: {deleted} 个\n"
                f"释放空间: {human_size(freed)}"
            )
    except Exception as error:
        LOG.exception("CSV 清理失败")
        send_message(f"❌ CSV 清理失败\n{error}")


def main() -> int:
    if not BOT_TOKEN or not AUTHORIZED_CHAT_ID:
        LOG.info("未同时配置 bot_token 和 chat_id，清理机器人未启用")
        return 0

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    offset = load_offset()
    if offset is None:
        # Discard commands that were queued before this destructive feature was
        # installed, so an old /cleanup can never run unexpectedly on first boot.
        latest = telegram_request(
            "getUpdates",
            {"offset": "-1", "timeout": "0", "allowed_updates": json.dumps(["message"])},
            timeout=20,
        )
        offset = int(latest[-1]["update_id"]) + 1 if latest else 0
        save_offset(offset)
        LOG.info("首次启动：已忽略安装前积压的 Telegram 消息")
    LOG.info("Telegram /cleanup 机器人已启动")

    while True:
        try:
            values = {"timeout": "50", "allowed_updates": json.dumps(["message"])}
            if offset is not None:
                values["offset"] = str(offset)
            updates = telegram_request("getUpdates", values, timeout=60)
            for update in updates:
                update_id = int(update["update_id"])
                try:
                    process_update(update)
                finally:
                    # A destructive command must never be replayed after restart,
                    # even if sending its confirmation message failed.
                    offset = update_id + 1
                    save_offset(offset)
        except KeyboardInterrupt:
            LOG.info("收到退出信号")
            return 0
        except Exception:
            LOG.exception("Telegram 轮询失败，10 秒后重试")
            time.sleep(10)


if __name__ == "__main__":
    raise SystemExit(main())
