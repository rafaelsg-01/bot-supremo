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
URL_VIDEO = os.environ.get("PAINEL_URL_VIDEO", "http://warp:8070")
VIDEO_TOKEN = os.environ.get("VIDEO_TOKEN", "")
# Os mesmos serviços, vistos de fora (Cloudflare + túnel), como a TV vê.
URL_BOT_PUBLICA = os.environ.get("PAINEL_URL_BOT_PUBLICA", "https://bot.iptv01.asia")
URL_VIDEO_PUBLICA = os.environ.get("PAINEL_URL_VIDEO_PUBLICA", "https://video.iptv01.asia")
URL_VNC = os.environ.get("PAINEL_URL_VNC", "ws://warp:6080/websockify")
IMAGEM = os.environ.get("PAINEL_IMAGEM", "ghcr.io/rafaelsg-01/bot-supremo:latest")
DIR_NOVNC = "/usr/share/novnc"

CONTAINER_BOT = "bot-supremo"
CONTAINER_VIDEO = "bot-supremo-video"
CONTAINER_TUNEL = "bot-supremo-tunel"
CONTAINER_WARP = "warp"
# Containers que aparecem no painel, na ordem, com um nome que o dono entende.
CONTAINERS = [
    ("bot-supremo", "Bot (Chrome + serviço)"),
    ("bot-supremo-video", "Vídeo (link mp4 e repasse)"),
    ("warp", "WARP (saída para a internet)"),
    ("bot-supremo-tunel", "Túnel (bot., video. e painel.iptv01.asia)"),
    ("bot-supremo-painel", "Painel (esta página)"),
]

COOKIE = "painel_sessao"
DEZ_ANOS = 10 * 365 * 24 * 3600
MAX_FALHAS = 5
BLOQUEIO_S = 15 * 60

LINHA_PEDIDO = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) INFO pedido: pedido (\S+) -> (\w+) em (\d+) ms \((\{.*?\})\)(?: erros=(.*))?$"
)

cache_publico = {"quando": 0, "dados": {}}
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


async def _saude_video(sessao):
    try:
        async with sessao.get(URL_VIDEO + "/saude", timeout=aiohttp.ClientTimeout(total=5),
                              headers={"Authorization": f"Bearer {VIDEO_TOKEN}"}) as r:
            return await r.json()
    except Exception as e:
        return {"semResposta": str(e) or type(e).__name__}


async def _publico(sessao):
    """Os endereços públicos respondem? (a cada 60 s). Pega o túnel caído mesmo com tudo bem por dentro."""
    if time.time() - cache_publico["quando"] > 60:
        async def um(url):
            try:
                inicio = time.monotonic()
                # Sem seguir redirect: o http:// do vídeo tem que responder direto (TV antiga).
                async with sessao.get(url + "/saude", timeout=aiohttp.ClientTimeout(total=10), allow_redirects=False,
                                      headers={"User-Agent": "painel-bot-supremo"}) as r:
                    await r.read()
                    ms = round((time.monotonic() - inicio) * 1000)
                    return {"ok": r.status == 200, "ms": ms, **({} if r.status == 200 else {"erro": f"HTTP {r.status}"})}
            except Exception as e:
                return {"ok": False, "erro": str(e) or type(e).__name__}
        # A TV antiga (Samsung 2012) só toca o vídeo por http:// (MANUAL, seção 12, "TV antiga").
        url_video_http = URL_VIDEO_PUBLICA.replace("https://", "http://", 1)
        bot, video, video_http = await asyncio.gather(um(URL_BOT_PUBLICA), um(URL_VIDEO_PUBLICA), um(url_video_http))
        cache_publico["dados"] = {"bot": bot, "video": video, "videoHttp": video_http}
        cache_publico["quando"] = time.time()
    return cache_publico["dados"]


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


