# SPD Worker API 自动化 （自用）

每天北京时间 08:00 自动执行：

1. 调用 `POST /clear-uploaded-ips` 清空旧的待测速列表。
2. 选择 `/root/ASNIPtest` 中当天修改的全部 CSV，逐个上传。
3. Worker 将每次上传的 IP 追加、去重，最多保留 300 个。
4. 每次调用 `POST /manual-speedtest` 测试 20 个，循环到全部完成。
5. 调用 `POST /upload-to-github` 发布结果。
6. 通过 Telegram 发送成功或失败结果（可选）。

项目直接调用 Worker API，不需要 Playwright、Chromium、Docker或第三方 Python 包。

## 安装

```bash
git clone https://github.com/122440367/spd-automation.git
cd spd-automation
cp example.env .env
nano .env
sudo bash install.sh
```

生成一个随机 Token：

```bash
openssl rand -hex 32
```

将同一个值分别添加到 Cloudflare Worker Secret 和本项目 `.env`。`.env` 必填：

```env
SPD_URL=https://your-spd-domain.example/
SPD_API_TOKEN=replace_with_the_same_random_token
```

Cloudflare 控制台路径通常为：`Workers & Pages → 你的 Worker → Settings →
Variables and Secrets → Add`。变量名填写 `SPD_API_TOKEN`，类型选择 Secret。

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
- `SPD_API_TOKEN`：Worker API Bearer Token，必填，必须与 Worker Secret 一致。
- `SPD_CSV_DIR`：CSV 目录，默认 `/root/ASNIPtest`。
- `SPD_UPLOAD_TIMEOUT_SECONDS`：上传超时，默认 `300` 秒。
- `SPD_SPEEDTEST_TIMEOUT_SECONDS`：全部分批测速的总超时，默认 `600` 秒。
- `SPD_GITHUB_TIMEOUT_SECONDS`：GitHub 上传超时，默认 `300` 秒。
- `SPD_MAX_PENDING_IPS`：待测速 IP 总数上限，默认和 Worker 上限均为 `300`。
- `SPD_SPEEDTEST_BATCH_SIZE`：每批测速数量，默认和 Worker 单批上限均为 `20`。
- `SPD_SPEEDTEST_BATCH_RETRIES`：每批失败后的最多尝试次数，默认 `3`；重试复用同一批结果。
- `SPD_STEP_DELAY_SECONDS`：清空、上传和测速批次之间的间隔，默认 `2` 秒。
- `SPD_FILE_STABLE_SECONDS`：上传前文件保持不变的时间，默认 `20` 秒。
- `SPD_FILE_STABLE_TIMEOUT_SECONDS`：等待文件稳定的上限，默认 `60` 秒。
- `bot_token`、`chat_id`：可选 Telegram 通知和命令功能。

自动化请求通过以下请求头认证：

```text
Authorization: Bearer <SPD_API_TOKEN>
```

Worker 会保护 `/update`、`/upload-ips`、`/clear-uploaded-ips`、
`/manual-speedtest` 和 `/upload-to-github`。Worker 未配置 Secret、请求缺少 Token
或 Token 错误时都会拒绝。

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

## 项目亮点

- **低磁盘占用**：不安装浏览器、虚拟环境或第三方 Python 包。
- **状态驱动**：每一步验证 Worker 返回的 JSON 和 `success` 状态。
- **API Token 认证**：所有写操作接口使用 Bearer Token，Worker 未配置 Token 时默认拒绝。
- **Cloudflare 检测**：识别 `cf-mitigated: challenge` 和非 JSON 验证页面。
- **当天多文件上传**：按修改时间选择当天全部 CSV，逐个上传并合并去重。
- **分批测速**：默认最多保留 300 个待测速 IP，每批测试 20 个直到完成。
- **低 KV 写入**：每批只更新一次进度，最后一批才汇总并写入优质 IP。
- **任务隔离与幂等重试**：每轮测速使用独立 `runId`，相同批次重试不会重复测速。
- **临时状态自动过期**：任务快照和独立批次结果在 24 小时后由 KV 自动清理。
- **安全选取文件**：每个 CSV 保持 20 秒不再变化后才上传。
- **防止并发冲突**：测速任务和 Telegram 清理命令共用文件锁。
- **可靠定时执行**：systemd timer 每天北京时间 08:00 运行，支持错过后补执行。
- **Telegram 集成**：发送执行结果，并支持授权私聊使用 `/cleanup`。
