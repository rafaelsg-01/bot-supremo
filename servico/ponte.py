"""Ponte com a extensão: WebSocket em 127.0.0.1, pedidos com resposta e fluxo de eventos.

Também serve o update.xml e o .crx da extensão, que o Chrome busca por causa da política.
"""
import asyncio
import itertools
import json
import logging
import os

from aiohttp import WSMsgType, web

from . import config

log = logging.getLogger("ponte")


class ErroExtensao(Exception):
    pass


class Ponte:
    def __init__(self):
        self.ws = None
        self.conectada = asyncio.Event()
        self.versao_extensao = None
        self._ids = itertools.count(1)
        self._pendentes = {}
        self._ouvintes = set()

    @staticmethod
    def versao_esperada():
        try:
            with open(config.ARQ_INFO_EXTENSAO) as f:
                return json.load(f).get("versao")
        except OSError:
            return None

    # --- eventos -----------------------------------------------------------

    def ouvir(self):
        """Fila que recebe todos os eventos da extensão até parar_de_ouvir()."""
        fila = asyncio.Queue()
        self._ouvintes.add(fila)
        return fila

    def parar_de_ouvir(self, fila):
        self._ouvintes.discard(fila)

    # --- pedidos -----------------------------------------------------------

    async def pedir(self, cmd, timeout=15, **args):
        if not self.ws or self.ws.closed:
            try:
                await asyncio.wait_for(self.conectada.wait(), timeout)
            except TimeoutError:
                raise ErroExtensao("a extensão não está conectada") from None
        id_ = next(self._ids)
        futuro = asyncio.get_running_loop().create_future()
        self._pendentes[id_] = futuro
        try:
            await self.ws.send_json({"id": id_, "cmd": cmd, **args})
            resposta = await asyncio.wait_for(futuro, timeout)
        except TimeoutError:
            raise ErroExtensao(f"a extensão não respondeu a '{cmd}' em {timeout}s") from None
        finally:
            self._pendentes.pop(id_, None)
        if not resposta.get("ok"):
            raise ErroExtensao(f"{cmd}: {resposta.get('erro')}")
        return resposta.get("resultado")

    # --- servidor ----------------------------------------------------------

    async def _tratar_ws(self, request):
        ws = web.WebSocketResponse(max_msg_size=64 * 1024 * 1024)
        await ws.prepare(request)
        if self.ws and not self.ws.closed:
            await self.ws.close()
        self.ws = ws
        self.conectada.set()
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                dados = json.loads(msg.data)
                if "id" in dados and "evento" not in dados:
                    futuro = self._pendentes.get(dados["id"])
                    if futuro and not futuro.done():
                        futuro.set_result(dados)
                    continue
                evento = dados.get("evento")
                if evento == "ping":
                    continue
                if evento == "ola":
                    await self._ola(ws, dados.get("versao"))
                    continue
                for fila in list(self._ouvintes):
                    fila.put_nowait(dados)
        finally:
            if self.ws is ws:
                self.ws = None
                self.conectada.clear()
                log.warning("extensão desconectou")
                for futuro in self._pendentes.values():
                    if not futuro.done():
                        futuro.set_exception(ErroExtensao("a extensão desconectou"))
        return ws

    async def _ola(self, ws, versao):
        self.versao_extensao = versao
        esperada = self.versao_esperada()
        log.info("extensão conectada (versão %s, esperada %s)", versao, esperada)
        if esperada and versao != esperada:
            log.info("pedindo para a extensão buscar a versão nova")
            await ws.send_json({"cmd": "verificarAtualizacao"})

    async def _arquivo(self, request):
        nome = request.match_info["nome"]
        tipos = {"update.xml": "text/xml", "extensao.crx": "application/x-chrome-extension"}
        if nome not in tipos:
            raise web.HTTPNotFound()
        caminho = os.path.join(config.DIR_ESTADO, nome)
        if not os.path.exists(caminho):
            raise web.HTTPNotFound()
        with open(caminho, "rb") as f:
            return web.Response(body=f.read(), content_type=tipos[nome])

    def app(self):
        app = web.Application()
        app.router.add_get("/ponte", self._tratar_ws)
        app.router.add_get("/extensao/{nome}", self._arquivo)
        return app
