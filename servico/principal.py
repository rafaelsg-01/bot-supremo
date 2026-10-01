"""Ponto de entrada: sobe a ponte da extensão, o Chrome e a API HTTP.

Uso (dentro do container, como o usuário "pessoa"): python3 -m servico.principal
"""
import asyncio
import hmac
import json
import logging
import os
import signal
import time

from aiohttp import web

from . import config
from .navegador import Navegador
from .pedido import ErroValidacao, Execucao, validar
from .ponte import Ponte

log = logging.getLogger("principal")


class FilaCheia(Exception):
    pass


class Fila:
    """Um pedido por vez, na ordem de chegada. Até FILA_MAXIMA no total (rodando + esperando).

    Pedidos idênticos (mesmo corpo) que chegam enquanto um igual ainda está na fila ou rodando não
    entram de novo: esperam o mesmo resultado. Assim quem repete um pedido (ex.: a TV tentando de
    novo o mesmo episódio) não faz o site ser visitado duas vezes. Nada é guardado depois que o
    pedido termina: cache é responsabilidade de quem chama.
    """

    def __init__(self, maximo):
        self.maximo = maximo
        self.trava = asyncio.Lock()
        self.na_fila = 0
        self.em_andamento = {}
        # Pausa (painel): uma tarefa segura a trava da fila, e os pedidos esperam até ela soltar.
        self.pausa = None
        self._soltar_pausa = None
        self.pausada_ate = None  # preenchido quando a pausa já segura a trava (o pedido da vez acabou)

    def pausar(self, segundos):
        if self.pausa is not None:
            return
        soltar = asyncio.Event()

        async def segurar():
            async with self.trava:
                self.pausada_ate = time.time() + segundos
                log.warning("bot pausado por até %d min", segundos // 60)
                try:
                    await asyncio.wait_for(soltar.wait(), segundos)
                except TimeoutError:
                    log.warning("a pausa acabou sozinha")
                finally:
                    self.pausada_ate = None
            log.info("bot de volta ao normal")

        self._soltar_pausa = soltar
        self.pausa = asyncio.create_task(segurar())
        self.pausa.add_done_callback(lambda _t: setattr(self, "pausa", None))

    def despausar(self):
        if self._soltar_pausa is not None:
            self._soltar_pausa.set()

    def estado_pausa(self):
        if self.pausa is None:
            return {"ativa": False}
        return {"ativa": True, "segurando": self.pausada_ate is not None, "ate": self.pausada_ate}

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
    if not recebido and request.method == "GET":
        # Para abrir as rotas de consulta direto no navegador: /v1/tela?token=...
        recebido = request.query.get("token", "").strip()
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
        chave = json.dumps(corpo, sort_keys=True, ensure_ascii=False)
        tarefa = fila.em_andamento.get(chave)
        if tarefa is not None:
            log.info("pedido repetido enquanto o igual ainda roda; esperando o mesmo resultado: %s", pedido["url"])
        else:
            if fila.na_fila >= fila.maximo:
                return web.json_response({"ok": False, "erro": "fila cheia, tente de novo daqui a pouco"}, status=429)
            # A execução segue até o fim mesmo se quem pediu desistir, para a aba não ficar pela metade.
            chegada = time.monotonic()
            tarefa = asyncio.create_task(fila.executar(lambda: Execucao(pedido, ponte, navegador, chegada).rodar()))
            fila.em_andamento[chave] = tarefa
            tarefa.add_done_callback(lambda _t: fila.em_andamento.pop(chave, None))
        # A Cloudflare corta (524) a resposta que não começa em ~120 s, e um pedido que espera na
        # fila passa disso fácil. Por isso o cabeçalho sai já, e um espaço a cada 20 s mantém a
        # conexão viva até o JSON ficar pronto (espaço antes do JSON é válido para qualquer parser).
        resposta = web.StreamResponse(headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            await resposta.prepare(request)
            while True:
                try:
                    resultado = await asyncio.wait_for(asyncio.shield(tarefa), config.INTERVALO_MANTER_VIVA_S)
                    break
                except TimeoutError:
                    await resposta.write(b" ")
            await resposta.write(json.dumps(resultado).encode())
            await resposta.write_eof()
        except ConnectionResetError:
            # Quem pediu desistiu. O pedido segue na fila (shield) e um pedido igual pega o resultado.
            log.info("quem pediu desconectou antes do fim: %s", pedido["url"])
        return resposta

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
            "pausa": fila.estado_pausa(),
            "extensoes": _extensoes_instaladas(),
            "tela": os.environ.get("TELA_ATIVA", "?"),
        }
        return web.json_response(dados, status=200 if dados["ok"] else 503)

    async def diagnostico(request):
        return web.json_response(await ponte.pedir("diagnostico"))

    async def tela(request):
        """Foto da tela do notebook (PNG), para depuração. Só olha: não mexe em nada."""
        proc = await asyncio.create_subprocess_exec(
            "scrot", "-o", "/tmp/tela.png", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        _, erro = await proc.communicate()
        if proc.returncode != 0:
            return web.json_response({"ok": False, "erro": erro.decode(errors="replace")}, status=500)
        with open("/tmp/tela.png", "rb") as f:
            return web.Response(body=f.read(), content_type="image/png")

    async def pausa(request):
        """Pausa o bot (os pedidos esperam na fila) para alguém mexer na tela. Volta sozinho."""
        corpo = await request.json()
        if corpo.get("pausado"):
            fila.pausar(int(corpo.get("minutos", 30)) * 60)
        else:
            fila.despausar()
        await asyncio.sleep(0.2)
        return web.json_response({"ok": True, "pausa": fila.estado_pausa()})

    async def reabrir_chrome(request):
        try:
            motivo = str((await request.json()).get("motivo") or "")
        except (ValueError, json.JSONDecodeError, AttributeError):
            motivo = ""
        log.warning("reabrindo o Chrome: %s", motivo or "a pedido do painel")
        if fila.estado_pausa().get("segurando"):
            # A pausa já segura a fila: nenhum pedido está rodando.
            await navegador.reiniciar_chrome()
        else:
            async with fila.trava:
                await navegador.reiniciar_chrome()
        return web.json_response({"ok": True})

    app = web.Application(middlewares=[autenticacao], client_max_size=1024 * 1024)
    app.router.add_post("/v1/navegar", navegar)
    app.router.add_get("/saude", saude)
    app.router.add_get("/v1/diagnostico", diagnostico)
    app.router.add_get("/v1/tela", tela)
    app.router.add_post("/v1/pausa", pausa)
    app.router.add_post("/v1/reabrir-chrome", reabrir_chrome)
    return app


def _extensoes_instaladas():
    """Versões instaladas no perfil (pasta Extensions/<id>/<versão>_0) da nossa extensão e do uBO Lite."""
    base = os.path.join(config.DIR_PERFIL, "Default", "Extensions")
    nomes = {config.ID_UBO_LITE: "uBlock Origin Lite"}
    try:
        with open(config.ARQ_INFO_EXTENSAO) as f:
            nomes[json.load(f)["id"]] = "bot-supremo"
    except (OSError, ValueError, KeyError):
        pass
    resultado = {}
    for id_ext, nome in nomes.items():
        try:
            versoes = sorted(os.listdir(os.path.join(base, id_ext)))
        except OSError:
            versoes = []
        resultado[nome] = versoes[-1].rsplit("_", 1)[0] if versoes else None
    return resultado


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

    async def vigiar_extensao():
        """Se a extensão ficar desconectada por 2 min, reabre o Chrome (ex.: um aviso travou a janela)."""
        desconectada_desde = loop.time()
        while True:
            await asyncio.sleep(15)
            if ponte.conectada.is_set():
                desconectada_desde = loop.time()
                continue
            if loop.time() - desconectada_desde >= 120:
                log.error("a extensão está desconectada há 2 min; reabrindo o Chrome")
                async with fila.trava:
                    await navegador.reiniciar_chrome()
                desconectada_desde = loop.time()

    vigia = asyncio.create_task(vigiar_extensao())
    await parar.wait()
    vigia.cancel()
    log.info("encerrando: fechando o Chrome")
    await navegador.fechar_chrome()
    await corredor_api.cleanup()
    await corredor_ponte.cleanup()


if __name__ == "__main__":
    asyncio.run(principal())
