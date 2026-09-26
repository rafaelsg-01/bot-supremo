"""Painel de monitoramento do bot-supremo (painel.iptv01.asia).

Uma página com login para o dono ver e consertar tudo: o notebook, o bot, os containers, os
últimos pedidos, o log e a tela do Chrome com mouse e teclado (noVNC).

Roda num container à parte (mesma imagem do bot), para continuar de pé se o bot cair.
Uso: python3 -m painel.app
"""
import asyncio
import glob
import hashlib
import hmac
import json
import logging
import os
import re
import time

import aiohttp
import psutil
from aiohttp import web

from .docker_api import Docker, ErroDocker

log = logging.getLogger("painel")

AQUI = os.path.dirname(os.path.abspath(__file__))
PORTA = int(os.environ.get("PORTA_PAINEL", 8090))
USUARIO = os.environ.get("PAINEL_USUARIO", "")
SENHA = os.environ.get("PAINEL_SENHA", "")
SEGREDO = os.environ.get("PAINEL_SEGREDO", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
VNC_SENHA = os.environ.get("VNC_SENHA", "")
URL_BOT = os.environ.get("PAINEL_URL_BOT", "http://warp:8080")
URL_VNC = os.environ.get("PAINEL_URL_VNC", "ws://warp:6080/websockify")
RC_DOMINIO = os.environ.get("RC_DOMINIO", "https://redecanais.press")
IMAGEM = os.environ.get("PAINEL_IMAGEM", "ghcr.io/rafaelsg-01/bot-supremo:latest")
DIR_NOVNC = "/usr/share/novnc"

CONTAINER_BOT = "bot-supremo"
# Containers que aparecem no painel, na ordem, com um nome que o dono entende.
CONTAINERS = [
    ("bot-supremo", "Bot (Chrome + serviço)"),
    ("warp", "WARP (saída para a internet)"),
    ("bot-supremo-tunel", "Túnel (bot. e painel.iptv01.asia)"),
    ("bot-supremo-painel", "Painel (esta página)"),
    ("content-proxy-web-01", "FlareSolverr antigo (sem uso)"),
    ("cloudflared-tunnel", "Outro túnel (serviços antigos)"),
]

COOKIE = "painel_sessao"
DEZ_ANOS = 10 * 365 * 24 * 3600
MAX_FALHAS = 5
BLOQUEIO_S = 15 * 60

LINHA_PEDIDO = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) INFO pedido: pedido (\S+) -> (\w+) em (\d+) ms \((\{.*?\})\)(?: erros=(.*))?$"
)

docker = Docker()
falhas_login = {}  # ip -> [quantidade, bloqueado_até]
cache_memoria = {"quando": 0, "dados": {}}
cache_iptv = {"quando": 0, "dados": None}


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def _chave():
    # Trocar a senha no .env desconecta quem estava logado.
    return hashlib.sha256(f"{SEGREDO}|{USUARIO}|{SENHA}".encode()).digest()


def _criar_sessao():
    expira = int(time.time()) + DEZ_ANOS
    assinatura = hmac.new(_chave(), str(expira).encode(), hashlib.sha256).hexdigest()
    return f"{expira}.{assinatura}"


