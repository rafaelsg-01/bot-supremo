"""Pede ao bot (o Chrome, na mesma rede do warp) o link do vídeo de uma página.

Mesma lógica que ficava no projeto-iptv (function_rc.ts, Function_getLinkMp4List):
- pedido rápido: começa a olhar a página assim que ela chega e clica no play (#submit, dentro do
  iframe player3/server.php) quando o iframe terminou de carregar. Se o player não chamar o
  serverforms.api em 4 s, clica de novo, até 3 vezes; se já reagiu, nunca clica de novo;
- reserva (o pedido antigo, 15 s parado e só então o play): só se o site NÃO reagiu ao play.
  Se reagiu e mesmo assim não veio vídeo, o problema é o limite do site, e repetir só piora.

ATENÇÃO: o site limita o serverforms.api por IP e tempo. Por isso o cache existe.
"""
import asyncio
import json
import logging
import re

import aiohttp

log = logging.getLogger("video.busca")

PADRAO_VIDEO = r"[?&]url=https?://[^?&]+[.]mp4[?]"
_RE_VIDEO = re.compile(PADRAO_VIDEO)

# O desafio da Cloudflare ("Um momento...") é resolvido pelo próprio pedido. Vai em todo pedido.
ACAO_DESAFIO = {
    "quando": "desafio", "tipo": "clicar", "seletor": "input[type=checkbox]",
    "frame": "challenges.cloudflare.com", "seExistir": True, "timeoutMs": 30000,
}


def pedido_rapido(url):
    return {
        "url": url, "html": False, "timeoutMs": 120000, "esperarPagina": "nao",
        "acoes": [ACAO_DESAFIO, {
            "tipo": "clicar", "seletor": "#submit", "frame": "player3/server.php", "frameCompleto": True,
            "confirmarRede": "player3/serverforms[.]api", "confirmarMs": 4000, "tentativas": 3, "timeoutMs": 40000,
        }],
        "esperarRede": {"padrao": PADRAO_VIDEO, "timeoutMs": 30000},
    }


def pedido_reserva(url):
    return {
        "url": url, "html": False, "timeoutMs": 150000,
        "acoes": [ACAO_DESAFIO, {"tipo": "esperar", "ms": 15000},
                  {"tipo": "clicar", "seletor": "#submit", "frame": "player3/server.php", "timeoutMs": 20000}],
        "esperarRede": {"padrao": PADRAO_VIDEO, "timeoutMs": 45000},
    }


class ErroBusca(Exception):
    pass


# Bot momentaneamente fora (container sendo recriado) ou fila cheia: tenta mais 2 vezes.
STATUS_TENTAR_DE_NOVO = {429, 502, 503, 504}


class Busca:
    def __init__(self, url_bot, token):
        self.url_bot = url_bot.rstrip("/")
        self.token = token

    async def _navegar(self, corpo):
        for tentativa in range(1, 4):
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=400)) as s:
                    async with s.post(f"{self.url_bot}/v1/navegar", json=corpo,
                                      headers={"Authorization": f"Bearer {self.token}"}) as r:
                        texto = await r.text()
                        if r.status in STATUS_TENTAR_DE_NOVO and tentativa < 3:
                            log.warning("bot respondeu %d; tentando de novo (%d/3)", r.status, tentativa)
                        elif r.status != 200:
                            raise ErroBusca(f"bot respondeu HTTP {r.status}: {texto[:200]}")
                        else:
                            # A resposta começa com espaços (mantém a conexão viva); o json aceita.
                            return json.loads(texto)
            except (aiohttp.ClientError, OSError, TimeoutError) as e:
                if tentativa >= 3:
                    raise ErroBusca(f"bot fora do ar: {e}") from None
                log.warning("falha ao falar com o bot (%s); tentando de novo (%d/3)", e, tentativa)
            await asyncio.sleep(10)
        raise ErroBusca("bot não respondeu")

    @staticmethod
    def _links(resposta):
        urls = [r["url"] for r in resposta.get("rede") or []
                if r.get("metodo") == "GET" and _RE_VIDEO.search(r.get("url", ""))]
        return list(dict.fromkeys(urls))

    async def links(self, url_pagina):
        """Lista de links de vídeo (URLs cruas). Levanta ErroBusca se o player não pediu nenhum."""
        resposta = await self._navegar(pedido_rapido(url_pagina))
        links = self._links(resposta)
        play = next((a for a in resposta.get("acoes") or [] if a.get("seletor") == "#submit"), None)
        if not links and not (play or {}).get("confirmado"):
            log.warning("reserva: o pedido rápido não fez o player reagir - %s %s %s",
                        url_pagina, (play or {}).get("resultado"), resposta.get("erros"))
            resposta = await self._navegar(pedido_reserva(url_pagina))
            links = self._links(resposta)
        if not links:
            raise ErroBusca(f"o player não pediu nenhum vídeo ({resposta.get('status')}, {resposta.get('erros')})")
        return links
