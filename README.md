# SPD Worker API 自动化 （自用）

每天北京时间 08:00 自动执行：

1. 调用 `POST /clear-uploaded-ips` 清空旧的待测速列表。
2. 选择 `/root/ASNIPtest` 中当天修改的全部 CSV，逐个上传。
3. Worker 将每次上传的 IP 追加、去重，最多保留 800 个。
4. 上传完成后任务结束，不再触发测速或 GitHub 发布。
5. 通过 Telegram 发送成功或失败结果（可选）。

项目直接调用 Worker API，不需要 Playwright、Chromium 或第三方 Python 包。
可以使用 systemd 或 Docker Compose 部署。

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

## Docker Compose 部署

先准备配置：

```bash
git clone https://github.com/122440367/spd-automation.git
cd spd-automation
cp example.env .env
nano .env
```

`.env` 至少填写 `SPD_URL` 和 `SPD_API_TOKEN`。默认读取宿主机
`/root/ASNIPtest`，如果 CSV 在其他目录，再填写：

```env
SPD_CSV_HOST_DIR=/your/asniptest/path
```

构建并启动常驻定时容器：

```bash
docker compose up -d --build
docker compose logs -f spd-automation
```

容器使用北京时间，每天 08:00 执行。如果容器在 08:00 之后才启动且当天尚未
执行，会立即补跑一次。可通过以下配置修改时间和时区：

```env
SPD_DAILY_TIME=08:00
TZ=Asia/Shanghai
```

不启动定时容器，立即手动执行一次：

```bash
docker compose run --rm spd-automation once
```

更新版本：

```bash
git pull --ff-only origin main
docker compose up -d --build
```

如果同一台机器已经启用了 systemd timer，应先停用它，避免重复执行：

```bash
sudo systemctl disable --now spd-automation.timer
```

Docker 部署当前只包含上传定时任务。Telegram `/cleanup` 机器人仍建议使用
systemd 部署，因为容器无法可靠判断宿主机上的 ASNIPtest 扫描进程。

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
- `SPD_CSV_HOST_DIR`：Docker Compose 挂载的宿主机 CSV 目录，默认 `/root/ASNIPtest`。
- `SPD_DAILY_TIME`：Docker 容器每日执行时间，默认 `08:00`。
- `TZ`：Docker 容器时区，默认 `Asia/Shanghai`。
- `SPD_UPLOAD_TIMEOUT_SECONDS`：上传超时，默认 `300` 秒。
- `SPD_MAX_PENDING_IPS`：上传到 Worker 的 IP 总数上限，默认和 Worker 上限均为 `800`。
- `SPD_STEP_DELAY_SECONDS`：清空和多文件上传之间的间隔，默认 `2` 秒。
- `SPD_FILE_STABLE_SECONDS`：上传前文件保持不变的时间，默认 `20` 秒。
- `SPD_FILE_STABLE_TIMEOUT_SECONDS`：等待文件稳定的上限，默认 `60` 秒。
- `bot_token`、`chat_id`：可选 Telegram 通知和命令功能。

自动化请求通过以下请求头认证：

```text
Authorization: Bearer <SPD_API_TOKEN>
```

本项目使用 `/upload-ips` 和 `/clear-uploaded-ips`。Worker 未配置 Secret、请求缺少
Token 或 Token 错误时都会拒绝。

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

- **低磁盘占用**：不安装浏览器、虚拟环境或第三方 Python 包，Docker 镜像基于 Alpine。
- **状态驱动**：每一步验证 Worker 返回的 JSON 和 `success` 状态。
- **API Token 认证**：所有写操作接口使用 Bearer Token，Worker 未配置 Token 时默认拒绝。
- **Cloudflare 检测**：识别 `cf-mitigated: challenge` 和非 JSON 验证页面。
- **当天多文件上传**：按修改时间选择当天全部 CSV，逐个上传并合并去重。
- **只上传 Worker**：清空旧列表后上传当天 CSV，不触发测速或 GitHub 发布。
- **800 条上限**：客户端提交的 `maxIPs` 默认与 Worker 上限一致，均为 800。
- **安全选取文件**：每个 CSV 保持 20 秒不再变化后才上传。
- **防止并发冲突**：上传任务和 Telegram 清理命令共用文件锁。
- **可靠定时执行**：systemd timer 每天北京时间 08:00 运行，支持错过后补执行。
- **Docker 支持**：提供一次性运行和带补跑能力的每日定时容器。
- **Telegram 集成**：发送执行结果，并支持授权私聊使用 `/cleanup`。
