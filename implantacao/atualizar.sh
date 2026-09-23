#!/bin/bash
# Mantém o bot-supremo no ar no notebook. Roda como root a cada 2 min (bot-supremo-atualizar.timer).
#
# Recria o container quando:
#   - saiu imagem nova no GHCR (deploy automático);
#   - o container "warp" foi recriado (acontece em todo boot, pelo ~/start.sh). Como usamos a
#     rede dele (network_mode container:warp), o nosso container fica sem rede até ser recriado;
#   - o container não está rodando.
#
# Modo dev: com o arquivo .modo-dev na pasta, usa também o compose.dev.yml e não faz pull.
# Tudo fica dentro de uma função para o bash ler o script inteiro antes de rodar
# (o git pull pode trocar este arquivo no meio).

principal() {
    set -u
    local dir dono arquivos modo_dev id_warp img_antes img_depois rede rodando
    dir="$(cd "$(dirname "$0")/.." && pwd)"
    cd "$dir" || exit 1
    dono="$(stat -c %U "$dir")"

    # Tela interna do notebook apagada (o Xorg usa a GPU, mas ninguém olha para a tela).
    for b in /sys/class/backlight/*/brightness; do
        [ -w "$b" ] && echo 0 > "$b"
    done

    arquivos=(-f compose.yml)
    modo_dev=0
    if [ -f .modo-dev ]; then
        arquivos+=(-f compose.dev.yml)
        modo_dev=1
    fi

    [ -f .env ] || { echo "falta o .env em $dir"; exit 1; }
    mkdir -p dados/perfil dados/estado
    chown "$dono": dados dados/perfil

    id_warp="$(docker inspect -f '{{.Id}}' warp 2>/dev/null)" || { echo "container warp não existe; esperando"; exit 0; }
    [ "$(docker inspect -f '{{.State.Running}}' warp)" = "true" ] || { echo "warp parado; esperando"; exit 0; }

    img_antes="$(docker compose "${arquivos[@]}" images -q bot 2>/dev/null | head -n1)"
    if [ "$modo_dev" = 0 ]; then
        sudo -u "$dono" git pull --ff-only -q || echo "aviso: git pull falhou"
        docker compose "${arquivos[@]}" pull -q bot || echo "aviso: pull da imagem falhou"
    fi
    img_depois="$(docker image inspect -f '{{.Id}}' "$(docker compose "${arquivos[@]}" config --images | head -n1)" 2>/dev/null)"

    rede="$(docker inspect -f '{{.HostConfig.NetworkMode}}' bot-supremo 2>/dev/null)"
    rodando="$(docker inspect -f '{{.State.Running}}' bot-supremo 2>/dev/null)"

    motivo=""
    [ "$rodando" = "true" ] || motivo="container não está rodando"
    [ -z "$motivo" ] && [ "$rede" != "container:$id_warp" ] && motivo="o warp foi recriado"
    [ -z "$motivo" ] && [ -n "$img_antes" ] && [ "$img_antes" != "$img_depois" ] && motivo="imagem nova"
    [ -z "$motivo" ] && return 0

    echo "recriando o bot-supremo: $motivo"
    docker compose "${arquivos[@]}" up -d --force-recreate bot
    docker image prune -f >/dev/null
}

principal "$@"
exit