def _sessao_valida(valor):
    try:
        expira, assinatura = (valor or "").split(".", 1)
        certa = hmac.new(_chave(), expira.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(assinatura, certa) and int(expira) > time.time()
    except ValueError:
        return False


def _ip(request):
    return request.headers.get("CF-Connecting-IP") or request.remote or "?"


@web.middleware
async def exigir_login(request, handler):
    if request.path in ("/entrar", "/saude") or _sessao_valida(request.cookies.get(COOKIE)):
        return await handler(request)
    if request.path.startswith("/api/") or request.path.startswith("/tela/"):
        return web.json_response({"erro": "faça login"}, status=401)
    return web.FileResponse(os.path.join(AQUI, "login.html"))


async def entrar(request):
    ip = _ip(request)
    quantidade, bloqueado_ate = falhas_login.get(ip, [0, 0])
    if bloqueado_ate > time.time():
        minutos = int((bloqueado_ate - time.time()) / 60) + 1
        raise web.HTTPFound(f"/?erro=bloqueado&min={minutos}")
    dados = await request.post()
    usuario_ok = hmac.compare_digest(str(dados.get("usuario", "")).encode(), USUARIO.encode())
    senha_ok = hmac.compare_digest(str(dados.get("senha", "")).encode(), SENHA.encode())
    if not (USUARIO and SENHA and usuario_ok and senha_ok):
        quantidade += 1
        log.warning("senha errada no painel (ip %s, %d/%d)", ip, quantidade, MAX_FALHAS)
        falhas_login[ip] = [0, time.time() + BLOQUEIO_S] if quantidade >= MAX_FALHAS else [quantidade, 0]
        raise web.HTTPFound("/?erro=senha")
    falhas_login.pop(ip, None)
    log.info("login no painel (ip %s)", ip)
    resposta = web.HTTPFound("/")
    resposta.set_cookie(COOKIE, _criar_sessao(), max_age=DEZ_ANOS, httponly=True, secure=True, samesite="Lax")
    raise resposta


async def sair(request):
    resposta = web.HTTPFound("/")
    resposta.del_cookie(COOKIE)
    raise resposta


# ---------------------------------------------------------------------------
# Dados
# ---------------------------------------------------------------------------


def _temperatura():
    maior = None
    for arq in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            with open(arq) as f:
                c = int(f.read().strip()) / 1000
            if 0 < c < 150:
                maior = c if maior is None else max(maior, c)
        except (OSError, ValueError):
            pass
    return round(maior) if maior is not None else None


def _notebook():
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disco = psutil.disk_usage("/")
    return {
        "ligadoDesde": psutil.boot_time(),
        "cpu": psutil.cpu_percent(interval=None),
        "carga": os.getloadavg(),
        "nucleos": psutil.cpu_count(),
        "ramUsadaMb": round((mem.total - mem.available) / 1048576),
        "ramTotalMb": round(mem.total / 1048576),
        "swapUsadaMb": round(swap.used / 1048576),
        "swapTotalMb": round(swap.total / 1048576),
        "discoUsadoGb": round(disco.used / 1e9, 1),
        "discoTotalGb": round(disco.total / 1e9, 1),
        "temperatura": _temperatura(),
    }


async def _saude_bot(sessao):
    try:
        async with sessao.get(URL_BOT + "/saude", timeout=aiohttp.ClientTimeout(total=5)) as r:
            return await r.json()
    except Exception as e:
        return {"semResposta": str(e) or type(e).__name__}


async def _memorias(nomes):
    if time.time() - cache_memoria["quando"] > 15:
        resultados = await asyncio.gather(*(docker.memoria_mb(n) for n in nomes), return_exceptions=True)
        cache_memoria["dados"] = {n: (r if not isinstance(r, Exception) else None) for n, r in zip(nomes, resultados)}
        cache_memoria["quando"] = time.time()
    return cache_memoria["dados"]


async def _containers():
    try:
        todos = {c["Names"][0].lstrip("/"): c for c in await docker.containers()}
    except (ErroDocker, aiohttp.ClientError, OSError) as e:
        return {"erro": f"não consegui falar com o Docker: {e}"}
    rodando = [n for n, _ in CONTAINERS if n in todos and todos[n]["State"] == "running"]
    memorias = await _memorias(rodando)
    lista = []
    for nome, descricao in CONTAINERS:
        c = todos.get(nome)
        if c is None:
            continue
        lista.append({
            "nome": nome,
            "descricao": descricao,
            "estado": c["State"],
            "status": c["Status"],
            "memoriaMb": memorias.get(nome),
        })
    versao = None
    try:
        info = await docker.container(CONTAINER_BOT)
        rotulos = info["Config"].get("Labels") or {}
        versao = {
            "commit": (rotulos.get("org.opencontainers.image.revision") or "")[:7] or None,
            "imagemCriada": rotulos.get("org.opencontainers.image.created"),
            "iniciadoEm": info["State"].get("StartedAt"),
        }
    except (ErroDocker, aiohttp.ClientError, OSError, KeyError):
        pass
    return {"lista": lista, "versaoBot": versao}


async def _iptv(sessao):
    if time.time() - cache_iptv["quando"] > 60:
        try:
            inicio = time.monotonic()
            async with sessao.get("https://iptv01.asia/login-tv", timeout=aiohttp.ClientTimeout(total=10),
                                  headers={"User-Agent": "painel-bot-supremo"}) as r:
                cache_iptv["dados"] = {"status": r.status, "ms": round((time.monotonic() - inicio) * 1000)}
        except Exception as e:
            cache_iptv["dados"] = {"erro": str(e) or type(e).__name__}
        cache_iptv["quando"] = time.time()
    return cache_iptv["dados"]


def _alertas(notebook, bot, containers):
    """Frases simples do que está errado. Lista vazia = tudo funcionando."""
    a = []
    if "semResposta" in bot:
        a.append("O bot não está respondendo (pode estar reiniciando; se passar de 3 min, clique em Reiniciar o bot).")
    else:
        if not bot.get("chrome"):
            a.append("O Chrome não está aberto.")
        if not (bot.get("extensao") or {}).get("conectada"):
            a.append("A extensão do bot está desconectada (ele reabre o Chrome sozinho em 2 min).")
        if not (bot.get("extensoes") or {}).get("uBlock Origin Lite"):
            a.append("O uBlock Origin Lite não aparece instalado.")
        if (bot.get("memoriaChromeMb") or 0) > 1100:
            a.append("O Chrome está usando muita memória.")
    for c in containers.get("lista", []):
        if c["nome"] in ("bot-supremo", "warp", "bot-supremo-tunel") and c["estado"] != "running":
            a.append(f"O container \"{c['descricao']}\" está parado.")
    if "erro" in containers:
        a.append(containers["erro"])
    if notebook["ramTotalMb"] and (notebook["ramTotalMb"] - notebook["ramUsadaMb"]) < 250:
        a.append("O notebook está com pouca memória livre.")
    if notebook["discoTotalGb"] and notebook["discoUsadoGb"] / notebook["discoTotalGb"] > 0.9:
        a.append("O disco do notebook está quase cheio.")
    if (notebook["temperatura"] or 0) >= 85:
        a.append(f"O notebook está quente ({notebook['temperatura']} °C).")
    return a


async def api_estado(request):
    sessao = request.app["sessao"]
    bot, containers, iptv = await asyncio.gather(_saude_bot(sessao), _containers(), _iptv(sessao))
    notebook = _notebook()
    return web.json_response({
        "agora": time.time(),
        "alertas": _alertas(notebook, bot, containers),
        "notebook": notebook,
        "bot": bot,
        "containers": containers,
        "iptv": iptv,
    })


async def _log_bot(linhas):
    return await docker.logs(CONTAINER_BOT, linhas)


async def api_pedidos(request):
    try:
        texto = await _log_bot(3000)
    except (ErroDocker, aiohttp.ClientError, OSError) as e:
        return web.json_response({"erro": str(e)})
    pedidos = []
    for linha in texto.splitlines():
        m = LINHA_PEDIDO.match(linha.strip())
        if m:
            hora, url, status, ms, etapas, erros = m.groups()
            pedidos.append({"hora": hora, "url": url, "status": status, "ms": int(ms), "erros": erros})
    hoje = time.strftime("%Y-%m-%d")
    de_hoje = [p for p in pedidos if p["hora"].startswith(hoje)]
    ok = [p for p in de_hoje if p["status"] == "concluido"]
    return web.json_response({
        "ultimos": pedidos[-50:][::-1],
        "hoje": {
            "total": len(de_hoje),
            "ok": len(ok),
            "falhas": len(de_hoje) - len(ok),
            "mediaMs": round(sum(p["ms"] for p in ok) / len(ok)) if ok else None,
        },
    })


async def api_log(request):
    try:
        texto = await _log_bot(300)
    except (ErroDocker, aiohttp.ClientError, OSError) as e:
        texto = f"não consegui ler o log: {e}"
    return web.Response(text=texto)


async def api_tela(request):
    """O que a página precisa para abrir a tela (a senha do VNC só vai para quem está logado)."""
    return web.json_response({"senha": VNC_SENHA})


# ---------------------------------------------------------------------------
# Ações
# ---------------------------------------------------------------------------


async def _post_bot(sessao, caminho, corpo, timeout_s=30):
    async with sessao.post(URL_BOT + caminho, json=corpo, headers={"Authorization": f"Bearer {BOT_TOKEN}"},
                           timeout=aiohttp.ClientTimeout(total=timeout_s)) as r:
        texto = (await r.text()).strip()
        return json.loads(texto) if texto else {}


async def api_acao(request):
    if request.headers.get("X-Painel") != "1":
        raise web.HTTPForbidden()
    acao = request.match_info["acao"]
    sessao = request.app["sessao"]
    log.warning("ação no painel: %s (ip %s)", acao, _ip(request))
    try:
        if acao == "pausar":
            r = await _post_bot(sessao, "/v1/pausa", {"pausado": True, "minutos": 30})
            return web.json_response({"ok": True, "mensagem": "Bot pausado por até 30 min. Pode mexer na tela.", **r})
        if acao == "despausar":
            r = await _post_bot(sessao, "/v1/pausa", {"pausado": False})
            return web.json_response({"ok": True, "mensagem": "Bot de volta ao normal.", **r})
        if acao == "reabrir-chrome":
            await _post_bot(sessao, "/v1/reabrir-chrome", {}, timeout_s=300)
            return web.json_response({"ok": True, "mensagem": "Chrome reaberto."})
        if acao == "reiniciar-bot":
            await docker.reiniciar(CONTAINER_BOT)
            return web.json_response({"ok": True, "mensagem": "Bot reiniciado. Ele volta em uns 30 s."})
        if acao == "testar-site":
            inicio = time.monotonic()
            r = await _post_bot(sessao, "/v1/navegar",
                                {"url": RC_DOMINIO + "/final_mapa.txt", "html": False, "timeoutMs": 60000},
                                timeout_s=600)
            s = round(time.monotonic() - inicio)
            if r.get("ok"):
                msg = f"O site abriu normal ({r.get('statusHttp')}) em {s} s, contando a espera na fila."
            else:
                msg = f"O site NÃO abriu: {r.get('status')} {r.get('statusHttp') or ''} {' '.join(r.get('erros') or [])}"
            return web.json_response({"ok": bool(r.get("ok")), "mensagem": msg})
        if acao == "reiniciar-notebook":
            await docker.reiniciar_notebook(IMAGEM)
            return web.json_response({"ok": True, "mensagem": "Reiniciando o notebook. Tudo volta sozinho em 3 a 5 min."})
    except (ErroDocker, aiohttp.ClientError, OSError, asyncio.TimeoutError, ValueError) as e:
        return web.json_response({"ok": False, "mensagem": f"Deu erro: {e}"})
    raise web.HTTPNotFound()


# ---------------------------------------------------------------------------
# Tela (noVNC): os arquivos saem da própria imagem; o websocket é repassado ao VNC do bot.
# ---------------------------------------------------------------------------


async def tela_websocket(request):
    ws_cliente = web.WebSocketResponse(protocols=("binary",), max_msg_size=0)
    await ws_cliente.prepare(request)
    try:
        async with request.app["sessao"].ws_connect(URL_VNC, protocols=("binary",), max_msg_size=0) as ws_vnc:
            async def repassar(de, para):
                async for msg in de:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        await para.send_bytes(msg.data)
                    elif msg.type == aiohttp.WSMsgType.TEXT:
                        await para.send_str(msg.data)
                    else:
                        break

            tarefas = [asyncio.create_task(repassar(ws_cliente, ws_vnc)), asyncio.create_task(repassar(ws_vnc, ws_cliente))]
            _, pendentes = await asyncio.wait(tarefas, return_when=asyncio.FIRST_COMPLETED)
            for t in pendentes:
                t.cancel()
    except aiohttp.ClientError as e:
        log.warning("não consegui abrir a tela: %s", e)
    await ws_cliente.close()
    return ws_cliente


# ---------------------------------------------------------------------------


async def pagina(request):
    return web.FileResponse(os.path.join(AQUI, "pagina.html"))


async def saude(request):
    return web.json_response({"ok": True})


async def ao_iniciar(app):
    app["sessao"] = aiohttp.ClientSession()
    psutil.cpu_percent(interval=None)  # a primeira leitura só serve de referência


async def ao_parar(app):
    await app["sessao"].close()
    await docker.fechar()


def criar_app():
    app = web.Application(middlewares=[exigir_login])
    app.router.add_get("/", pagina)
    app.router.add_get("/saude", saude)
    app.router.add_post("/entrar", entrar)
    app.router.add_post("/sair", sair)
    app.router.add_get("/api/estado", api_estado)
    app.router.add_get("/api/pedidos", api_pedidos)
    app.router.add_get("/api/log", api_log)
    app.router.add_get("/api/tela", api_tela)
    app.router.add_post("/api/acao/{acao}", api_acao)
    app.router.add_get("/tela/websockify", tela_websocket)
    if os.path.isdir(DIR_NOVNC):
        app.router.add_static("/tela/", DIR_NOVNC)
    app.on_startup.append(ao_iniciar)
    app.on_cleanup.append(ao_parar)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    if not (USUARIO and SENHA):
        log.error("PAINEL_USUARIO/PAINEL_SENHA vazios no .env: ninguém consegue entrar")
    if not SEGREDO:
        log.warning("PAINEL_SEGREDO vazio: as sessões usam só a senha como chave")
    web.run_app(criar_app(), port=PORTA, access_log=None)
