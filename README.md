# SPD Worker API 自动化

每天北京时间 07:01 自动执行：

1. 选择 `/root/ASNIPtest` 中修改时间最新的 CSV。
2. 调用 `POST /upload-ips` 上传并解析。
3. 调用 `POST /manual-speedtest` 测试 IP。
4. 调用 `POST /upload-to-github` 发布结果。
5. 通过 Telegram 发送成功或失败结果（可选）。

项目直接调用 Worker API，不需要 Playwright、Chromium、Docker或第三方 Python 包。

## 安装

```bash
git clone https://github.com/122440367/spd-automation.git
cd spd-automation
cp example.env .env
nano .env
sudo bash install.sh
```

`.env` 中只需填写 Worker 地址：

```env
SPD_URL=https://your-spd-domain.example/
```

手动执行并查看日志：

```bash
sudo systemctl start spd-automation.service
sudo journalctl -u spd-automation.service -f
```

查看下一次执行时间：

```bash
systemctl list-timers spd-automation.timer
```

## 从 Playwright 旧版升级

```bash
cd /root/spd-automation
git pull
sudo bash install.sh
```

安装器会自动删除 `/opt/spd-automation/.venv` 和
`/opt/spd-automation/browsers`，释放旧版 Chromium 占用的空间。

## 配置项

- `SPD_URL`：Worker 地址，必填。
- `SPD_CSV_DIR`：CSV 目录，默认 `/root/ASNIPtest`。
- `SPD_UPLOAD_TIMEOUT_SECONDS`：上传超时，默认 `300` 秒。
- `SPD_SPEEDTEST_TIMEOUT_SECONDS`：测速超时，默认 `600` 秒。
- `SPD_GITHUB_TIMEOUT_SECONDS`：GitHub 上传超时，默认 `300` 秒。
- `SPD_MAX_TESTS`：最多测试的 IP 数量，默认 `25`，Worker 上限 `50`。
- `SPD_FILE_STABLE_SECONDS`：上传前文件保持不变的时间，默认 `5` 秒。
- `SPD_FILE_STABLE_TIMEOUT_SECONDS`：等待文件稳定的上限，默认 `60` 秒。
- `SPD_USERNAME`、`SPD_PASSWORD`：可选 HTTP Basic Auth，必须同时配置。
- `bot_token`、`chat_id`：可选 Telegram 通知和命令功能。

当前仓库参考的 `worker.js` 没有校验页面密码，因此 API 模式不需要
`SPD_PASSWORD`。如果以后在 Worker 前增加 HTTP Basic Auth，再同时配置
`SPD_USERNAME` 和 `SPD_PASSWORD`。

## Telegram 命令

在 `.env` 中同时配置：

```env
bot_token=replace_with_your_bot_token
chat_id=replace_with_your_private_chat_id
```

指定的私人聊天可以发送：

```text
/cleanup
```

机器人会永久删除 `/root/ASNIPtest` 第一层的全部 CSV，并回复删除数量和释放空间。
如果 ASNIPtest 正在生成文件或自动化正在读取 CSV，清理会被拒绝。

查看机器人日志：

```bash
journalctl -u spd-telegram-cleanup.service -f
```

## 项目特点

- **低磁盘占用**：不安装浏览器、虚拟环境或第三方 Python 包。
- **状态驱动**：每一步验证 Worker 返回的 JSON 和 `success` 状态。
- **Cloudflare 检测**：识别 `cf-mitigated: challenge` 和非 JSON 验证页面。
- **安全选取文件**：选择最新 CSV，并确认文件不再变化后才上传。
- **防止并发冲突**：测速任务和 Telegram 清理命令共用文件锁。
- **可靠定时执行**：systemd timer 每天北京时间 07:01 运行，支持错过后补执行。
- **Telegram 集成**：发送执行结果，并支持授权私聊使用 `/cleanup`。
