"""Ponto de entrada: sobe a ponte da extensão, o Chrome e a API HTTP.

Uso (dentro do container, como o usuário "pessoa"): python3 -m servico.principal
"""
import asyncio
import hmac
import json
import logging
import os
import signal

from aiohttp import web

from . import config
from .navegador import Navegador
from .pedido import ErroValidacao, Execucao, validar
from .ponte import Ponte

log = logging.getLogger("principal")


class FilaCheia(Exception):
    pass


class Fila:
    """Um pedido por vez. Até FILA_MAXIMA esperando; além disso, recusa."""

    def __init__(self, maximo):
        self.maximo = maximo
        self.trava = asyncio.Lock()
        self.na_fila = 0

    async def executar(self, fabrica):
        self.na_fila += 1
        try:
            async with self.trava:
                return await fabrica()
        finally:
            self.na_fila -= 1


@web.middleware
async def autenticacao(request, handler):
    if request.path == "/saude":
        return await handler(request)
    if not config.TOKEN:
        return web.json_response({"ok": False, "erro": "BOT_TOKEN não configurado no servidor"}, status=503)
    recebido = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(recebido.encode(), config.TOKEN.encode()):
        return web.json_response({"ok": False, "erro": "token inválido"}, status=401)
    return await handler(request)


def criar_api(ponte, navegador, fila):
    async def navegar(request):
        try:
            corpo = await request.json()
        except (ValueError, json.JSONDecodeError):
            return web.json_response({"ok": False, "erro": "corpo não é JSON"}, status=400)
        try:
            pedido = validar(corpo)
        except ErroValidacao as e:
            return web.json_response({"ok": False, "erro": str(e)}, status=400)
        if fila.na_fila >= fila.maximo:
            return web.json_response({"ok": False, "erro": "fila cheia, tente de novo daqui a pouco"}, status=429)
        # A execução segue até o fim mesmo se quem pediu desistir, para a aba não ficar pela metade.
        tarefa = asyncio.create_task(fila.executar(lambda: Execucao(pedido, ponte, navegador).rodar()))
        resposta = await asyncio.shield(tarefa)
        return web.json_response(resposta)

    async def saude(request):
        esperada = ponte.versao_esperada()
        dados = {
            "ok": navegador.vivo() and ponte.conectada.is_set(),
            "chrome": navegador.vivo(),
            "memoriaChromeMb": navegador.memoria_mb(),
            "extensao": {
                "conectada": ponte.conectada.is_set(),
                "versao": ponte.versao_extensao,
                "esperada": esperada,
            },
            "fila": {"ocupada": fila.trava.locked(), "pedidos": fila.na_fila},
            "tela": os.environ.get("TELA_ATIVA", "?"),
        }
        return web.json_response(dados, status=200 if dados["ok"] else 503)

    app = web.Application(middlewares=[autenticacao], client_max_size=1024 * 1024)
    app.router.add_post("/v1/navegar", navegar)
    app.router.add_get("/saude", saude)
    return app


async def principal():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if not config.TOKEN:
        log.warning("BOT_TOKEN vazio: a API vai recusar todos os pedidos")

    ponte = Ponte()
    navegador = Navegador()
    fila = Fila(config.FILA_MAXIMA)

    # A ponte sobe primeiro: o Chrome busca o update.xml da extensão nela ao abrir.
    corredor_ponte = web.AppRunner(ponte.app(), access_log=None)
    await corredor_ponte.setup()
    await web.TCPSite(corredor_ponte, "127.0.0.1", config.PORTA_PONTE).start()

    await navegador.iniciar()

    corredor_api = web.AppRunner(criar_api(ponte, navegador, fila), access_log=None)
    await corredor_api.setup()
    await web.TCPSite(corredor_api, "0.0.0.0", config.PORTA_API).start()
    log.info("API no ar na porta %d", config.PORTA_API)

    parar = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sinal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sinal, parar.set)

    async def avisar_se_extensao_nao_conectar():
        try:
            await asyncio.wait_for(ponte.conectada.wait(), 90)
        except TimeoutError:
            log.error("a extensão não conectou em 90s (confira a política e o chrome://extensions pelo VNC)")

    aviso = asyncio.create_task(avisar_se_extensao_nao_conectar())
    await parar.wait()
    aviso.cancel()
    log.info("encerrando: fechando o Chrome")
    await navegador.fechar_chrome()
    await corredor_api.cleanup()
    await corredor_ponte.cleanup()


if __name__ == "__main__":
    asyncio.run(principal())
