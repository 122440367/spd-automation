#!/usr/bin/env python3
"""Automate the SPD web UI: upload newest CSV, speed-test, then upload to GitHub."""

from __future__ import annotations

import logging
import os
import re
import sys
import time
import fcntl
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from playwright.sync_api import Browser, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


URL = os.getenv("SPD_URL", "").strip()
CSV_DIR = Path(os.getenv("SPD_CSV_DIR", "/root/ASNIPtest"))
PASSWORD = os.getenv("SPD_PASSWORD", "")
USERNAME = os.getenv("SPD_USERNAME", "")
HEADLESS = os.getenv("SPD_HEADLESS", "1").lower() not in {"0", "false", "no"}
PAGE_TIMEOUT_MS = int(os.getenv("SPD_PAGE_TIMEOUT_SECONDS", "60")) * 1000
UPLOAD_TIMEOUT_MS = int(os.getenv("SPD_UPLOAD_TIMEOUT_SECONDS", "300")) * 1000
SPEEDTEST_TIMEOUT_MS = int(os.getenv("SPD_SPEEDTEST_TIMEOUT_SECONDS", "600")) * 1000
GITHUB_TIMEOUT_MS = int(os.getenv("SPD_GITHUB_TIMEOUT_SECONDS", "300")) * 1000
FILE_STABLE_SECONDS = int(os.getenv("SPD_FILE_STABLE_SECONDS", "5"))
FILE_STABLE_TIMEOUT_SECONDS = int(os.getenv("SPD_FILE_STABLE_TIMEOUT_SECONDS", "60"))
ARTIFACT_DIR = Path(os.getenv("SPD_ARTIFACT_DIR", "/var/lib/spd-automation"))
TELEGRAM_BOT_TOKEN = os.getenv("bot_token", "").strip()
TELEGRAM_CHAT_ID = os.getenv("chat_id", "").strip()
JOB_LOCK_PATH = ARTIFACT_DIR / "job.lock"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOG = logging.getLogger("spd-automation")


def newest_csv(directory: Path) -> Path:
    if not directory.is_dir():
        raise RuntimeError(f"CSV 目录不存在或不是目录: {directory}")

    files = [path for path in directory.glob("*.csv") if path.is_file()]
    if not files:
        raise RuntimeError(f"目录中没有 CSV 文件: {directory}")

    return max(files, key=lambda path: (path.stat().st_mtime_ns, path.name)).resolve()


def wait_for_stable_file(path: Path) -> None:
    """Avoid uploading a CSV while another process is still writing it."""
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
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    handle = JOB_LOCK_PATH.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError("已有自动化或清理任务正在运行")
    return handle


def handle_dialog(dialog) -> None:
    """Fill a JavaScript password prompt if the site presents one."""
    LOG.info("页面弹窗: type=%s message=%s", dialog.type, dialog.message)
    if dialog.type == "prompt" and PASSWORD:
        dialog.accept(PASSWORD)
    else:
        dialog.accept()


def fill_login_page_if_present(page: Page) -> None:
    """Support a normal HTML password page; do nothing on the dashboard."""
    password_input = page.locator('input[type="password"]:visible').first
    if password_input.count() == 0:
        return

    if not PASSWORD:
        raise RuntimeError("页面要求密码，但 SPD_PASSWORD 未配置")

    LOG.info("检测到登录页，正在填写凭据")
    username_input = page.locator(
        'input[type="text"]:visible, input[type="email"]:visible, '
        'input[name*="user" i]:visible'
    ).first
    if USERNAME and username_input.count() > 0:
        username_input.fill(USERNAME)
    password_input.fill(PASSWORD)

    submit = page.get_by_role(
        "button", name=re.compile(r"登录|登入|进入|确认|提交|Login|Sign in", re.I)
    ).first
    if submit.count() > 0:
        submit.click()
    else:
        password_input.press("Enter")

    page.locator("#upload-btn").wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)


def wait_for_terminal_text(
    page: Page,
    success_selector: str,
    success_text: str,
    failure_texts: tuple[str, ...],
    timeout_ms: int,
    failure_selector: str | None = None,
) -> str:
    failure_selector = failure_selector or success_selector
    page.wait_for_function(
        """
        (args) => {
            const successEl = document.querySelector(args.successSelector);
            const failureEl = document.querySelector(args.failureSelector);
            const successValue = successEl
                ? (successEl.innerText || successEl.textContent || '').trim()
                : '';
            const failureValue = failureEl
                ? (failureEl.innerText || failureEl.textContent || '').trim()
                : '';
            return successValue.includes(args.successText) ||
                args.failureTexts.some(value => failureValue.includes(value));
        }
        """,
        {
            "successSelector": success_selector,
            "successText": success_text,
            "failureSelector": failure_selector,
            "failureTexts": list(failure_texts),
        },
        timeout=timeout_ms,
    )
    success_value = page.locator(success_selector).inner_text().strip()
    if success_text in success_value:
        return success_value

    failure_value = page.locator(failure_selector).inner_text().strip()
    raise RuntimeError(f"操作失败: {failure_value or success_value}")


