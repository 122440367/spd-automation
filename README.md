# SPD 自动上传与测速

此任务每天北京时间 07:01 执行以下流程：

1. 打开环境变量 `SPD_URL` 指定的网站，如出现密码框则自动填写。
2. 找出 `/root/ASNIPtest/` 目录中修改时间最新的 `.csv` 文件。
3. 确认 CSV 连续 5 秒没有变化，避免上传仍在生成中的文件。
4. 点击“上传 IP 列表”，选择该 CSV，再点击“上传并解析”。
5. 等待页面明确显示“上传成功”，然后点击“开始测速”。
6. 最长等待 10 分钟；显示“测速完成”后点击“上传到 GitHub”。
7. 等待 GitHub 上传成功后退出。任一步失败都会返回非零状态，并保存截图和 HTML。

上传、测速和 GitHub 上传都等待页面的明确结果，不依赖固定 `sleep` 猜测执行时间。

## 安装

```bash
git clone https://github.com/122440367/spd-automation.git
cd spd-automation
cp example.env .env
nano .env
sudo bash install.sh
```

`.env` 已被 Git 忽略。至少填写 `SPD_URL` 和 `SPD_PASSWORD`；需要 Telegram 通知时，
再填写 `bot_token` 和 `chat_id`。

手动测试并查看日志：

```bash
sudo systemctl start spd-automation.service
sudo journalctl -u spd-automation.service -f
```

查看下一次定时执行时间：

```bash
systemctl list-timers spd-automation.timer
```

## 配置项

主要环境变量如下：

- `SPD_URL`：目标网站。
- `SPD_PASSWORD`：页面密码。
- `SPD_CSV_DIR`：CSV 所在目录，默认 `/root/ASNIPtest`。
- `SPD_SPEEDTEST_TIMEOUT_SECONDS`：测速等待上限，当前为 `600` 秒。
- `SPD_FILE_STABLE_SECONDS`：上传前文件需要保持不变的时间，当前为 `5` 秒。
- `SPD_FILE_STABLE_TIMEOUT_SECONDS`：等待文件稳定的最长时间，当前为 `60` 秒。
- `bot_token`：Telegram Bot Token；与 `chat_id` 同时配置后启用通知。
- `chat_id`：Telegram 私人聊天 ID，用于接收通知及授权 `/cleanup` 命令。

## Telegram 命令

同时配置 `bot_token` 和 `chat_id` 后，安装器会启动独立的机器人服务。在指定的
私人聊天中发送：

```text
/cleanup
```

机器人会永久删除 `/root/ASNIPtest` 第一层的全部 `.csv` 文件，并回复删除数量和
释放空间。群组、频道及其他 Chat ID 的命令均会被忽略；如果 ASNIPtest 正在生成
文件，或自动化任务正在使用 CSV，清理会被拒绝，请稍后重试。

查看机器人状态和日志：

```bash
systemctl status spd-telegram-cleanup.service
journalctl -u spd-telegram-cleanup.service -f
```

## 项目特点

- **全流程自动化**：自动完成 CSV 选择、上传解析、IP 测速和 GitHub 发布。
- **智能选择文件**：按修改时间选择目录中最新的 CSV，并在上传前确认文件已稳定，
  避免读取仍在写入的数据。
- **状态驱动执行**：根据页面返回的成功或失败状态进入下一步，不依赖固定延时猜测。
- **完善的超时保护**：上传、测速和 GitHub 发布分别设置等待上限，测速最长 10 分钟，
  整个任务最长 25 分钟。
- **安全配置管理**：网址、密码和超时参数统一通过环境变量管理，真实 `.env` 默认
  不会提交到 GitHub，部署配置以 `0600` 权限保存。
- **可靠的定时任务**：使用 systemd timer 每天北京时间 07:01 自动运行，并支持
  关机错过任务后的补执行。
- **兼容多种登录方式**：支持普通密码输入框、JavaScript 密码提示，以及可选的
  HTTP Basic Auth 用户名和密码。
- **便于故障排查**：失败时返回非零状态，并自动保存页面截图、HTML 和 systemd 日志。
- **Telegram 集成**：可选发送执行结果，并允许授权私聊通过 `/cleanup` 安全清理 CSV。
- **无侵入设计**：通过浏览器模拟人工操作，不需要修改现有 `worker.js`。
