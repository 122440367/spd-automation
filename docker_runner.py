#!/usr/bin/env python3
"""Run the upload once or schedule it daily inside a Docker container."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, time as daily_time, timedelta
from pathlib import Path


LOG = logging.getLogger("spd-docker-runner")
APP = Path(__file__).with_name("spd_automation.py")
STATE_FILE = Path(
    os.getenv(
        "SPD_DOCKER_STATE_FILE",
        "/var/lib/spd-automation/last-run-date",
    )
)
STOP_REQUESTED = False
CHILD: subprocess.Popen | None = None


def parse_daily_time(value: str) -> daily_time:
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as error:
        raise ValueError("SPD_DAILY_TIME 必须使用 HH:MM 格式，例如 08:00") from error
    return daily_time(parsed.hour, parsed.minute)


def scheduled_for(day: datetime, at: daily_time) -> datetime:
    return day.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)


def read_last_run_date() -> str:
    try:
        return STATE_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def record_run_date(value: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = STATE_FILE.with_suffix(".tmp")
    temp_file.write_text(value, encoding="utf-8")
    temp_file.replace(STATE_FILE)


def run_upload() -> int:
    global CHILD
    LOG.info("开始执行 SPD Worker 上传任务")
    CHILD = subprocess.Popen([sys.executable, str(APP)])
    try:
        return_code = CHILD.wait()
    finally:
        CHILD = None
    if return_code == 0:
        LOG.info("SPD Worker 上传任务执行成功")
    else:
        LOG.error("SPD Worker 上传任务执行失败，退出码=%s", return_code)
    return return_code


def request_stop(signum, _frame) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True
    LOG.info("收到信号 %s，准备停止容器", signum)
    if CHILD is not None and CHILD.poll() is None:
        CHILD.terminate()


def sleep_until(target: datetime) -> None:
    while not STOP_REQUESTED:
        remaining = (target - datetime.now().astimezone()).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 60))


def schedule() -> int:
    at = parse_daily_time(os.getenv("SPD_DAILY_TIME", "07:10"))
    LOG.info(
        "Docker 定时任务已启动，每天 %s 执行，当前时区=%s",
        at.strftime("%H:%M"),
        time.tzname[0],
    )

    while not STOP_REQUESTED:
        now = datetime.now().astimezone()
        today = now.date().isoformat()
        today_target = scheduled_for(now, at)
        last_run = read_last_run_date()

        if now >= today_target and last_run != today:
            run_upload()
            if not STOP_REQUESTED:
                record_run_date(today)
            continue

        if now < today_target:
            next_target = today_target
        else:
            next_target = scheduled_for(now + timedelta(days=1), at)
        LOG.info("下一次执行时间: %s", next_target.isoformat())
        sleep_until(next_target)

    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    mode = sys.argv[1] if len(sys.argv) > 1 else "schedule"
    if mode == "once":
        return run_upload()
    if mode == "schedule":
        return schedule()
    LOG.error("未知运行模式 %r，只支持 once 或 schedule", mode)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
