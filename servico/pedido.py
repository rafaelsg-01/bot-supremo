"""Execução de um pedido: navegar, esperar carregar, agir, esperar a rede e ler o HTML."""
import asyncio
import logging
import math
import random
import re
import time
import unicodedata
from urllib.parse import quote, urlsplit, urlunsplit

from . import config, gancho_desafio, xdotool
from .ponte import ErroExtensao

log = logging.getLogger("pedido")

TITULOS_DESAFIO = ("just a moment", "um momento")


class ErroValidacao(ValueError):
    pass


class ErroPedido(Exception):
    pass


class ErroTempoRede(Exception):
    pass


# ---------------------------------------------------------------------------
# Validação da entrada
# ---------------------------------------------------------------------------


def validar(corpo):
    if not isinstance(corpo, dict):
        raise ErroValidacao("o corpo tem que ser um objeto JSON")
    url = corpo.get("url")
    if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https"):
        raise ErroValidacao("'url' tem que ser uma URL http(s)")

    timeout = corpo.get("timeoutMs", config.TIMEOUT_PADRAO_MS)
    if not isinstance(timeout, int) or timeout <= 0:
        raise ErroValidacao("'timeoutMs' tem que ser um inteiro positivo")
    timeout = min(timeout, config.TIMEOUT_MAXIMO_MS)

    acoes = []
    for i, acao in enumerate(corpo.get("acoes") or []):
        if not isinstance(acao, dict):
            raise ErroValidacao(f"acoes[{i}] tem que ser um objeto")
        tipo = acao.get("tipo")
        if tipo == "esperar":
            ms = acao.get("ms")
            if not isinstance(ms, int) or ms < 0:
                raise ErroValidacao(f"acoes[{i}].ms tem que ser um inteiro >= 0")
            acoes.append({"tipo": "esperar", "ms": ms})
        elif tipo == "clicar":
            seletor = acao.get("seletor")
            if not isinstance(seletor, str) or not seletor.strip():
                raise ErroValidacao(f"acoes[{i}].seletor é obrigatório")
            acoes.append({
                "tipo": "clicar",
                "seletor": seletor,
                "seExistir": bool(acao.get("seExistir", False)),
                "timeoutMs": int(acao.get("timeoutMs", 10000)),
            })
        else:
            raise ErroValidacao(f"acoes[{i}].tipo desconhecido: {tipo!r} (use 'clicar' ou 'esperar')")

    esperar_rede = None
    if corpo.get("esperarRede") is not None:
        er = corpo["esperarRede"]
        if not isinstance(er, dict) or not isinstance(er.get("padrao"), str):
            raise ErroValidacao("'esperarRede.padrao' tem que ser uma regex em texto")
        try:
            regex = re.compile(er["padrao"])
        except re.error as e:
            raise ErroValidacao(f"'esperarRede.padrao' inválido: {e}") from None
        esperar_rede = {"regex": regex, "timeoutMs": int(er.get("timeoutMs", 30000))}

    return {
        "url": url,
        "acoes": acoes,
        "esperarRede": esperar_rede,
        "html": bool(corpo.get("html", True)),
        "timeoutMs": timeout,
    }


def _url_ascii(url):
    """A URL como ela seria digitada: só ASCII (IDN no host, %XX no resto)."""
    p = urlsplit(url)
    host = p.hostname or ""
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        host = host.encode("idna").decode()
    netloc = host
    if p.port:
        netloc += f":{p.port}"
    if p.username:
        cred = quote(p.username, safe="")
        if p.password:
            cred += ":" + quote(p.password, safe="")
        netloc = f"{cred}@{netloc}"
    seguro = "/%:@!$&'()*+,;=-._~?"
    return urlunsplit((p.scheme, netloc, quote(p.path, safe=seguro), quote(p.query, safe=seguro + "="),
                       quote(p.fragment, safe=seguro + "#")))


def _eh_desafio(titulo):
    t = unicodedata.normalize("NFKC", titulo or "").strip().lower()
    return any(t.startswith(d) for d in TITULOS_DESAFIO)


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------


