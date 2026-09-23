#!/bin/bash
# Instala o bot-supremo no notebook (uma vez). Rode com sudo, de dentro do clone do repositório:
#   sudo bash implantacao/instalar.sh
set -euo pipefail

dir="$(cd "$(dirname "$0")/.." && pwd)"
dono="$(stat -c %U "$dir")"

if [ ! -f "$dir/.env" ]; then
    cp "$dir/.env.exemplo" "$dir/.env"
    sed -i "s/^BOT_TOKEN=.*/BOT_TOKEN=$(openssl rand -hex 32)/" "$dir/.env"
    sed -i "s/^VNC_SENHA=.*/VNC_SENHA=$(openssl rand -hex 6)/" "$dir/.env"
    chown "$dono": "$dir/.env"
    chmod 600 "$dir/.env"
    echo "criado $dir/.env com token e senha do VNC novos"
fi

chmod +x "$dir/implantacao/atualizar.sh"
sed "s#@DIR@#$dir#g" "$dir/implantacao/bot-supremo-atualizar.service" \
    > /etc/systemd/system/bot-supremo-atualizar.service
cp "$dir/implantacao/bot-supremo-atualizar.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now bot-supremo-atualizar.timer
echo "timer instalado. Logs: journalctl -u bot-supremo-atualizar -f"
