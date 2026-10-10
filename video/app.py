"""Serviço de vídeo do bot-supremo (video.iptv01.asia).

O site prende o link do vídeo ao IP de quem o gerou (o WARP do notebook). Então o vídeo tem que
ser baixado daqui, e este serviço faz duas coisas:

- POST /v1/mp4 (Bearer VIDEO_TOKEN): link do vídeo de uma página. Vem do cache (SQLite, sem
  prazo) depois de testado; se o teste falhar, o link é apagado e o bot busca um novo na hora.
- GET/HEAD /proxy-rc?url=&pagina=&sig=: repassa o vídeo do site para quem pediu (TV, PC). Aceita
  qualquer URL assinada pelo iptv (HMAC com o VIDEO_TOKEN). Se o link morrer e vier a página,
  busca um link novo e continua.
- POST /v1/pagina: abre uma página do site pelo bot (listas, séries) no domínio da vez.
- GET/POST /v1/dominios: os dois domínios do site (painel). Ver dominio.py.

O domínio do site é decidido aqui (dominio.py), não pelo iptv: se ele mudar, o serviço descobre
o novo sozinho e grava no dominio2.

Roda num container à parte (mesma imagem do bot, rede do warp). Uso: python3 -m video.app
"""
import asyncio
import collections
import hashlib
import hmac
import json
import logging
import os
import time
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

from .busca import ACAO_DESAFIO, HOST_ESPERADO, Busca, ErroBusca, ErroPlayer
from .cache import Cache
from .dominio import ErroDominio, Dominios, falha_de_dominio
from .origem import ErroOrigem, Origem

log = logging.getLogger("video")