class Execucao:
    def __init__(self, pedido, ponte, navegador):
        self.p = pedido
        self.ponte = ponte
        self.navegador = navegador
        self.inicio = time.monotonic()
        self.aba = None
        self.titulo = ""
        self.url_atual = ""
        self.navegando = False
        self.commit = asyncio.Event()
        self.carregada = asyncio.Event()
        self.ultimo_movimento_rede = time.monotonic()
        self.pendentes = set()
        self.rede = []
        self._rede_por_id = {}
        self.casou = asyncio.Event()
        self.desafio_visto = False
        self.etapas = {}
        self.erros = []
        self.resultado_acoes = []
        self._tarefas_gancho = set()

    def _ms(self, desde):
        return round((time.monotonic() - desde) * 1000)

    async def rodar(self):
        fila = self.ponte.ouvir()
        consumidor = asyncio.create_task(self._consumir(fila))
        status = "concluido"
        html = None
        try:
            try:
                async with asyncio.timeout(self.p["timeoutMs"] / 1000):
                    await self._etapas()
            except TimeoutError:
                if _eh_desafio(self.titulo):
                    status = "desafio"
                    self.erros.append("a página ficou no desafio da Cloudflare até o fim do tempo do pedido")
                else:
                    status = "timeout"
                    self.erros.append(f"tempo do pedido esgotado ({self.p['timeoutMs']} ms)")
            except ErroTempoRede as e:
                status = "timeout"
                self.erros.append(str(e))
            except (ErroPedido, ErroExtensao, xdotool.ErroXdotool) as e:
                status = "erro"
                self.erros.append(str(e))
            except Exception as e:
                log.exception("erro inesperado no pedido")
                status = "erro"
                self.erros.append(f"erro inesperado: {e}")

            if self.p["html"] and self.aba is not None:
                try:
                    html = await self.ponte.pedir("lerHtml", timeout=15, aba=self.aba)
                except ErroExtensao as e:
                    self.erros.append(f"falha ao ler o HTML: {e}")
        finally:
            consumidor.cancel()
            self.ponte.parar_de_ouvir(fila)
            await self._limpar()

        duracao = self._ms(self.inicio)
        log.info("pedido %s -> %s em %d ms (%s)", self.p["url"], status, duracao, self.etapas)
        return {
            "ok": status == "concluido",
            "status": status,
            "urlFinal": self.url_atual,
            "titulo": self.titulo,
            "html": html,
            "rede": self.rede,
            "acoes": self.resultado_acoes,
            "desafio": self.desafio_visto,
            "erros": self.erros,
            "duracaoMs": duracao,
            "etapas": self.etapas,
        }

    async def _etapas(self):
        t = time.monotonic()
        preparo = await self.ponte.pedir("prepararAba")
        self.aba = preparo["aba"]
        await self._navegar()
        self.etapas["navegar"] = self._ms(t)

        t = time.monotonic()
        await self._esperar_carregar()
        self.etapas["carregar"] = self._ms(t)

        if self.p["acoes"]:
            t = time.monotonic()
            for acao in self.p["acoes"]:
                await self._acao(acao)
            self.etapas["acoes"] = self._ms(t)

        if self.p["esperarRede"]:
            t = time.monotonic()
            er = self.p["esperarRede"]
            try:
                await asyncio.wait_for(self.casou.wait(), er["timeoutMs"] / 1000)
            except TimeoutError:
                raise ErroTempoRede(
                    f"nenhuma request casou com {er['regex'].pattern!r} em {er['timeoutMs']} ms"
                ) from None
            finally:
                self.etapas["rede"] = self._ms(t)

    # --- eventos da extensão ----------------------------------------------

    async def _consumir(self, fila):
        while True:
            ev = await fila.get()
            tipo = ev.get("evento")
            aba = ev.get("aba")
            if tipo == "req":
                if aba == self.aba:
                    self.pendentes.add(ev["id"])
                    self.ultimo_movimento_rede = time.monotonic()
                # O service worker do site faz requests com aba -1: também contam.
                er = self.p["esperarRede"]
                if er and aba in (self.aba, -1) and er["regex"].search(ev["url"]):
                    item = {
                        "url": ev["url"],
                        "metodo": ev.get("metodo"),
                        "status": None,
                        "tipo": ev.get("tipo"),
                        "horario": ev.get("horario"),
                        "aba": aba,
                    }
                    self.rede.append(item)
                    self._rede_por_id[ev["id"]] = item
                    self.casou.set()
            elif tipo == "reqFim":
                if ev["id"] in self.pendentes:
                    self.pendentes.discard(ev["id"])
                    self.ultimo_movimento_rede = time.monotonic()
                item = self._rede_por_id.get(ev["id"])
                if item:
                    item["status"] = ev.get("status")
                    if ev.get("erro"):
                        item["erro"] = ev["erro"]
            elif tipo == "nav" and aba == self.aba and self.navegando:
                if ev["fase"] == "commit":
                    self.url_atual = ev["url"]
                    self.carregada.clear()
                    if not self.commit.is_set():
                        log.info("navegou (transição %s %s)", ev.get("transicao"), ev.get("qualificadores"))
                    self.commit.set()
                elif ev["fase"] == "completo" and self.commit.is_set():
                    self.carregada.set()
            elif tipo == "aba" and aba == self.aba and self.navegando:
                if ev.get("titulo") is not None:
                    self.titulo = ev["titulo"]
                if ev.get("url"):
                    self.url_atual = ev["url"]

    # --- navegar ------------------------------------------------------------

    async def _navegar(self):
        """Digita a URL na barra de endereço, como uma pessoa."""
        janela = await xdotool.janela_chrome()
        if not janela:
            raise ErroPedido("janela do Chrome não encontrada")
        await xdotool.focar(janela)
        await asyncio.sleep(random.uniform(0.1, 0.25))
        await xdotool.teclas("ctrl+l")
        await asyncio.sleep(random.uniform(0.15, 0.3))
        self.navegando = True
        await xdotool.digitar(_url_ascii(self.p["url"]), config.ATRASO_DIGITACAO_MS)
        await asyncio.sleep(random.uniform(0.1, 0.25))
        # Delete apaga o "autocompletar" que a barra poderia ter sugerido do histórico.
        await xdotool.teclas("Delete")
        await xdotool.teclas("Return")
        try:
            await asyncio.wait_for(self.commit.wait(), 10)
        except TimeoutError:
            log.warning("a digitação não navegou em 10s; usando chrome.tabs como reserva")
            self.erros.append("aviso: a URL não pôde ser digitada; aberta pela extensão")
            await self.ponte.pedir("irPara", aba=self.aba, url=self.p["url"])
            await self.commit.wait()

    # --- carregar -----------------------------------------------------------

    async def _esperar_carregar(self):
        while True:
            if _eh_desafio(self.titulo):
                await self._tratar_desafio()
                continue
            try:
                await asyncio.wait_for(self.carregada.wait(), 1)
            except TimeoutError:
                continue
            limite = time.monotonic() + config.ESPERA_MAXIMA_REDE_QUIETA_MS / 1000
            while time.monotonic() < limite:
                if _eh_desafio(self.titulo) or not self.carregada.is_set():
                    break
                if time.monotonic() - self.ultimo_movimento_rede >= config.REDE_QUIETA_MS / 1000:
                    return
                await asyncio.sleep(0.2)
            else:
                return

    async def _tratar_desafio(self):
        inicio = time.monotonic()
        if not self.desafio_visto:
            self.desafio_visto = True
            log.warning("desafio da Cloudflare em %s (título %r)", self.url_atual, self.titulo)
            contexto = {"url": self.url_atual, "titulo": self.titulo, "aba": self.aba, "horario": time.time()}
            tarefa = asyncio.create_task(asyncio.to_thread(self._chamar_gancho, contexto))
            self._tarefas_gancho.add(tarefa)
            tarefa.add_done_callback(self._tarefas_gancho.discard)
        while _eh_desafio(self.titulo):
            await asyncio.sleep(0.5)
        log.info("o desafio sumiu depois de %d ms", self._ms(inicio))

    @staticmethod
    def _chamar_gancho(contexto):
        try:
            gancho_desafio.ao_detectar_desafio(contexto)
        except Exception:
            log.exception("erro no gancho do desafio")

    # --- ações --------------------------------------------------------------

    async def _acao(self, acao):
        inicio = time.monotonic()
        registro = {"tipo": acao["tipo"]}
        if acao["tipo"] == "esperar":
            await asyncio.sleep(acao["ms"] / 1000)
            registro["resultado"] = "esperou"
        else:
            registro["seletor"] = acao["seletor"]
            registro["resultado"] = await self._clicar(acao)
        registro["ms"] = self._ms(inicio)
        self.resultado_acoes.append(registro)
        if registro["resultado"] == "nao_existia" and not acao.get("seExistir"):
            raise ErroPedido(f"o seletor {acao['seletor']!r} não apareceu em {acao['timeoutMs']} ms")

    async def _clicar(self, acao):
        limite = time.monotonic() + acao["timeoutMs"] / 1000
        rolagens = 0
        while True:
            dados = await self.ponte.pedir("localizar", aba=self.aba, seletor=acao["seletor"])
            erro_seletor = next((m["erroSeletor"] for m in dados["medidas"] if m.get("erroSeletor")), None)
            if erro_seletor:
                raise ErroPedido(f"seletor inválido {acao['seletor']!r}: {erro_seletor}")
            alvo = _ponto_na_tela(dados)
            if alvo and alvo["fora"] == 0:
                if alvo["coberto"]:
                    log.info("o elemento %r parece coberto por outro; clicando mesmo assim", acao["seletor"])
                await xdotool.clicar(alvo["x"], alvo["y"])
                await asyncio.sleep(random.uniform(0.3, 0.6))
                return "clicou"
            if alvo and rolagens < 30:
                # Fora da área visível: rola com a roda do mouse, sobre o meio da página.
                await xdotool.mover(*alvo["meio_da_pagina"])
                cliques = max(1, min(5, math.ceil(abs(alvo["fora"]) / 100)))
                await xdotool.rolar(cliques, para_baixo=alvo["fora"] > 0)
                rolagens += 1
                await asyncio.sleep(0.3)
                continue
            if time.monotonic() >= limite:
                return "nao_existia"
            await asyncio.sleep(0.4)

    # --- fim ----------------------------------------------------------------

    async def _limpar(self):
        if self.aba is not None:
            try:
                await self.ponte.pedir("limpar", aba=self.aba)
            except ErroExtensao as e:
                log.warning("falha ao limpar a aba: %s", e)
        memoria = self.navegador.memoria_mb()
        if memoria > config.LIMITE_MEMORIA_CHROME_MB:
            log.warning("Chrome usando %d MB (limite %d); reabrindo", memoria, config.LIMITE_MEMORIA_CHROME_MB)
            await self.navegador.reiniciar_chrome()


