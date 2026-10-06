"""Pede ao bot (o Chrome, na mesma rede do warp) o link do vídeo de uma página.

Mesma lógica que ficava no projeto-iptv (function_rc.ts, Function_getLinkMp4List):
- pedido rápido: começa a olhar a página assim que ela chega e clica no play (#submit, dentro do
  iframe player3/server.php) quando o iframe terminou de carregar. Se o player não chamar o
  serverforms.api em 4 s, clica de novo, até 3 vezes; se já reagiu, nunca clica de novo;
- reserva (o pedido antigo, 15 s parado e só então o play): só se o site NÃO reagiu ao play.
  Se reagiu e mesmo assim não veio vídeo, o problema é o limite do site, e repetir só piora.

ATENÇÃO: o site limita o serverforms.api por IP e tempo. Por isso o cache existe.

Todo pedido leva hostEsperado: se a página parar fora do site (domínio velho redirecionando para
outro lugar), o bot desiste na hora e quem chama tenta outro domínio (dominio.py).
"""
import asyncio
import json
import logging
import re

import aiohttp

from .dominio import falha_de_dominio, saiu_do_site

log = logging.getLogger("video.busca")

PADRAO_VIDEO = r"[?&]url=https?://[^?&]+[.]mp4[?]"
_RE_VIDEO = re.compile(PADRAO_VIDEO)

# O desafio da Cloudflare ("Um momento...") é resolvido pelo próprio pedido. Vai em todo pedido.
ACAO_DESAFIO = {
    "quando": "desafio", "tipo": "clicar", "seletor": "input[type=checkbox]",
    "frame": "challenges.cloudflare.com", "seExistir": True, "timeoutMs": 30000,
}
# A página tem que terminar em algum redecanais.* (a troca de .press para .ae é tratada em dominio.py).
HOST_ESPERADO = r"(^|[.])redecanais[.]"


def pedido_rapido(url):
    return {
        "url": url, "html": False, "timeoutMs": 200000, "esperarPagina": "nao", "hostEsperado": HOST_ESPERADO,
        "acoes": [ACAO_DESAFIO, {
            "tipo": "clicar", "seletor": "#submit", "frame": "player3/server.php", "frameCompleto": True,
            "confirmarRede": "player3/serverforms[.]api", "confirmarMs": 4000, "tentativas": 3, "timeoutMs": 90000,
        }],
        "esperarRede": {"padrao": PADRAO_VIDEO, "timeoutMs": 60000},
    }


def pedido_reserva(url):
    return {
        "url": url, "html": False, "timeoutMs": 240000, "hostEsperado": HOST_ESPERADO,
        "acoes": [ACAO_DESAFIO, {"tipo": "esperar", "ms": 15000},
                  {"tipo": "clicar", "seletor": "#submit", "frame": "player3/server.php", "timeoutMs": 60000}],
        "esperarRede": {"padrao": PADRAO_VIDEO, "timeoutMs": 60000},
    }


class ErroBusca(Exception):
    pass


class ErroPlayer(ErroBusca):
    """O bot respondeu, mas o player não pediu nenhum vídeo. Às vezes é o Chrome que ficou num
    estado ruim (01/10: só voltou depois de reabrir o Chrome); por isso quem chama pode reabrir.
    `resposta` é a última resposta do bot; `dominio` diz se parece o domínio que não serve mais."""

    def __init__(self, mensagem, resposta=None):
        super().__init__(mensagem)
        self.resposta = resposta or {}
        self.dominio = falha_de_dominio(self.resposta)


# Bot momentaneamente fora (container sendo recriado) ou fila cheia: tenta mais 2 vezes.
STATUS_TENTAR_DE_NOVO = {429, 502, 503, 504}


class Busca:
    def __init__(self, url_bot, token):
        self.url_bot = url_bot.rstrip("/")
        self.token = token

    async def navegar(self, corpo):
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
        """(links de vídeo, resposta do bot). Levanta ErroBusca se o player não pediu nenhum."""
        resposta = await self.navegar(pedido_rapido(url_pagina))
        links = self._links(resposta)
        play = next((a for a in resposta.get("acoes") or [] if a.get("seletor") == "#submit"), None)
        if not links and (saiu_do_site(resposta) or (resposta.get("statusHttp") or 0) >= 400):
            # Domínio velho ou site fora: o pedido reserva daria no mesmo.
            raise ErroPlayer(f"a página não abriu no site ({resposta.get('status')}, {resposta.get('erros')})", resposta)
        if not links and not (play or {}).get("confirmado"):
            log.warning("reserva: o pedido rápido não fez o player reagir - %s %s %s",
                        url_pagina, (play or {}).get("resultado"), resposta.get("erros"))
            resposta = await self.navegar(pedido_reserva(url_pagina))
            links = self._links(resposta)
        if not links:
            raise ErroPlayer(f"o player não pediu nenhum vídeo ({resposta.get('status')}, {resposta.get('erros')})", resposta)
        return links, resposta

    async def abrir_raiz(self, dominio):
        """Abre a página inicial de um domínio e devolve a resposta do bot (para ver aonde ela leva)."""
        return await self.navegar({
            "url": dominio.rstrip("/") + "/", "html": False, "timeoutMs": 60000,
            "acoes": [ACAO_DESAFIO, {"tipo": "esperar", "ms": 3000}],
        })

    async def reabrir_chrome(self, motivo):
        """Pede ao bot para fechar e abrir o Chrome (ele espera o pedido da vez terminar)."""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as s:
                async with s.post(f"{self.url_bot}/v1/reabrir-chrome", json={"motivo": motivo},
                                  headers={"Authorization": f"Bearer {self.token}"}) as r:
                    if r.status != 200:
                        raise ErroBusca(f"bot respondeu HTTP {r.status} ao reabrir o Chrome: {(await r.text())[:200]}")
        except (aiohttp.ClientError, OSError, TimeoutError) as e:
            raise ErroBusca(f"não consegui pedir para reabrir o Chrome: {e}") from None