PORTA = int(os.environ.get("PORTA_VIDEO", 8070))
TOKEN = os.environ.get("VIDEO_TOKEN", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
URL_BOT = os.environ.get("VIDEO_URL_BOT", "http://127.0.0.1:8080")
ARQ_CACHE = os.environ.get("VIDEO_CACHE", "/dados/cache.sqlite")
MAX_STREAMS = int(os.environ.get("VIDEO_MAX_STREAMS", 16))
# Endereço público (túnel), para o teste do painel baixar o vídeo pela internet.
URL_PUBLICA = os.environ.get("VIDEO_URL_PUBLICA", "https://video.iptv01.asia")
# Domínio do site na primeira subida (depois vale o que está no cache: dominio1/dominio2).
RC_DOMINIO = os.environ.get("RC_DOMINIO", "https://redecanais.ae")
INTERVALO_MANTER_VIVA_S = 20
# Reparo automático: se o player não pedir o vídeo, reabre o Chrome e tenta uma vez de novo. No
# máximo um reparo a cada 10 min: se o problema for o limite do site ou o site fora, reabrir não
# resolve, e cada tentativa a mais gasta o limite do serverforms.api.
INTERVALO_REPARO_S = 600
BLOCO = 256 * 1024

# Headers da origem que seguem para quem pediu o vídeo.
REPASSAR = ("Content-Length", "Content-Range", "Accept-Ranges", "ETag", "Last-Modified")
# Diagnóstico dos repasses (painel, "Repasses de vídeo"): headers de quem pediu que não guardamos.
NAO_GUARDAR = {"cookie", "authorization", "cf-ray", "cf-visitor", "cdn-loop", "cf-warp-tag-id",
               "cf-ipcountry", "x-forwarded-for", "x-forwarded-proto", "cf-connecting-ip"}


def assinatura(url, pagina):
    """A mesma conta do iptv (function_rc.ts, urlVideoRc)."""
    return hmac.new(TOKEN.encode(), f"{url}\n{pagina}".encode(), hashlib.sha256).hexdigest()[:32]


class Servico:
    def __init__(self):
        self.cache = Cache(ARQ_CACHE)
        self.busca = Busca(URL_BOT, BOT_TOKEN)
        self.dominios = Dominios(self.cache, self.busca, RC_DOMINIO)
        self.origem = Origem(site=self.dominios.principal)
        self.em_andamento = {}  # pagina -> tarefa (um pedido por página ao mesmo tempo)
        self.historico = collections.deque(maxlen=20)
        self.repasses = collections.deque(maxlen=60)  # cada GET/HEAD do /proxy-rc (diagnóstico)
        self.streams = 0
        self.bytes_total = 0
        self.ip = None
        self.ip_desde = None
        self._trava_reparo = asyncio.Lock()
        self.ultimo_reparo = None  # time.monotonic() do último reparo automático
        self.reparos = 0
        self.ultimo_reparo_horario = None

    # --- link do vídeo ------------------------------------------------------

    def obter(self, pagina):
        """Tarefa compartilhada: pedidos iguais ao mesmo tempo esperam a mesma busca. A tarefa segue
        até o fim mesmo se quem pediu desistir, para o link novo ficar no cache."""
        tarefa = self.em_andamento.get(pagina)
        if tarefa is None:
            tarefa = asyncio.create_task(self._obter(pagina))
            self.em_andamento[pagina] = tarefa
            tarefa.add_done_callback(lambda _t: self.em_andamento.pop(pagina, None))
        return tarefa

    async def _obter(self, pagina):
        inicio = time.monotonic()
        ms = lambda: round((time.monotonic() - inicio) * 1000)  # noqa: E731
        guardado = await self.cache.ler(pagina)
        if guardado:
            ok, status = await self.origem.testar(guardado["links"][0])
            if ok:
                await self.cache.marcar_ok(pagina)
                return self._registrar(pagina, "cache", ms(), links=guardado["links"])
            log.info("mp4 %s: link do cache morreu (%s); buscando outro", pagina, status)
            await self.cache.apagar(pagina)
        reparo = None
        try:
            links, url_pagina = await self.dominios.executar(pagina, self._tentar_links)
        except (ErroPlayer, ErroDominio) as e:
            if isinstance(e, ErroDominio) and e.so_fora_do_site:
                # A página nem abriu no site: reabrir o Chrome não muda nada.
                return self._registrar(pagina, "erro", ms(), erro=str(e))
            url_pagina = await self.dominios.principal() + (pagina if pagina.startswith("/") else "/" + pagina)
            erro = e if isinstance(e, ErroPlayer) else ErroPlayer(str(e))
            try:
                links, reparo = await self._buscar_com_reparo(pagina, url_pagina, erro)
            except ErroBusca as e2:
                return self._registrar(pagina, "erro", ms(), erro=str(e2), reparo=getattr(e2, "reparo", None))
        except ErroBusca as e:
            return self._registrar(pagina, "erro", ms(), erro=str(e))
        ok, status = await self.origem.testar(links[0])
        if not ok:
            return self._registrar(pagina, "erro", ms(), erro=f"o link novo não entregou vídeo ({status})", reparo=reparo)
        await self.cache.guardar(pagina, links, url_pagina)
        return self._registrar(pagina, "novo", ms(), links=links, reparo=reparo)

    async def _tentar_links(self, url_pagina):
        """Uma tentativa num domínio: (links, resposta, falhou_por_dominio). Um player que não deu
        vídeo num site que abriu normalmente sobe como ErroPlayer (vai para o reparo)."""
        try:
            links, resposta = await self.busca.links(url_pagina)
            return links, resposta, False
        except ErroPlayer as e:
            if e.dominio:
                return e, e.resposta, True
            raise

    async def _buscar_com_reparo(self, pagina, url_pagina, erro):
        """O player não pediu o vídeo: reabre o Chrome (no máximo uma vez a cada INTERVALO_REPARO_S)
        e tenta mais uma vez. Devolve (links, reparo) ou levanta ErroBusca."""
        visto = self.reparos
        async with self._trava_reparo:
            if self.reparos != visto:
                # Outra página reabriu o Chrome enquanto esperávamos a trava: só tenta de novo.
                reparo = "tentou de novo com o Chrome recém-reaberto"
            elif self.ultimo_reparo is not None and time.monotonic() - self.ultimo_reparo < INTERVALO_REPARO_S:
                raise erro
            else:
                log.warning("mp4 %s: o player não pediu o vídeo; reabrindo o Chrome e tentando de novo (%s)", pagina, erro)
                try:
                    await self.busca.reabrir_chrome(f"o vídeo de {pagina} não veio (reparo automático)")
                finally:
                    self.reparos += 1
                    self.ultimo_reparo = time.monotonic()
                    self.ultimo_reparo_horario = time.strftime("%Y-%m-%d %H:%M:%S")
                reparo = "reabriu o Chrome"
        try:
            return (await self.busca.links(url_pagina))[0], reparo
        except ErroBusca as e:
            novo = ErroBusca(f"{e} (mesmo depois de reabrir o Chrome)")
            novo.reparo = reparo
            raise novo from None

    def _registrar(self, pagina, origem, ms, links=None, erro=None, reparo=None):
        if erro:
            log.warning("mp4 %s -> erro em %d ms: %s", pagina, ms, erro)
        else:
            log.info("mp4 %s -> %s em %d ms%s", pagina, origem, ms, f" ({reparo})" if reparo else "")
        self.historico.appendleft({"horario": time.strftime("%Y-%m-%d %H:%M:%S"), "pagina": pagina,
                                   "origem": origem, "ms": ms, **({"erro": erro} if erro else {}),
                                   **({"reparo": reparo} if reparo else {})})
        if erro:
            return {"ok": False, "origem": origem, "ms": ms, "erro": erro}
        return {"ok": True, "links": links, "origem": origem, "ms": ms}

    # --- rotas --------------------------------------------------------------

    def _autorizado(self, request):
        recebido = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        recebido = recebido or request.query.get("token", "").strip()
        return bool(TOKEN) and hmac.compare_digest(recebido, TOKEN)

    async def rota_mp4(self, request):
        if not self._autorizado(request):
            return web.json_response({"ok": False, "erro": "token inválido"}, status=401)
        try:
            corpo = await request.json()
        except (ValueError, json.JSONDecodeError):
            return web.json_response({"ok": False, "erro": "corpo não é JSON"}, status=400)
        # O 'url' que o iptv manda é ignorado: o domínio é decidido aqui (dominio.py).
        pagina = str(corpo.get("pagina") or "").strip()
        if not pagina.startswith("/"):
            return web.json_response({"ok": False, "erro": "precisa de 'pagina' (caminho começando com /)"}, status=400)
        return await self._responder_aos_poucos(request, self.obter(pagina), pagina)

    async def _responder_aos_poucos(self, request, tarefa, nome):
        # Como no bot: o cabeçalho sai já e um espaço a cada 20 s evita o 524 da Cloudflare.
        resposta = web.StreamResponse(headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            await resposta.prepare(request)
            while True:
                try:
                    resultado = await asyncio.wait_for(asyncio.shield(tarefa), INTERVALO_MANTER_VIVA_S)
                    break
                except TimeoutError:
                    await resposta.write(b" ")
            await resposta.write(json.dumps(resultado).encode())
            await resposta.write_eof()
        except (ConnectionResetError, aiohttp.ClientConnectionResetError):
            log.info("quem pediu desconectou antes do fim: %s (a busca continua)", nome)
        return resposta

    async def rota_pagina(self, request):
        """Abre uma página do site pelo bot, no domínio da vez: {caminho, pedido} -> resposta do bot
        com 'dominio'. Se o domínio não servir, tenta o outro e descobre o novo (dominio.py)."""
        if not self._autorizado(request):
            return web.json_response({"ok": False, "erro": "token inválido"}, status=401)
        try:
            corpo = await request.json()
        except (ValueError, json.JSONDecodeError):
            return web.json_response({"ok": False, "erro": "corpo não é JSON"}, status=400)
        caminho = str(corpo.get("caminho") or "").strip()
        pedido = corpo.get("pedido") or {}
        if not caminho.startswith("/") or not isinstance(pedido, dict):
            return web.json_response({"ok": False, "erro": "precisa de 'caminho' (começando com /) e 'pedido'"}, status=400)
        tarefa = asyncio.create_task(self._pagina(caminho, pedido))
        return await self._responder_aos_poucos(request, tarefa, caminho)

    async def _pagina(self, caminho, pedido):
        pedido = {"hostEsperado": HOST_ESPERADO, **pedido}
        if not any(a.get("quando") == "desafio" for a in pedido.get("acoes") or []):
            pedido["acoes"] = [ACAO_DESAFIO, *(pedido.get("acoes") or [])]

        async def tentar(url):
            resposta = await self.busca.navegar({**pedido, "url": url})
            return resposta, resposta, falha_de_dominio(resposta)

        try:
            resposta, url = await self.dominios.executar(caminho, tentar)
        except ErroDominio as e:
            resposta = e.ultimo if isinstance(e.ultimo, dict) else {"ok": False, "status": "erro", "erros": []}
            resposta = {**resposta, "ok": False, "erros": [*(resposta.get("erros") or []), str(e)]}
            url = None
        except ErroBusca as e:
            return {"ok": False, "status": "erro", "erros": [str(e)], "rede": [], "acoes": []}
        resposta["dominio"] = "/".join(url.split("/", 3)[:3]) if url else None
        return resposta

    async def rota_dominios(self, request):
        """GET: os dois domínios e a última troca. POST {dominio1?, dominio2?}: grava (painel)."""
        if not self._autorizado(request):
            return web.json_response({"ok": False, "erro": "token inválido"}, status=401)
        if request.method == "POST":
            try:
                corpo = await request.json()
                await self.dominios.gravar(corpo.get("dominio1"), corpo.get("dominio2"))
            except (ValueError, json.JSONDecodeError) as e:
                return web.json_response({"ok": False, "erro": str(e)}, status=400)
        return web.json_response({"ok": True, **await self.dominios.ler()})

    async def rota_proxy(self, request):
        url = request.query.get("url", "")
        pagina = request.query.get("pagina", "")
        if not TOKEN or not hmac.compare_digest(request.query.get("sig", ""), assinatura(url, pagina)):
            return web.Response(status=403, text="assinatura inválida")
        if not url.startswith(("https://", "http://")):
            return web.Response(status=400, text="url inválida")
        if self.streams >= MAX_STREAMS:
            return web.Response(status=503, text="muitos vídeos ao mesmo tempo", headers={"Retry-After": "10"})

        extra = {k: request.headers[k] for k in ("Range", "If-Range") if k in request.headers}
        self.streams += 1
        inicio = time.monotonic()
        enviados = 0
        origem_resp = None
        resposta = None
        # Registro do repasse, para entender clientes que não tocam (TV antiga). Fica visível no
        # painel enquanto o vídeo passa; os campos são preenchidos aos poucos.
        reg = {
            "horario": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ip": request.headers.get("CF-Connecting-IP") or request.remote,
            "metodo": request.method,
            "range": request.headers.get("Range"),
            "ua": request.headers.get("User-Agent", ""),
            "pagina": pagina,
            "cabecalhos": {k: v for k, v in request.headers.items() if k.lower() not in NAO_GUARDAR},
            "fim": "andando",
        }
        self.repasses.appendleft(reg)

        def fechar_registro(fim, erro=None):
            reg["fim"] = fim
            reg["bytes"] = enviados
            reg["ms"] = round((time.monotonic() - inicio) * 1000)
            if erro:
                reg["erro"] = erro
            log.info("repasse %s %s range=%s -> %s %s, %d bytes em %d ms, origem %s ms, ua=%r%s",
                     reg["ip"], reg["metodo"], reg["range"], reg.get("status"), fim, enviados,
                     reg["ms"], reg.get("msOrigem"), reg["ua"][:120], f" ({erro})" if erro else "")

        try:
            origem_resp = await self.origem.abrir(url, extra)
            reg["statusOrigem"] = origem_resp.status
            reg["msOrigem"] = round((time.monotonic() - inicio) * 1000)
            if origem_resp.status in (403, 404, 410) and pagina:
                # Link morreu (IP do WARP mudou, assinatura velha): pega outro e tenta uma vez.
                log.info("stream %s: origem %d; buscando link novo", pagina, origem_resp.status)
                origem_resp.release()
                origem_resp = None
                resultado = await asyncio.shield(self.obter(pagina))
                if not resultado["ok"]:
                    reg["status"] = 502
                    fechar_registro("erro", f"sem link novo: {resultado['erro']}")
                    return web.Response(status=502, text=f"sem link novo: {resultado['erro']}")
                origem_resp = await self.origem.abrir(resultado["links"][0], extra)
                reg["linkNovo"] = True
                reg["statusOrigem"] = origem_resp.status
                reg["msOrigem"] = round((time.monotonic() - inicio) * 1000)

            resposta = web.StreamResponse(status=origem_resp.status)
            for k in REPASSAR:
                if k in origem_resp.headers:
                    resposta.headers[k] = origem_resp.headers[k]
            if origem_resp.status < 300:
                resposta.headers["Content-Type"] = "video/mp4"
                resposta.headers["Content-Disposition"] = "inline"
            else:
                resposta.headers["Content-Type"] = origem_resp.headers.get("Content-Type", "text/plain")
            resposta.headers["Access-Control-Allow-Origin"] = "*"
            resposta.headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range"
            resposta.headers["Cache-Control"] = "no-store"
            reg["status"] = origem_resp.status
            reg["resposta"] = {k: v for k, v in resposta.headers.items()}
            await resposta.prepare(request)
            if request.method != "HEAD":
                async for bloco in origem_resp.content.iter_chunked(BLOCO):
                    await resposta.write(bloco)
                    if not enviados:
                        reg["ms1oBloco"] = round((time.monotonic() - inicio) * 1000)
                    enviados += len(bloco)
                await resposta.write_eof()
            fechar_registro("completo")
            return resposta
        except (ConnectionResetError, aiohttp.ClientConnectionResetError):
            # Quem assistia fechou ou pulou para outro ponto do vídeo: normal.
            fechar_registro("cliente fechou")
            return resposta or web.Response(status=499)
        except asyncio.CancelledError:
            fechar_registro("cancelado (cliente sumiu)")
            raise
        except (aiohttp.ClientError, OSError, TimeoutError, ErroOrigem) as e:
            log.warning("stream %s: falha na origem: %s", pagina or url[:80], e)
            fechar_registro("falha na origem", f"{type(e).__name__}: {e}")
            if resposta is not None and resposta.prepared:
                return resposta  # os cabeçalhos já saíram: só resta encerrar
            return web.Response(status=502, text=f"falha ao buscar o vídeo: {e}")
        finally:
            if origem_resp is not None:
                origem_resp.close()
            self.streams -= 1
            self.bytes_total += enviados
            if enviados > 1024 * 1024:
                log.info("stream %s: %.1f MB em %.0f s", pagina or "-", enviados / 1048576, time.monotonic() - inicio)

    async def rota_teste(self, request):
        """Para o botão "Testar o vídeo" do painel: o link do episódio usado por último (testado ou
        buscado de novo) e a URL pública assinada, para o painel baixar um pedaço pela internet."""
        if not self._autorizado(request):
            return web.json_response({"ok": False, "erro": "token inválido"}, status=401)
        pagina = await self.cache.mais_recente()
        if not pagina:
            return web.json_response({"ok": False, "erro": "nenhum vídeo guardado ainda: abra um filme ou episódio na TV primeiro"})
        resultado = await asyncio.shield(self.obter(pagina))
        if resultado["ok"]:
            url = resultado["links"][0]
            resultado["url"] = f"{URL_PUBLICA}/proxy-rc?" + urlencode({"url": url, "pagina": pagina, "sig": assinatura(url, pagina)})
        return web.json_response({**resultado, "pagina": pagina})

    async def rota_saude(self, request):
        if not self._autorizado(request):
            return web.json_response({"ok": True})
        return web.json_response({
            "ok": True,
            "streams": self.streams,
            "maxStreams": MAX_STREAMS,
            "mbEnviados": round(self.bytes_total / 1048576),
            "linksNoCache": await self.cache.total(),
            "buscando": list(self.em_andamento),
            "ipPublico": self.ip,
            "ipDesde": self.ip_desde,
            "ultimoReparo": self.ultimo_reparo_horario,
            "dominios": await self.dominios.ler(),
            "ultimos": list(self.historico),
            "repasses": list(self.repasses),
        })

    # --- IP público do WARP -------------------------------------------------

    async def vigiar_ip(self):
        """Anota o IP de saída do WARP. Se ele mudar, os links velhos morrem (e são refeitos)."""
        self.ip = await self.cache.meta("ip")
        self.ip_desde = await self.cache.meta("ip_desde")
        while True:
            try:
                ip = await self.origem.ip_publico()
                if ip and ip != self.ip:
                    log.info("IP público do WARP: %s (antes: %s)", ip, self.ip)
                    self.ip, self.ip_desde = ip, time.strftime("%Y-%m-%d %H:%M:%S")
                    await self.cache.definir_meta("ip", ip)
                    await self.cache.definir_meta("ip_desde", self.ip_desde)
            except Exception:
                log.exception("falha ao conferir o IP público")
            await asyncio.sleep(600)


def criar_app():
    servico = Servico()
    app = web.Application(client_max_size=64 * 1024)
    app.router.add_post("/v1/mp4", servico.rota_mp4)
    app.router.add_route("GET", "/proxy-rc", servico.rota_proxy)
    app.router.add_route("HEAD", "/proxy-rc", servico.rota_proxy)
    app.router.add_get("/saude", servico.rota_saude)
    app.router.add_post("/v1/teste", servico.rota_teste)
    app.router.add_post("/v1/pagina", servico.rota_pagina)
    app.router.add_get("/v1/dominios", servico.rota_dominios)
    app.router.add_post("/v1/dominios", servico.rota_dominios)

    async def ao_iniciar(_app):
        await servico.dominios.iniciar()
        _app["vigia_ip"] = asyncio.create_task(servico.vigiar_ip())

    async def ao_parar(_app):
        _app["vigia_ip"].cancel()
        await servico.origem.fechar()

    app.on_startup.append(ao_iniciar)
    app.on_cleanup.append(ao_parar)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    if not TOKEN:
        log.warning("VIDEO_TOKEN vazio: o serviço vai recusar todos os pedidos")
    web.run_app(criar_app(), port=PORTA, access_log=None, print=None)
