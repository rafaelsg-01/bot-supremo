#!/bin/bash
# Entrada do container (roda como root, via tini):
#   1. empacota a extensão e escreve a política do Chrome;
#   2. abre a tela: Xorg na GPU real (TELA=xorg, padrão) ou Xvfb (TELA=xvfb). Se o Xorg falhar, cai no Xvfb;
#   3. abre o VNC, se VNC_SENHA estiver definida;
#   4. roda o serviço Python como o usuário "pessoa" (ele abre o Openbox e o Chrome).
# Se a tela ou o serviço morrerem, o container sai e o Docker o reinicia.
set -uo pipefail

export DISPLAY=:0
mkdir -p /perfil /estado
chown pessoa:pessoa /perfil
# 711: o serviço (pessoa) lê o .crx e o update.xml; a chave (600) só o root lê.
chmod 711 /estado

# Dá ao usuário "pessoa" acesso à GPU, com os mesmos números de grupo do host.
for dev in /dev/dri/card0 /dev/dri/renderD128; do
    [ -e "$dev" ] || continue
    gid=$(stat -c %g "$dev")
    grupo=$(getent group "$gid" | cut -d: -f1)
    if [ -z "$grupo" ]; then
        grupo="gpu$gid"
        groupadd -g "$gid" "$grupo"
    fi
    usermod -aG "$grupo" pessoa
done

python3 -m servico.empacotar || exit 1

rm -f /tmp/.X0-lock /tmp/.X11-unix/X0

tela_pronta() {
    for _ in $(seq 1 50); do
        xdpyinfo -display :0 >/dev/null 2>&1 && return 0
        sleep 0.2
    done
    return 1
}

abrir_xvfb() {
    Xvfb :0 -screen 0 1366x768x24 -dpi 96 -nolisten tcp &
    PID_TELA=$!
    TELA_ATIVA=xvfb
}

TELA_ATIVA=""
if [ "${TELA:-xorg}" = "xorg" ] && [ -e /dev/dri/card0 ]; then
    Xorg :0 vt7 -novtswitch -sharevts -nolisten tcp -noreset -dpi 96 \
        -config /etc/X11/bot-supremo.conf -logfile /tmp/Xorg.0.log &
    PID_TELA=$!
    TELA_ATIVA=xorg
    if ! tela_pronta; then
        echo "[entrada] o Xorg não subiu; últimas linhas do log:"
        tail -n 25 /tmp/Xorg.0.log 2>/dev/null
        kill "$PID_TELA" 2>/dev/null
        wait "$PID_TELA" 2>/dev/null
        rm -f /tmp/.X0-lock /tmp/.X11-unix/X0
        echo "[entrada] caindo para o Xvfb"
        abrir_xvfb
        tela_pronta || { echo "[entrada] nem o Xvfb subiu"; exit 1; }
    fi
else
    abrir_xvfb
    tela_pronta || { echo "[entrada] o Xvfb não subiu"; exit 1; }
fi
echo "[entrada] tela: $TELA_ATIVA"
export TELA_ATIVA
# O X foi aberto como root, sem xauth: libera os clientes locais (a tela só existe dentro do container).
xhost +local: >/dev/null

if [ -n "${VNC_SENHA:-}" ]; then
    x11vnc -storepasswd "$VNC_SENHA" /tmp/vnc.senha >/dev/null 2>&1
    x11vnc -display :0 -rfbauth /tmp/vnc.senha -rfbport 5900 -forever -shared -quiet \
        >/tmp/x11vnc.log 2>&1 &
fi

setpriv --reuid=pessoa --regid=pessoa --init-groups \
    env HOME=/home/pessoa USER=pessoa python3 -u -m servico.principal &
PID_SERVICO=$!

parar() {
    kill -TERM "$PID_SERVICO" 2>/dev/null
    wait "$PID_SERVICO" 2>/dev/null
    kill -TERM "$PID_TELA" 2>/dev/null
    exit 0
}
trap parar TERM INT

wait -n "$PID_TELA" "$PID_SERVICO"
echo "[entrada] a tela ou o serviço saiu; encerrando o container"
parar
