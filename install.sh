#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "请使用 root 运行: sudo bash install.sh" >&2
    exit 1
fi

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
install_dir=/opt/spd-automation
env_file=/etc/spd-automation.env
local_env_file="${source_dir}/.env"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv ca-certificates

install -d -m 0755 "${install_dir}"
install -d -m 0750 /var/lib/spd-automation
install -m 0755 "${source_dir}/spd_automation.py" "${install_dir}/spd_automation.py"
install -m 0755 "${source_dir}/telegram_cleanup_bot.py" "${install_dir}/telegram_cleanup_bot.py"
install -m 0644 "${source_dir}/requirements.txt" "${install_dir}/requirements.txt"

if [[ ! -f ${local_env_file} ]]; then
    echo "缺少本地环境配置: ${local_env_file}" >&2
    echo "请先执行: cp example.env .env，然后编辑 .env 填写真实密码。" >&2
    exit 1
fi

if [[ -f ${env_file} ]]; then
    install -m 0600 "${env_file}" "${env_file}.bak"
    echo "已有环境配置已备份到: ${env_file}.bak"
fi
install -m 0600 "${local_env_file}" "${env_file}"
echo "已安装 root-only 环境配置: ${env_file}"

python3 -m venv "${install_dir}/.venv"
"${install_dir}/.venv/bin/pip" install --upgrade pip
"${install_dir}/.venv/bin/pip" install -r "${install_dir}/requirements.txt"
PLAYWRIGHT_BROWSERS_PATH="${install_dir}/browsers" \
    "${install_dir}/.venv/bin/python" -m playwright install --with-deps chromium

install -m 0644 "${source_dir}/spd-automation.service" /etc/systemd/system/spd-automation.service
install -m 0644 "${source_dir}/spd-automation.timer" /etc/systemd/system/spd-automation.timer
install -m 0644 "${source_dir}/spd-telegram-cleanup.service" /etc/systemd/system/spd-telegram-cleanup.service
systemctl daemon-reload
systemctl enable --now spd-automation.timer
systemctl enable spd-telegram-cleanup.service
systemctl restart spd-telegram-cleanup.service

echo
echo "安装完成。下一次执行时间："
systemctl list-timers spd-automation.timer --no-pager
echo
echo "建议现在手动测试一次：systemctl start spd-automation.service"
echo "查看日志：journalctl -u spd-automation.service -n 100 --no-pager"
echo "清理机器人日志：journalctl -u spd-telegram-cleanup.service -n 100 --no-pager"
