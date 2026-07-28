#!/usr/bin/env bash
#
# 在樹莓派上安裝 Tailscale,讓儀表板可以從家裡以外的網路存取。
#
# 為什麼需要:server.py 綁在 0.0.0.0,但 192.168.0.x 是私有位址,
# 只有連著家裡 WiFi 的裝置看得到。Tailscale 會建立一個私有虛擬網路,
# 你的手機不管在哪都能連到這台樹莓派 —— 而且不必在路由器上開任何連接埠。
#
# 用法:
#     sudo ./setup_tailscale.sh              # 安裝並登入
#     sudo ./setup_tailscale.sh --install-only   # 只安裝,稍後自己 tailscale up
#
set -euo pipefail

HOSTNAME_TS="jimmy-devices"        # 登入後這台機器在 tailnet 裡的名字(MagicDNS 會用到)
KEYRING=/usr/share/keyrings/tailscale-archive-keyring.gpg
SRC_LIST=/etc/apt/sources.list.d/tailscale.list

if [[ ${EUID} -ne 0 ]]; then
  echo "請用 sudo 執行:sudo $0" >&2
  exit 1
fi

install_only=0
[[ ${1:-} == "--install-only" ]] && install_only=1

# ---- 找出這個 Debian 版本對應的套件庫 ----
# Tailscale 的套件庫是依發行代號分開的。若目前系統太新、對應的目錄還沒發布,
# 就退回上一個穩定代號 —— 套件本身相容,不必等官方補上。
. /etc/os-release
codename="${VERSION_CODENAME:-bookworm}"
base="https://pkgs.tailscale.com/stable/debian"

if ! curl -fsI "${base}/${codename}.noarmor.gpg" >/dev/null 2>&1; then
  echo "[i] 套件庫尚未提供 ${codename},改用 bookworm(相容)"
  codename="bookworm"
fi
echo "[1/4] 使用套件庫:${base}/${codename}"

# ---- 加入簽章金鑰與套件來源 ----
curl -fsSL "${base}/${codename}.noarmor.gpg" -o "${KEYRING}"
curl -fsSL "${base}/${codename}.tailscale-keyring.list" -o "${SRC_LIST}"
chmod 0644 "${KEYRING}" "${SRC_LIST}"
echo "[2/4] 已加入套件來源"

# ---- 安裝 ----
apt-get update -qq
apt-get install -y tailscale
echo "[3/4] 已安裝 $(tailscale version | head -1)"

systemctl enable --now tailscaled
echo "[4/4] tailscaled 已啟動並設為開機自動執行"

if [[ ${install_only} -eq 1 ]]; then
  echo
  echo "安裝完成。接著請執行:sudo tailscale up --hostname=${HOSTNAME_TS}"
  exit 0
fi

# ---- 登入 ----
# 這一步會印出一個授權網址,要用瀏覽器打開並登入(Google / Apple / GitHub 帳號皆可)。
# 登入完成前指令會停在這裡等待。
echo
echo "=============================================================="
echo " 接下來會出現一個授權網址,請用瀏覽器打開並登入。"
echo " 手機端請安裝 Tailscale App 並登入同一個帳號。"
echo "=============================================================="
echo
tailscale up --hostname="${HOSTNAME_TS}"

echo
echo "完成。這台機器的 Tailscale 位址:"
tailscale ip -4
echo
echo "在任何已登入同一帳號的裝置上,用下面任一個網址開儀表板:"
echo "  http://$(tailscale ip -4 | head -1):8080/"
echo "  http://${HOSTNAME_TS}:8080/          (需在 Tailscale 後台開啟 MagicDNS)"
