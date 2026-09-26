#!/bin/bash
# Mostra o IP atual do container "warp" na rede flare-net (muda a cada boot).
# É nesse IP que ficam a API (8080), o VNC (5900) e o noVNC (6080) do bot.
docker inspect -f '{{(index .NetworkSettings.Networks "flare-net").IPAddress}}' warp