def _alertas(notebook, bot, containers, video, publico):
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
    if "semResposta" in video:
        a.append("O serviço de vídeo não está respondendo: os filmes e episódios não tocam (tente Reiniciar o vídeo).")
    else:
        ultimo = (video.get("ultimos") or [None])[0]
        if ultimo and ultimo.get("origem") == "erro":
            if ultimo.get("reparo"):
                dica = "O Chrome já foi reaberto sozinho e não resolveu: tente Testar o site e depois Reiniciar o bot."
            else:
                dica = "Tente Testar o vídeo; se repetir, Testar o site."
            a.append(f"A última busca de vídeo deu erro ({ultimo.get('horario', '')[11:16]}): {ultimo.get('erro')}. {dica}")
        dom = video.get("dominios") or {}
        if dom.get("pulandoDominio1"):
            a.append(f"O domínio 1 ({dom.get('dominio1')}) não está funcionando; o sistema está usando o domínio 2 "
                     f"({dom.get('dominio2')}). Se o 2 estiver tocando normal, apague o domínio 1.")
    # Por dentro funciona, por fora não: é o túnel (ou a Cloudflare).
    if not (publico.get("video") or {}).get("ok", True) and "semResposta" not in video:
        a.append("O endereço do vídeo (video.iptv01.asia) não responde pela internet: clique em Reiniciar o túnel.")
    if (publico.get("video") or {}).get("ok", True) and not (publico.get("videoHttp") or {}).get("ok", True):
        a.append("O vídeo por http:// (video.iptv01.asia sem o s) não responde direto: a TV antiga para de tocar. "
                 "Se o erro for HTTP 301, desligue \"Always Use HTTPS\" para video.iptv01.asia na Cloudflare.")
    if not (publico.get("bot") or {}).get("ok", True) and "semResposta" not in bot:
        a.append("O endereço do bot (bot.iptv01.asia) não responde pela internet: clique em Reiniciar o túnel.")
    for c in containers.get("lista", []):
        if c["nome"] in (CONTAINER_BOT, CONTAINER_VIDEO, CONTAINER_WARP, CONTAINER_TUNEL) and c["estado"] != "running":
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
    bot, video, containers, iptv, publico = await asyncio.gather(
        _saude_bot(sessao), _saude_video(sessao), _containers(), _iptv(sessao), _publico(sessao))
    notebook = _notebook()
    return web.json_response({
        "agora": time.time(),
        "alertas": _alertas(notebook, bot, containers, video, publico),
        "publico": publico,
        "notebook": notebook,
        "bot": bot,
        "video": video,
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


async def _testar_video(sessao):
    """O caminho inteiro do vídeo: link do episódio usado por último (testado ou buscado de novo) e
    4 MB baixados pela internet (Cloudflare + túnel + notebook + site), como a TV faz."""
    inicio = time.monotonic()
    async with sessao.post(URL_VIDEO + "/v1/teste", headers={"Authorization": f"Bearer {VIDEO_TOKEN}"},
                           timeout=aiohttp.ClientTimeout(total=600)) as r:
        t = await r.json()
    if not t.get("ok"):
        return {"ok": False, "mensagem": f"O vídeo NÃO passou: {t.get('erro')}"}
    origem = "do cache (testado)" if t.get("origem") == "cache" else "buscado agora no site"
    link_s = (t.get("ms") or 0) / 1000
    tamanho = 4 * 1024 * 1024
    inicio_download = time.monotonic()
    try:
        async with sessao.get(t["url"], headers={"Range": f"bytes=0-{tamanho - 1}"},
                              timeout=aiohttp.ClientTimeout(total=120)) as v:
            recebidos = 0
            async for bloco in v.content.iter_chunked(65536):
                recebidos += len(bloco)
            status = v.status
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        return {"ok": False, "mensagem": f"O link está bom ({origem}), mas o vídeo não veio pela internet: {e}. Tente Reiniciar o túnel."}
    segundos = max(time.monotonic() - inicio_download, 0.001)
    mbit = recebidos * 8 / 1e6 / segundos
    if status not in (200, 206) or recebidos < tamanho // 2:
        return {"ok": False, "mensagem": f"O link está bom ({origem}), mas pela internet veio HTTP {status} com {recebidos // 1024} KB. Tente Reiniciar o túnel."}
    lento = " Está lento: tente Reiniciar o túnel." if mbit < 15 else ""
    return {"ok": True, "mensagem": f"Vídeo ok: link {origem} em {link_s:.1f} s; pela internet a {mbit:.0f} Mbit/s "
                                     f"(teste inteiro em {time.monotonic() - inicio:.0f} s).{lento}"}


async def _reiniciar_internet():
    """Reinicia o WARP e, quando ele volta, o bot e o vídeo (os dois usam a rede dele)."""
    await docker.reiniciar(CONTAINER_WARP, espera_s=10)
    inicio = time.monotonic()
    saude = "?"
    while time.monotonic() - inicio < 120:
        await asyncio.sleep(5)
        try:
            saude = ((await docker.container(CONTAINER_WARP))["State"].get("Health") or {}).get("Status", "?")
        except (ErroDocker, aiohttp.ClientError, OSError):
            continue
        if saude == "healthy":
            break
    await docker.reiniciar(CONTAINER_BOT)
    await docker.reiniciar(CONTAINER_VIDEO, espera_s=5)
    cache_publico["quando"] = 0
    if saude != "healthy":
        return {"ok": False, "mensagem": f"O WARP não ficou pronto em 2 min ({saude}). Bot e vídeo reiniciados assim mesmo. "
                                         "Se nada voltar em 5 min, Reinicie o notebook."}
    return {"ok": True, "mensagem": f"Internet (WARP) reiniciada em {time.monotonic() - inicio:.0f} s; bot e vídeo reiniciados. "
                                    "Tudo volta em uns 30 s."}


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
            # Pelo serviço de vídeo: usa o domínio da vez e troca de domínio se ele não servir.
            inicio = time.monotonic()
            async with sessao.post(URL_VIDEO + "/v1/pagina", headers={"Authorization": f"Bearer {VIDEO_TOKEN}"},
                                   json={"caminho": "/final_mapa.txt", "pedido": {"html": False, "timeoutMs": 60000}},
                                   timeout=aiohttp.ClientTimeout(total=600)) as resp:
                texto = (await resp.text()).strip()
                r = json.loads(texto) if texto.startswith("{") else {"erros": [f"HTTP {resp.status}: {texto[:200]}"]}
            s = round(time.monotonic() - inicio)
            if r.get("ok"):
                msg = (f"O site abriu normal em {r.get('dominio')} ({r.get('statusHttp')}) em {s} s, "
                       "contando a espera na fila.")
            else:
                msg = f"O site NÃO abriu: {r.get('status')} {r.get('statusHttp') or ''} {' '.join(r.get('erros') or [])}"
            return web.json_response({"ok": bool(r.get("ok")), "mensagem": msg})
        if acao == "testar-video":
            return web.json_response(await _testar_video(sessao))
        if acao == "reiniciar-tunel":
            # Esta página também chega pelo túnel: responde primeiro e reinicia logo depois.
            async def depois():
                await asyncio.sleep(1)
                try:
                    await docker.reiniciar(CONTAINER_TUNEL, espera_s=5)
                except (ErroDocker, aiohttp.ClientError, OSError) as e:
                    log.warning("falha ao reiniciar o túnel: %s", e)
                cache_publico["quando"] = 0
            tarefa = asyncio.create_task(depois())
            request.app["tarefas"].add(tarefa)
            tarefa.add_done_callback(request.app["tarefas"].discard)
            return web.json_response({"ok": True, "mensagem": "Reiniciando o túnel agora. Esta página e os vídeos ficam sem resposta "
                                                              "por uns 10 s; depois, clique em Testar o vídeo."})
        if acao == "reiniciar-video":
            await docker.reiniciar(CONTAINER_VIDEO, espera_s=5)
            return web.json_response({"ok": True, "mensagem": "Serviço de vídeo reiniciado. Volta em uns 5 s (quem assistia precisa dar play de novo)."})
        if acao == "reiniciar-internet":
            return web.json_response(await _reiniciar_internet())
        if acao == "reiniciar-notebook":
            await docker.reiniciar_notebook(IMAGEM)
            return web.json_response({"ok": True, "mensagem": "Reiniciando o notebook. Tudo volta sozinho em 3 a 5 min."})
    except (ErroDocker, aiohttp.ClientError, OSError, asyncio.TimeoutError, ValueError) as e:
        return web.json_response({"ok": False, "mensagem": f"Deu erro: {e}"})
    raise web.HTTPNotFound()


# ---------------------------------------------------------------------------
# Tela (noVNC): os arquivos saem da própria imagem; o websocket é repassado ao VNC do bot.
# ---------------------------------------------------------------------------


async def api_dominios(request):
    """Grava os domínios do site (no serviço de vídeo). Corpo: {dominio1?, dominio2?}."""
    if request.headers.get("X-Painel") != "1":
        raise web.HTTPForbidden()
    corpo = await request.json()
    dados = {k: str(corpo[k]) for k in ("dominio1", "dominio2") if k in corpo}
    log.warning("domínios gravados no painel: %s (ip %s)", dados, _ip(request))
    try:
        async with request.app["sessao"].post(URL_VIDEO + "/v1/dominios", json=dados,
                                              headers={"Authorization": f"Bearer {VIDEO_TOKEN}"},
                                              timeout=aiohttp.ClientTimeout(total=15)) as r:
            resposta = await r.json()
    except (aiohttp.ClientError, OSError, asyncio.TimeoutError, ValueError) as e:
        return web.json_response({"ok": False, "mensagem": f"O serviço de vídeo não respondeu: {e}"})
    if not resposta.get("ok"):
        return web.json_response({"ok": False, "mensagem": resposta.get("erro") or "não gravou"})
    return web.json_response({"ok": True, "mensagem": "Domínio gravado. Vale a partir do próximo pedido.", **resposta})


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
    app["tarefas"] = set()
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
    app.router.add_post("/api/dominios", api_dominios)
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
