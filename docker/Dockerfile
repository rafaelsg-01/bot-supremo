# Imagem do bot-supremo: tela (Xorg na GPU real ou Xvfb), Openbox, Google Chrome oficial,
# xdotool, x11vnc e o serviço Python. Construída no GitHub Actions; o notebook só baixa.
FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=pt_BR.UTF-8 \
    LANGUAGE=pt_BR:pt \
    LC_ALL=pt_BR.UTF-8 \
    TZ=America/Sao_Paulo \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg locales tzdata tini procps \
        xserver-xorg-core xvfb x11-utils x11-xserver-utils xauth \
        libgl1-mesa-dri libegl1 libgbm1 mesa-vulkan-drivers \
        openbox xdotool x11vnc \
        fonts-liberation fonts-noto-core fonts-noto-color-emoji fonts-dejavu-core \
        python3 python3-aiohttp python3-psutil python3-cryptography \
    && sed -i 's/^# *pt_BR.UTF-8/pt_BR.UTF-8/' /etc/locale.gen && locale-gen \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Muda toda semana (o workflow passa a semana do ano) para forçar o Chrome mais novo.
ARG SEMANA_CHROME=0
RUN echo "semana $SEMANA_CHROME" \
    && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Usuário comum que roda o Chrome e o serviço. O Xorg é o único processo como root.
RUN useradd -m -u 1000 -s /bin/bash pessoa

WORKDIR /opt/bot-supremo
COPY docker/xorg.conf /etc/X11/bot-supremo.conf
COPY docker/entrada.sh /opt/bot-supremo/docker/entrada.sh
COPY extensao /opt/bot-supremo/extensao
COPY servico /opt/bot-supremo/servico
RUN chmod +x /opt/bot-supremo/docker/entrada.sh

EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/bot-supremo/docker/entrada.sh"]