def upload_csv(page: Page, csv_path: Path) -> str:
    LOG.info("选择最新 CSV: %s", csv_path)
    page.locator("#upload-btn").click()
    file_input = page.locator("#ip-file-input")
    file_input.wait_for(state="attached", timeout=PAGE_TIMEOUT_MS)
    file_input.set_input_files(str(csv_path))

    preview = page.locator("#upload-preview")
    try:
        preview.wait_for(state="visible", timeout=10_000)
        LOG.info("文件预览: %s", preview.inner_text().strip().replace("\n", " "))
    except PlaywrightTimeoutError:
        LOG.warning("未出现 IP 预览，仍将提交并以服务器返回结果为准")

    page.locator("#upload-submit-btn").click()
    result = wait_for_terminal_text(
        page,
        "#upload-status",
        "上传成功",
        ("上传失败", "请求失败"),
        UPLOAD_TIMEOUT_MS,
    )
    LOG.info("上传解析完成: %s", result.replace("\n", " "))
    return result


def run_speedtest(page: Page) -> str:
    LOG.info("开始测速")
    page.locator("#speedtest-btn").click()
    result = wait_for_terminal_text(
        page,
        "#speed-test-status",
        "测速完成",
        ("测速错误", "测速失败"),
        SPEEDTEST_TIMEOUT_MS,
        failure_selector="#result",
    )
    LOG.info("测速完成: %s", result.replace("\n", " "))
    return result


def upload_to_github(page: Page) -> str:
    LOG.info("开始上传到 GitHub")
    page.locator("#github-upload-btn").click()
    result = wait_for_terminal_text(
        page,
        "#result",
        "上传成功",
        ("上传失败", "请求失败"),
        GITHUB_TIMEOUT_MS,
    )
    LOG.info("GitHub 上传完成: %s", result.replace("\n", " "))
    return result


def save_failure_artifacts(page: Page) -> None:
    try:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        screenshot = ARTIFACT_DIR / f"failure-{stamp}.png"
        html = ARTIFACT_DIR / f"failure-{stamp}.html"
        page.screenshot(path=str(screenshot), full_page=True)
        html.write_text(page.content(), encoding="utf-8")
        LOG.error("失败现场已保存: %s 和 %s", screenshot, html)
    except Exception:
        LOG.exception("保存失败现场时发生错误")


def create_browser_context(browser: Browser):
    options = {"ignore_https_errors": False}
    # HTTP Basic Auth requires both fields. A password-only HTML/prompt login is
    # handled separately so the common case does not need a fake username.
    if USERNAME and PASSWORD:
        options["http_credentials"] = {"username": USERNAME, "password": PASSWORD}
    return browser.new_context(**options)


def compact_text(value: str, limit: int = 600) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def send_telegram(message: str) -> None:
    """Send an optional Telegram notification without affecting the main job."""
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
    csv_path: Path | None = None
    upload_result = ""
    speedtest_result = ""
    github_result = ""
    job_lock = None

    try:
        if not URL:
            raise RuntimeError("必须通过 SPD_URL 环境变量配置目标网址")

        stage = "获取任务锁"
        job_lock = acquire_job_lock()
        stage = "选择并检查 CSV"
        csv_path = newest_csv(CSV_DIR)
        wait_for_stable_file(csv_path)
        LOG.info("任务开始，目标=%s", URL)

        with sync_playwright() as playwright:
            browser = None
            context = None
            page: Page | None = None
            try:
                browser = playwright.chromium.launch(
                    headless=HEADLESS,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                context = create_browser_context(browser)
                page = context.new_page()
                page.set_default_timeout(PAGE_TIMEOUT_MS)
                page.on("dialog", handle_dialog)

                stage = "打开并登录页面"
                page.goto(URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
                fill_login_page_if_present(page)
                page.locator("#upload-btn").wait_for(
                    state="visible", timeout=PAGE_TIMEOUT_MS
                )

                stage = "上传并解析 CSV"
                upload_result = upload_csv(page, csv_path)
                stage = "IP 测速"
                speedtest_result = run_speedtest(page)
                stage = "上传到 GitHub"
                github_result = upload_to_github(page)
            except Exception:
                if page is not None and not page.is_closed():
                    save_failure_artifacts(page)
                raise
            finally:
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        LOG.exception("关闭浏览器上下文时发生错误")
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:
                        LOG.exception("关闭浏览器时发生错误")
    except Exception as error:
        elapsed = round(time.monotonic() - started)
        LOG.exception("自动化任务失败，阶段=%s", stage)
        send_telegram(
            "❌ SPD 自动化执行失败\n"
            f"阶段: {stage}\n"
            f"CSV: {csv_path.name if csv_path else '未选择'}\n"
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
        f"CSV: {csv_path.name if csv_path else '未知'}\n"
        f"上传: {compact_text(upload_result)}\n"
        f"测速: {compact_text(speedtest_result)}\n"
        f"GitHub: {compact_text(github_result)}\n"
        f"耗时: {elapsed} 秒"
    )
    if job_lock is not None:
        job_lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