def _ponto_na_tela(dados):
    """Converte a medida da extensão (px do frame) em pixel da tela, somando iframes e janela.

    Devolve None se o elemento não está visível em nenhum frame. "fora" diz quantos px o
    centro do elemento está acima (<0) ou abaixo (>0) da área visível da aba.
    """
    medidas = {m["frame"]: m for m in dados["medidas"]}
    frames = {f["id"]: f for f in dados["frames"]}
    topo = medidas.get(0)
    if not topo:
        return None
    zoom = dados.get("zoom") or 1
    janela = dados["janela"]
    # A área da página fica encostada embaixo da janela. Acima dela ficam as abas e a barra de endereço.
    borda = max(0, (janela["w"] - topo["vw"] * zoom) / 2)
    origem_x = janela["x"] + borda
    origem_y = janela["y"] + janela["h"] - borda - topo["vh"] * zoom

    deslocamentos = {0: (0.0, 0.0)}

    def deslocamento(fid, profundidade=0):
        if fid in deslocamentos:
            return deslocamentos[fid]
        frame = frames.get(fid)
        if not frame or profundidade > 10:
            return None
        pai = frame["pai"]
        base = deslocamento(pai, profundidade + 1)
        irmaos = (medidas.get(pai) or {}).get("iframes") or []
        if base is None or not irmaos:
            return None
        escolhido = next((i for i in irmaos if i["src"] and i["src"] == frame["url"]), None)
        if escolhido is None:
            filhos = [f for f in frames.values() if f["pai"] == pai]
            if len(irmaos) == 1 or len(filhos) == len(irmaos):
                ordem = sorted(filhos, key=lambda f: f["id"])
                idx = next(i for i, f in enumerate(ordem) if f["id"] == fid)
                escolhido = irmaos[min(idx, len(irmaos) - 1)]
        if escolhido is None:
            return None
        deslocamentos[fid] = (base[0] + escolhido["x"], base[1] + escolhido["y"])
        return deslocamentos[fid]

    candidatos = sorted(
        (m for m in medidas.values() if m.get("alvo") and m["alvo"]["visivel"]),
        key=lambda m: m["frame"],
    )
    for m in candidatos:
        d = deslocamento(m["frame"])
        if d is None:
            continue
        a = m["alvo"]
        cx = d[0] + a["x"] + a["w"] * random.uniform(0.35, 0.65)
        cy = d[1] + a["y"] + a["h"] * random.uniform(0.35, 0.65)
        centro_y = d[1] + a["y"] + a["h"] / 2
        if centro_y < 0:
            fora = centro_y - topo["vh"] / 3
        elif centro_y > topo["vh"]:
            fora = centro_y - topo["vh"] * 2 / 3
        else:
            fora = 0
        return {
            "x": round(origem_x + cx * zoom),
            "y": round(origem_y + cy * zoom),
            "fora": round(fora),
            "coberto": a["coberto"],
            "meio_da_pagina": (round(origem_x + topo["vw"] * zoom / 2), round(origem_y + topo["vh"] * zoom / 2)),
        }
    return None
