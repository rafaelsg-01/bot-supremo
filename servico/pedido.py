"""Execução de um pedido: navegar, esperar carregar, agir, esperar a rede e ler o HTML."""
import asyncio
import logging
import math
import os
import random
import re
import time
import unicodedata
from urllib.parse import quote, unquote, urlsplit, urlunsplit

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
        quando = acao.get("quando", "carregada")
        if quando not in ("carregada", "desafio"):
            raise ErroValidacao(f"acoes[{i}].quando tem que ser 'carregada' ou 'desafio'")
        tipo = acao.get("tipo")
        if tipo == "esperar":
            ms = acao.get("ms")
            if not isinstance(ms, int) or ms < 0:
                raise ErroValidacao(f"acoes[{i}].ms tem que ser um inteiro >= 0")
            acoes.append({"tipo": "esperar", "ms": ms, "quando": quando})
        elif tipo == "clicar":
            seletor = acao.get("seletor")
            if not isinstance(seletor, str) or not seletor.strip():
                raise ErroValidacao(f"acoes[{i}].seletor é obrigatório")
            frame = acao.get("frame")
            if frame is not None and not isinstance(frame, str):
                raise ErroValidacao(f"acoes[{i}].frame tem que ser texto (pedaço da URL do frame)")
            frame_completo = bool(acao.get("frameCompleto", False))
            if frame_completo and not frame:
                raise ErroValidacao(f"acoes[{i}].frameCompleto exige 'frame'")
            confirmar = None
            if acao.get("confirmarRede") is not None:
                if not isinstance(acao["confirmarRede"], str):
                    raise ErroValidacao(f"acoes[{i}].confirmarRede tem que ser uma regex em texto")
                try:
                    confirmar = re.compile(acao["confirmarRede"])
                except re.error as e:
                    raise ErroValidacao(f"acoes[{i}].confirmarRede inválido: {e}") from None
            tentativas = acao.get("tentativas", 1)
            if not isinstance(tentativas, int) or not 1 <= tentativas <= 5:
                raise ErroValidacao(f"acoes[{i}].tentativas tem que ser um inteiro de 1 a 5")
            acoes.append({
                "tipo": "clicar",
                "seletor": seletor,
                "frame": frame,
                "frameCompleto": frame_completo,
                "confirmarRede": confirmar,
                "confirmarMs": int(acao.get("confirmarMs", 4000)),
                "tentativas": tentativas if confirmar else 1,
                "seExistir": bool(acao.get("seExistir", False)),
                "timeoutMs": int(acao.get("timeoutMs", 10000)),
                "quando": quando,
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

    inspecionar = corpo.get("inspecionar")
    if inspecionar is not None and not isinstance(inspecionar, str):
        raise ErroValidacao("'inspecionar' tem que ser um seletor em texto")

    # Só para medir: requests que casarem viram marcos na linha do tempo (início e fim, com status).
    marcar_rede = None
    if corpo.get("marcarRede") is not None:
        if not isinstance(corpo["marcarRede"], str):
            raise ErroValidacao("'marcarRede' tem que ser uma regex em texto")
        try:
            marcar_rede = re.compile(corpo["marcarRede"])
        except re.error as e:
            raise ErroValidacao(f"'marcarRede' inválido: {e}") from None

    esperar_pagina = corpo.get("esperarPagina", "quieta")
    if esperar_pagina not in ("quieta", "completa", "nao"):
        raise ErroValidacao("'esperarPagina' tem que ser 'quieta', 'completa' ou 'nao'")

    return {
        "url": url,
        "acoes": acoes,
        "esperarPagina": esperar_pagina,
        "inspecionar": inspecionar,
        "marcarRede": marcar_rede,
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


def _mesma_url(aberta, digitada):
    """A navegação começou no endereço digitado? (mesmo host, caminho e query)"""
    if not aberta:
        return False
    a, d = urlsplit(aberta), urlsplit(digitada)
    return ((a.hostname or "").lower() == (d.hostname or "").lower()
            and unquote(a.path or "/") == unquote(d.path or "/")
            and unquote(a.query) == unquote(d.query))


def _url_curta(url, tamanho=70):
    """Host + caminho, sem a query, para caber no log."""
    p = urlsplit(url or "")
    return ((p.hostname or "") + p.path)[:tamanho]


def _carga():
    try:
        return round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------


class Execucao:
    def __init__(self, pedido, ponte, navegador, chegada=None):
        self.p = pedido
        self.ponte = ponte
        self.navegador = navegador
        self.inicio = time.monotonic()
        # Hora em que o pedido chegou à API (antes da fila). A linha do tempo conta a partir dela.
        self.chegada = chegada or self.inicio
        self.marcos = []
        self._marcados = {}  # requests do marcarRede em andamento: id -> URL curta
        self.aba = None
        self.titulo = ""
        self.url_atual = ""
        self.navegando = False
        # saiu: a navegação começou (webNavigation ou request do documento). commit: a resposta chegou.
        self.saiu = asyncio.Event()
        self.url_saida = None  # a URL com que a navegação começou (para conferir a digitação)
        self.commit = asyncio.Event()
        self.carregada = asyncio.Event()
        self.ultimo_movimento_rede = time.monotonic()
        self.pendentes = set()
        self.rede = []
        self._rede_por_id = {}
        self._documentos = set()
        self.status_http = None
        # Requests do service worker do site (aba -1): id -> URL, e URL -> último status.
        # Quando o service worker atende a navegação, o status da página só aparece aqui.
        self._sw_urls = {}
        self._sw_status = {}
        self.casou = asyncio.Event()
        # Iframes que terminaram de carregar (onCompleted), por id: o sinal de "player pronto".
        self.frames_completos = {}
        # Confirmação do clique: {"regex", "evento", "url"} enquanto um clique espera o site reagir.
        self._confirmacao = None
        self.desafio_visto = False
        self.etapas = {}
        self.erros = []
        self.resultado_acoes = []
        self._tarefas_gancho = set()

    def _ms(self, desde):
        return round((time.monotonic() - desde) * 1000)

    def _marco(self, nome, **extra):
        """Anota na linha do tempo: ms desde a chegada do pedido, o nome e detalhes opcionais."""
        self.marcos.append({"ms": self._ms(self.chegada), "marco": nome, **extra})

    def _log_tempo(self):
        """Uma linha com a linha do tempo inteira, para achar onde o tempo vai."""
        partes = []
        frames = 0
        for m in self.marcos:
            nome = m["marco"]
            if nome.startswith("frame_"):
                frames += 1
                if frames > 12:
                    continue
            detalhe = " ".join(str(v) for k, v in m.items() if k not in ("ms", "marco"))
            partes.append(f"{nome}={m['ms']}" + (f"[{detalhe}]" if detalhe else ""))
        log.info("tempo %s carga=%s %s", self.p["url"], _carga(), " ".join(partes))

    async def rodar(self):
        self.etapas["fila"] = self._ms(self.chegada)
        self._marco("vez")
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

            self._marco("fim_etapas", status=status)
            if self.p["html"] and self.aba is not None:
                try:
                    html = await self.ponte.pedir("lerHtml", timeout=15, aba=self.aba)
                    self._marco("html_lido", kb=round(len(html or "") / 1024))
                except ErroExtensao as e:
                    self.erros.append(f"falha ao ler o HTML: {e}")
            inspecao = None
            if self.p["inspecionar"] and self.aba is not None:
                try:
                    inspecao = await self.ponte.pedir("localizar", aba=self.aba, seletor=self.p["inspecionar"])
                except ErroExtensao as e:
                    self.erros.append(f"falha ao inspecionar: {e}")
        finally:
            consumidor.cancel()
            self.ponte.parar_de_ouvir(fila)
            await self._limpar()
            self._marco("limpo")

        if self.status_http is None and self.url_atual:
            # A aba não mostrou o documento: o service worker do site buscou a página por ela.
            self.status_http = self._sw_status.get(self.url_atual.split("#", 1)[0])
        duracao = self._ms(self.inicio)
        log.info("pedido %s -> %s em %d ms (%s)%s", self.p["url"], status, duracao, self.etapas,
                 f" erros={self.erros}" if self.erros else "")
        self._log_tempo()
        extra = {"inspecao": inspecao} if self.p["inspecionar"] else {}
        return {
            **extra,
            # A página pode "carregar" e mesmo assim ser um erro do site (ex.: 522 da Cloudflare).
            "ok": status == "concluido" and not (self.status_http and self.status_http >= 400),
            "status": status,
            "statusHttp": self.status_http,
            "urlFinal": self.url_atual,
            "titulo": self.titulo,
            "html": html,
            "rede": self.rede,
            "acoes": self.resultado_acoes,
            "desafio": self.desafio_visto,
            "erros": self.erros,
            "duracaoMs": duracao,
            "etapas": self.etapas,
            "linhaDoTempo": self.marcos,
        }

    async def _etapas(self):
        t = time.monotonic()
        preparo = await self.ponte.pedir("prepararAba")
        self.aba = preparo["aba"]
        self._marco("aba_pronta")
        await self._navegar()
        self.etapas["navegar"] = self._ms(t)

        if self.p["esperarPagina"] != "nao":
            t = time.monotonic()
            await self._esperar_carregar()
            self.etapas["carregar"] = self._ms(t)

        acoes = [a for a in self.p["acoes"] if a["quando"] == "carregada"]
        if acoes:
            t = time.monotonic()
            for acao in acoes:
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
                    self.pendentes.add(ev["req"])
                    self.ultimo_movimento_rede = time.monotonic()
                    if ev.get("tipo") == "main_frame":
                        self._documentos.add(ev["req"])
                        if self.navegando:
                            self._saiu(ev["url"])
                elif aba == -1 and self.navegando:
                    self._sw_urls[ev["req"]] = ev["url"].split("#", 1)[0]
                mr = self.p["marcarRede"]
                if mr and aba in (self.aba, -1) and mr.search(ev["url"]):
                    self._marcados[ev["req"]] = _url_curta(ev["url"])
                    self._marco("req", url=self._marcados[ev["req"]])
                conf = self._confirmacao
                if conf and not conf["evento"].is_set() and aba in (self.aba, -1) and conf["regex"].search(ev["url"]):
                    conf["url"] = _url_curta(ev["url"])
                    conf["evento"].set()
                # O service worker do site faz requests com aba -1: também contam.
                er = self.p["esperarRede"]
                if er and aba in (self.aba, -1) and er["regex"].search(ev["url"]):
                    if not self.casou.is_set():
                        self._marco("casou", url=_url_curta(ev["url"]))
                    item = {
                        "url": ev["url"],
                        "metodo": ev.get("metodo"),
                        "status": None,
                        "tipo": ev.get("tipo"),
                        "horario": ev.get("horario"),
                        "aba": aba,
                    }
                    self.rede.append(item)
                    self._rede_por_id[ev["req"]] = item
                    self.casou.set()
            elif tipo == "reqFim":
                url_marcada = self._marcados.pop(ev["req"], None)
                if url_marcada:
                    self._marco("req_fim", url=url_marcada, status=ev.get("status"),
                                **({"erro": ev["erro"]} if ev.get("erro") else {}))
                if ev["req"] in self._documentos:
                    self.status_http = ev.get("status")
                url_sw = self._sw_urls.pop(ev["req"], None)
                if url_sw and ev.get("status"):
                    self._sw_status[url_sw] = ev["status"]
                if ev["req"] in self.pendentes:
                    self.pendentes.discard(ev["req"])
                    self.ultimo_movimento_rede = time.monotonic()
                item = self._rede_por_id.get(ev["req"])
                if item:
                    item["status"] = ev.get("status")
                    if ev.get("erro"):
                        item["erro"] = ev["erro"]
            elif tipo == "nav" and aba == self.aba and self.navegando:
                if ev["fase"] == "inicio":
                    self._saiu(ev.get("url"))
                elif ev["fase"] == "commit":
                    self.url_atual = ev["url"]
                    self.carregada.clear()
                    self.frames_completos.clear()
                    if not self.commit.is_set():
                        log.info("navegou (transição %s %s)", ev.get("transicao"), ev.get("qualificadores"))
                    self._marco("commit", url=_url_curta(ev["url"]))
                    self.commit.set()
                elif ev["fase"] == "completo" and self.commit.is_set():
                    self._marco("completo")
                    self.carregada.set()
            elif tipo == "navFrame" and aba == self.aba and self.navegando:
                # Iframes (anúncios, player). Um commit novo do mesmo frame desfaz o "completo".
                if ev["fase"] == "commit":
                    self.frames_completos.pop(ev.get("frame"), None)
                elif ev["fase"] == "completo":
                    self.frames_completos[ev.get("frame")] = ev.get("url") or ""
                self._marco(f"frame_{ev['fase']}", id=ev.get("frame"), url=_url_curta(ev.get("url"), 50))
            elif tipo == "aba" and aba == self.aba and self.navegando:
                if ev.get("titulo") is not None:
                    self.titulo = ev["titulo"]
                if ev.get("url"):
                    self.url_atual = ev["url"]

    def _saiu(self, url):
        if not self.saiu.is_set():
            self.url_saida = url
            self._marco("saiu")
            self.saiu.set()

    # --- navegar ------------------------------------------------------------

    async def _navegar(self):
        """Digita a URL na barra de endereço, como uma pessoa."""
        texto = _url_ascii(self.p["url"])
        for tentativa in (1, 2):
            await self._digitar_url(texto)
            # Confirma pelo INÍCIO da navegação, não pela resposta: um servidor lento demora a
            # responder, e abrir de novo pela extensão faria dois acessos.
            try:
                await asyncio.wait_for(self.saiu.wait(), 10)
            except TimeoutError:
                log.warning("a digitação não navegou em 10s; usando chrome.tabs como reserva")
                self.erros.append("aviso: a URL não pôde ser digitada; aberta pela extensão")
                await self.ponte.pedir("irPara", aba=self.aba, url=self.p["url"])
                break
            if _mesma_url(self.url_saida, texto):
                break
            # Uma tecla se perdeu (já aconteceu: "tps://..." virou uma busca no Google).
            log.warning("a digitação abriu %r em vez de %r (tentativa %d)", self.url_saida, texto, tentativa)
            self._marco("digitou_errado", url=_url_curta(self.url_saida))
            self.erros.append(f"aviso: a digitação abriu {_url_curta(self.url_saida)!r}; "
                              + ("digitando de novo" if tentativa == 1 else "aberta pela extensão"))
            # Deixa a navegação errada chegar ao fim do commit antes de trocar, para os eventos
            # dela não se misturarem com os da certa.
            try:
                await asyncio.wait_for(self.commit.wait(), 10)
            except TimeoutError:
                pass
            self.saiu.clear()
            self.commit.clear()
            self.url_saida = None
            if tentativa == 2:
                await self.ponte.pedir("irPara", aba=self.aba, url=self.p["url"])
        await self.commit.wait()

    async def _digitar_url(self, texto):
        janela = await xdotool.janela_chrome()
        if not janela:
            raise ErroPedido("janela do Chrome não encontrada")
        self._marco("achou_janela")
        precisou = await xdotool.focar(janela)
        self._marco("focou", ativou=precisou)
        await asyncio.sleep(random.uniform(0.1, 0.25))
        await xdotool.teclas("ctrl+l")
        await asyncio.sleep(random.uniform(0.15, 0.3))
        self.navegando = True
        await xdotool.digitar(texto, config.ATRASO_DIGITACAO_MS)
        self._marco("digitou", letras=len(texto))
        await asyncio.sleep(random.uniform(0.1, 0.25))
        # Delete apaga o "autocompletar" que a barra poderia ter sugerido do histórico.
        await xdotool.teclas("Delete")
        await xdotool.teclas("Return")
        self._marco("enter")

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
            if self.p["esperarPagina"] == "completa":
                return
            limite = time.monotonic() + config.ESPERA_MAXIMA_REDE_QUIETA_MS / 1000
            while time.monotonic() < limite:
                if _eh_desafio(self.titulo) or not self.carregada.is_set():
                    break
                if time.monotonic() - self.ultimo_movimento_rede >= config.REDE_QUIETA_MS / 1000:
                    self._marco("quieta", pendentes=len(self.pendentes))
                    return
                await asyncio.sleep(0.2)
            else:
                self._marco("quieta_limite", pendentes=len(self.pendentes))
                return

    async def _tratar_desafio(self):
        inicio = time.monotonic()
        self._marco("desafio_inicio")
        if not self.desafio_visto:
            self.desafio_visto = True
            log.warning("desafio da Cloudflare em %s (título %r)", self.url_atual, self.titulo)
            contexto = {"url": self.url_atual, "titulo": self.titulo, "aba": self.aba, "horario": time.time()}
            tarefa = asyncio.create_task(asyncio.to_thread(self._chamar_gancho, contexto))
            self._tarefas_gancho.add(tarefa)
            tarefa.add_done_callback(self._tarefas_gancho.discard)
        # Ações que quem pediu mandou para esta situação (ex.: clicar na caixa do Turnstile).
        # Rodam uma vez por aparição do desafio.
        for acao in (a for a in self.p["acoes"] if a["quando"] == "desafio"):
            if not _eh_desafio(self.titulo):
                break
            await self._acao(acao)
        while _eh_desafio(self.titulo):
            await asyncio.sleep(0.5)
        log.info("o desafio sumiu depois de %d ms", self._ms(inicio))
        self._marco("desafio_fim")
        self.etapas["desafio"] = self.etapas.get("desafio", 0) + self._ms(inicio)

    @staticmethod
    def _chamar_gancho(contexto):
        try:
            gancho_desafio.ao_detectar_desafio(contexto)
        except Exception:
            log.exception("erro no gancho do desafio")

    # --- ações --------------------------------------------------------------

    async def _acao(self, acao):
        inicio = time.monotonic()
        registro = {"tipo": acao["tipo"], "quando": acao["quando"]}
        if acao["tipo"] == "esperar":
            await asyncio.sleep(acao["ms"] / 1000)
            registro["resultado"] = "esperou"
            self._marco("esperou", ms_pedidos=acao["ms"])
        else:
            registro["seletor"] = acao["seletor"]
            registro["resultado"], extra = await self._clicar(acao)
            registro.update(extra)
        registro["ms"] = self._ms(inicio)
        self.resultado_acoes.append(registro)
        if registro["resultado"] == "nao_existia" and not acao.get("seExistir"):
            raise ErroPedido(f"o seletor {acao['seletor']!r} não apareceu em {acao['timeoutMs']} ms")

    async def _clicar(self, acao):
        limite = time.monotonic() + acao["timeoutMs"] / 1000
        rolagens = 0
        medicoes = 0
        cliques = 0
        conf = None
        if acao["confirmarRede"]:
            # Um objeto só para todas as tentativas: uma reação atrasada ao 1º clique também conta,
            # e impede um 2º clique (o site já está trabalhando; clicar de novo só gastaria o limite).
            conf = {"regex": acao["confirmarRede"], "evento": asyncio.Event(), "url": None}
        try:
            while True:
                if acao["quando"] == "desafio" and not _eh_desafio(self.titulo):
                    return "nao_precisou", {}
                if acao["quando"] != "desafio" and _eh_desafio(self.titulo):
                    # Com esperarPagina "nao" as ações começam antes de o desafio ser tratado.
                    await self._tratar_desafio()
                    continue
                if conf and conf["evento"].is_set():
                    return self._confirmado(conf, cliques)
                if acao["frameCompleto"] and not any(acao["frame"] in u for u in self.frames_completos.values()):
                    # O player ainda não terminou de carregar: uma pessoa ainda estaria esperando.
                    if time.monotonic() >= limite:
                        return "nao_existia", {}
                    await asyncio.sleep(0.2)
                    continue
                try:
                    medicoes += 1
                    dados = await self.ponte.pedir("localizar", aba=self.aba, seletor=acao["seletor"])
                except ErroExtensao as e:
                    # Frame sumindo no meio de uma navegação, por exemplo. Tenta de novo até o limite.
                    if time.monotonic() >= limite:
                        raise
                    log.info("falha ao medir %r (%s); tentando de novo", acao["seletor"], e)
                    await asyncio.sleep(0.5)
                    continue
                erro_seletor = next((m["erroSeletor"] for m in dados["medidas"] if m.get("erroSeletor")), None)
                if erro_seletor:
                    raise ErroPedido(f"seletor inválido {acao['seletor']!r}: {erro_seletor}")
                alvo = _ponto_na_tela(dados, acao.get("frame"))
                if alvo and alvo["fora"] == 0:
                    self._marco("achou", seletor=acao["seletor"], medicoes=medicoes, rolagens=rolagens)
                    if alvo["coberto"]:
                        log.info("o elemento %r parece coberto por outro; clicando mesmo assim", acao["seletor"])
                    if acao["frameCompleto"]:
                        # O botão pode ter acabado de aparecer: a pessoa vê e só então clica.
                        await asyncio.sleep(random.uniform(0.2, 0.6))
                    if conf and conf["evento"].is_set():
                        return self._confirmado(conf, cliques)
                    if conf:
                        self._confirmacao = conf
                    await xdotool.clicar(alvo["x"], alvo["y"])
                    cliques += 1
                    self._marco("clicou", vez=cliques)
                    if not conf:
                        await asyncio.sleep(random.uniform(0.3, 0.6))
                        return "clicou", {}
                    try:
                        await asyncio.wait_for(conf["evento"].wait(), acao["confirmarMs"] / 1000)
                        return self._confirmado(conf, cliques)
                    except TimeoutError:
                        pass
                    if cliques >= acao["tentativas"]:
                        self._marco("sem_reacao", cliques=cliques)
                        return "sem_reacao", {"cliques": cliques, "confirmado": False}
                    self._marco("clique_de_novo")
                    log.info("o site não reagiu ao clique em %r; clicando de novo", acao["seletor"])
                    medicoes = 0
                    continue
                if alvo and rolagens < 30:
                    # Fora da área visível: rola com a roda do mouse, sobre o meio da página.
                    await xdotool.mover(*alvo["meio_da_pagina"])
                    n = max(1, min(5, math.ceil(abs(alvo["fora"]) / 100)))
                    await xdotool.rolar(n, para_baixo=alvo["fora"] > 0)
                    rolagens += 1
                    await asyncio.sleep(0.3)
                    continue
                if time.monotonic() >= limite:
                    if cliques:
                        self._marco("sem_reacao", cliques=cliques)
                        return "sem_reacao", {"cliques": cliques, "confirmado": False}
                    return "nao_existia", {}
                await asyncio.sleep(0.4)
        finally:
            if self._confirmacao is conf:
                self._confirmacao = None

    def _confirmado(self, conf, cliques):
        self._marco("confirmou", url=conf["url"])
        return "clicou", {"cliques": cliques, "confirmado": True}

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


def _ponto_na_tela(dados, filtro_frame=None):
    """Converte a medida da extensão (px do frame) em pixel da tela, somando iframes e janela.

    Devolve None se o elemento não está visível em nenhum frame (ou em nenhum cuja URL contenha
    filtro_frame). "fora" diz quantos px o centro do elemento está acima (<0) ou abaixo (>0) da
    área visível da aba.
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
        url_frame = frame["url"].split("#", 1)[0]
        escolhido = next((i for i in irmaos if i["src"] and i["src"].split("#", 1)[0] == url_frame), None)
        if escolhido is None:
            # A URL do frame pode ter mudado um detalhe (parâmetro, redirecionamento): aceita o
            # iframe com o mesmo endereço sem a query, se só um tiver.
            base = url_frame.split("?", 1)[0]
            parecidos = [i for i in irmaos if i["src"] and i["src"].split("#", 1)[0].split("?", 1)[0] == base]
            if len(parecidos) == 1:
                escolhido = parecidos[0]
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
        (
            m for m in medidas.values()
            if m.get("alvo") and m["alvo"]["visivel"] and (not filtro_frame or filtro_frame in (m.get("url") or ""))
        ),
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
