"""Serviço do YouTube do bot-supremo (para o url.iptv01.asia, ../projeto-url).

Baixa um vídeo do YouTube em 720p (H.264 + AAC), junta vídeo e áudio num MP4 normal (não
fragmentado, que é o que a TV antiga toca) e serve o arquivo com Range. O /proxy-rc do serviço de vídeo
busca o arquivo daqui e entrega para a TV.

Por que assim (testes de 2026-10-14, MANUAL seção 12, "YouTube"):
- roda com a internet de casa (network_mode: host): pelo WARP o YouTube pede "confirme que não é um
  robô", e o link do vídeo fica preso ao IP de quem o extraiu;
- 720p com som num arquivo só não existe mais: vídeo e áudio vêm separados e o ffmpeg junta (só cópia);
- o MP4 fragmentado (que daria para tocar enquanto baixa) a TV não abre;
- baixando em pedaços de 10 MB, 4 por vez, a casa puxa ~250 Mbps (um pedaço só é limitado pelo YouTube).

Rotas (todas com Bearer VIDEO_TOKEN, menos /arquivo, que usa a chave k=):
- POST /preparar {url}: começa (ou reaproveita) e devolve o estado;
- POST /playlist {url}: a lista de uma playlist ou mix (ids, títulos, durações), sem baixar nada;
- GET /estado/<id>: na_fila | extraindo | baixando (progresso 0-100) | juntando | pronto | erro;
- GET/HEAD /arquivo/<id>.mp4?k=: o vídeo pronto, com Range;
- GET /saude.

Uso: python3 -m youtube.app
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import subprocess
import time

import aiohttp
from aiohttp import web

log = logging.getLogger("youtube")

TOKEN = os.environ.get("VIDEO_TOKEN", "")
# Gateway da rede flare-net: o serviço de vídeo (rede do warp) chega aqui, a internet não.
ESCUTA = os.environ.get("YOUTUBE_ESCUTA", "172.18.0.1")
PORTA = int(os.environ.get("YOUTUBE_PORTA", 8075))
DADOS = os.environ.get("YOUTUBE_DADOS", "/dados")
LIMITE_GB = float(os.environ.get("YOUTUBE_LIMITE_GB", 20))
DIAS = float(os.environ.get("YOUTUBE_DIAS", 7))
ALTURA = int(os.environ.get("YOUTUBE_ALTURA", 720))
YTDLP_IMAGEM = "/usr/local/bin/yt-dlp"
YTDLP = os.path.join(DADOS, "bin", "yt-dlp")  # cópia gravável, para o yt-dlp -U funcionar
PEDACO = 10 * 1024 * 1024
PARALELO = 4
# Só https (sem m3u8); H.264 e AAC, que a TV toca. Último recurso: o 360p com som (itag 18).
FORMATO = (f"bv*[vcodec^=avc1][height<={ALTURA}][ext=mp4][protocol=https]+ba[ext=m4a][protocol=https]"
           "/b[ext=mp4][vcodec^=avc1][acodec!=none][protocol=https]")
INTERVALO_ATUALIZAR_S = 6 * 3600

# Erro de bloqueio (vale tentar a próxima camada); outros erros (vídeo privado, removido) param na hora.
BLOQUEIO = re.compile(r"not a bot|Sign in|429|Too Many Requests|cookies are no longer valid|rotated", re.I)
# Camadas, na ordem: 1) com os cookies de uma conta secundária (dados/youtube/cookies.txt), como
# alguém logado; 2) sem conta, cliente web_embedded (o player embutido), que passou quando a casa foi
# barrada em 14/10. MANUAL seção 12, "Se aparecer 'não é um robô'".
CAMADAS = ("cookies", "web_embedded")

ID_VIDEO = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})")
ID_LISTA = re.compile(r"[?&]list=([A-Za-z0-9_-]+)")
# Cada ~100 itens é mais um pedido ao YouTube. O mix (RD...) logado não acaba nunca: 300 deram 18 s.
MAX_PLAYLIST = 200
MAX_MIX = 50


def id_do_video(url):
    m = ID_VIDEO.search(url or "")
    return m.group(1) if m else None


def chave_arquivo(nome):
    return hmac.new(TOKEN.encode(), f"youtube:{nome}".encode(), hashlib.sha256).hexdigest()[:20]


class ErroYoutube(Exception):
    pass


class Servico:
    def __init__(self):
        self.trabalhos = {}  # id -> estado (dict)
        self.trava = asyncio.Lock()  # um vídeo por vez (HD mecânico, Celeron)
        self.ultima_atualizacao = 0.0
        self.sessao = None
        self.cookies = "sem arquivo"  # ok | recusados em <hora> | sem arquivo
        self.ultima_camada = None

    # --- arquivos -------------------------------------------------------------

    def _arquivo(self, vid):
        return os.path.join(DADOS, f"{vid}-{ALTURA}.mp4")

    def _url_arquivo(self, vid):
        nome = f"{vid}-{ALTURA}.mp4"
        return f"http://{ESCUTA}:{PORTA}/arquivo/{nome}?k={chave_arquivo(nome)}"

    def limpar(self):
        """Apaga os vídeos mais velhos: mais de DIAS sem uso, ou acima de LIMITE_GB no total."""
        arquivos = []
        for nome in os.listdir(DADOS):
            if nome.endswith(".mp4"):
                caminho = os.path.join(DADOS, nome)
                st = os.stat(caminho)
                arquivos.append((st.st_mtime, st.st_size, caminho))
        arquivos.sort()
        total = sum(a[1] for a in arquivos)
        agora = time.time()
        for mtime, tamanho, caminho in arquivos:
            if agora - mtime > DIAS * 86400 or total > LIMITE_GB * 1024 ** 3:
                log.info("apagando %s (%d MB)", os.path.basename(caminho), tamanho // 1048576)
                os.remove(caminho)
                try:
                    os.remove(caminho[:-4] + ".json")
                except OSError:
                    pass
                total -= tamanho

    # --- trabalho -------------------------------------------------------------

    def estado(self, vid):
        if vid in self.trabalhos:
            return self.trabalhos[vid]
        if os.path.exists(self._arquivo(vid)):
            # Título e duração ficam num .json ao lado do vídeo (a fila do url.iptv01.asia mostra)
            try:
                with open(self._arquivo(vid)[:-4] + ".json") as f:
                    dados = json.load(f)
            except (OSError, ValueError):
                dados = {}
            return self._pronto(vid, {"titulo": dados.get("titulo", ""), "duracao": dados.get("duracao", 0)})
        return None

    def _pronto(self, vid, t):
        t.update(id=vid, estado="pronto", progresso=100, arquivo=self._url_arquivo(vid))
        return t

    def preparar(self, url):
        vid = id_do_video(url)
        if not vid:
            raise ErroYoutube("não achei o id do vídeo no link")
        atual = self.estado(vid)
        if atual and atual["estado"] != "erro":
            if atual["estado"] == "pronto":
                os.utime(self._arquivo(vid))  # usado agora: a limpeza começa pelos outros
            return atual
        t = {"id": vid, "estado": "na_fila", "progresso": 0, "titulo": "", "duracao": 0, "inicio": time.time()}
        self.trabalhos[vid] = t
        asyncio.create_task(self._executar(vid, t))
        return t

    async def _executar(self, vid, t):
        async with self.trava:
            try:
                await self._baixar(vid, t)
                self._pronto(vid, t)
                log.info("youtube %s pronto em %.1f s: %s", vid, time.time() - t["inicio"], t["titulo"])
            except Exception as e:
                t.update(estado="erro", erro=self._erro_curto(e))
                log.warning("youtube %s falhou: %s", vid, e)
            finally:
                shutil.rmtree(os.path.join(DADOS, "tmp", vid), ignore_errors=True)
        # O estado fica 10 min na memória para quem pergunta; depois vale o arquivo no disco.
        await asyncio.sleep(600)
        if self.trabalhos.get(vid) is t:
            del self.trabalhos[vid]

    async def _baixar(self, vid, t):
        t["estado"] = "extraindo"
        info = await self._extrair(vid)
        t["titulo"] = info.get("title") or ""
        t["duracao"] = info.get("duration") or 0
        formatos = info.get("requested_formats") or [info]
        tmp = os.path.join(DADOS, "tmp", vid)
        os.makedirs(tmp, exist_ok=True)

        tamanhos = [await self._tamanho(f) for f in formatos]
        total = sum(tamanhos)
        feito = [0]

        def andou(n):
            feito[0] += n
            t["progresso"] = min(99, int(feito[0] * 100 / total)) if total else 0

        t["estado"] = "baixando"
        partes = [os.path.join(tmp, f"p{i}") for i in range(len(formatos))]
        await asyncio.gather(*(self._baixar_em_pedacos(f, tam, p, andou) for f, tam, p in zip(formatos, tamanhos, partes)))

        t["estado"] = "juntando"
        saida = os.path.join(tmp, "saida.mp4")
        comando = ["ffmpeg", "-nostdin", "-loglevel", "error"]
        for p in partes:
            comando += ["-i", p]
        if len(partes) == 2:
            comando += ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
        # Sem +faststart: o índice fica no fim, como nos filmes do RC que a TV já toca, e o ffmpeg
        # não precisa regravar o arquivo inteiro (no HD mecânico, ~30 s a menos num vídeo de 1 h 30).
        comando += ["-c", "copy", "-y", saida]
        proc = await asyncio.create_subprocess_exec(*comando, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, erro = await proc.communicate()
        if proc.returncode != 0:
            raise ErroYoutube(f"ffmpeg: {erro.decode(errors='replace').strip()[-200:]}")
        os.replace(saida, self._arquivo(vid))
        with open(self._arquivo(vid)[:-4] + ".json", "w") as f:
            json.dump({"titulo": t["titulo"], "duracao": t["duracao"]}, f, ensure_ascii=False)
        self.limpar()

    def _erro_curto(self, e):
        """O que aparece no celular: curto, e dizendo o que fazer."""
        texto = str(e)
        if BLOQUEIO.search(texto):
            if self.cookies.startswith("recusados"):
                return "o YouTube barrou o notebook e os cookies venceram: exporte os cookies de novo (MANUAL, seção 12)"
            return "o YouTube barrou o notebook por enquanto (\"não é um robô\"); tente mais tarde"
        return texto.split(". Use --")[0][:200]

    async def _extrair(self, vid):
        """Tenta as camadas na ordem. Se todas forem barradas, atualiza o yt-dlp (no máximo a cada
        6 h) e tenta mais uma vez."""
        alvo = f"https://www.youtube.com/watch?v={vid}"
        extras = ["--no-playlist", "--remote-components", "ejs:github", "-f", FORMATO]
        try:
            return await self._camadas(vid, alvo, extras)
        except ErroYoutube as e:
            if time.time() - self.ultima_atualizacao < INTERVALO_ATUALIZAR_S:
                raise
            log.info("yt-dlp falhou (%s); atualizando e tentando de novo", e)
            await self._atualizar_ytdlp()
            return await self._camadas(vid, alvo, extras)

    async def _camadas(self, rotulo, alvo, extras):
        ultimo = None
        for camada in CAMADAS:
            if camada == "cookies" and not os.path.exists(os.path.join(DADOS, "cookies.txt")):
                self.cookies = "sem arquivo"
                continue
            try:
                info = await self._ytdlp(rotulo, alvo, extras, camada)
            except ErroYoutube as e:
                if camada == "cookies" and BLOQUEIO.search(str(e)):
                    self.cookies = "recusados em " + time.strftime("%Y-%m-%d %H:%M")
                    log.warning("youtube %s: cookies recusados (%s); tentando sem conta", rotulo, str(e).split(". Use --")[0])
                if not BLOQUEIO.search(str(e)):
                    raise
                ultimo = e
                continue
            if camada == "cookies":
                self.cookies = "ok"
            self.ultima_camada = camada
            log.info("youtube %s: extraído pela camada %s", rotulo, camada)
            return info
        raise ultimo or ErroYoutube("nenhuma camada disponível")

    async def _ytdlp(self, rotulo, alvo, extras, camada):
        programa = YTDLP if os.path.exists(YTDLP) else YTDLP_IMAGEM
        comando = [programa, "-J", *extras]
        copia = None
        if camada == "cookies":
            # O yt-dlp regrava o arquivo de cookies: usa uma cópia, para o original nunca estragar
            copia = os.path.join(DADOS, "tmp", f"cookies-{rotulo}-{time.monotonic_ns()}.txt")
            os.makedirs(os.path.dirname(copia), exist_ok=True)
            shutil.copyfile(os.path.join(DADOS, "cookies.txt"), copia)
            comando += ["--cookies", copia]
        else:
            comando += ["--extractor-args", f"youtube:player_client={camada}"]
        comando.append(alvo)
        proc = await asyncio.create_subprocess_exec(*comando, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            saida, erro = await asyncio.wait_for(proc.communicate(), 120)
        except TimeoutError:
            proc.kill()
            raise ErroYoutube("yt-dlp passou de 2 min")
        finally:
            if copia:
                try:
                    os.remove(copia)
                except OSError:
                    pass
        if proc.returncode != 0:
            linhas = [l for l in erro.decode(errors="replace").splitlines() if l.startswith("ERROR")]
            raise ErroYoutube((linhas[-1] if linhas else erro.decode(errors="replace").strip()[-200:]).removeprefix("ERROR: "))
        return json.loads(saida)

    async def playlist(self, url):
        """Lista de uma playlist (ou mix) sem extrair os vídeos: um pedido só ao YouTube. Não pega a
        trava: é rápido e não baixa nada. `inicio` é a posição do v= do link (0 se não houver)."""
        lista = ID_LISTA.search(url or "")
        if not lista:
            raise ErroYoutube("não achei o list= no link")
        vid = id_do_video(url)
        # Mix (RD...) só abre pela página de um vídeo; playlist normal, pela página da playlist
        alvo = (f"https://www.youtube.com/watch?v={vid}&list={lista.group(1)}" if vid
                else f"https://www.youtube.com/playlist?list={lista.group(1)}")
        maximo = MAX_MIX if lista.group(1).startswith("RD") else MAX_PLAYLIST
        extras = ["--flat-playlist", "--yes-playlist", "--playlist-items", f"1:{maximo}"]
        inicio_s = time.time()
        info = await self._camadas("lista-" + lista.group(1)[:20], alvo, extras)
        itens = []
        for e in info.get("entries") or []:
            titulo = e.get("title") or ""
            if not e.get("id") or titulo in ("[Private video]", "[Deleted video]"):
                continue
            itens.append({"id": e["id"], "titulo": titulo, "duracao": e.get("duration") or 0})
        inicio = next((i for i, item in enumerate(itens) if item["id"] == vid), 0)
        log.info("playlist %s: %d vídeos em %.1f s", lista.group(1), len(itens), time.time() - inicio_s)
        return {"titulo": info.get("title") or "", "itens": itens, "inicio": inicio}

    async def _atualizar_ytdlp(self):
        self.ultima_atualizacao = time.time()
        proc = await asyncio.create_subprocess_exec(YTDLP, "-U", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        saida, _ = await proc.communicate()
        log.info("yt-dlp -U: %s", saida.decode(errors="replace").strip().splitlines()[-1:])

    def _headers(self, formato):
        h = dict(formato.get("http_headers") or {})
        h.pop("Accept-Encoding", None)
        return h

    async def _tamanho(self, formato):
        if formato.get("filesize"):
            return int(formato["filesize"])
        async with self.sessao.get(formato["url"], headers={**self._headers(formato), "Range": "bytes=0-0"}) as r:
            faixa = r.headers.get("Content-Range", "")
            if "/" not in faixa:
                raise ErroYoutube(f"sem tamanho do arquivo (HTTP {r.status})")
            return int(faixa.rsplit("/", 1)[1])

    async def _baixar_em_pedacos(self, formato, tamanho, caminho, andou):
        with open(caminho, "wb") as f:
            f.truncate(tamanho)
        fila = asyncio.Queue()
        for ini in range(0, tamanho, PEDACO):
            fila.put_nowait((ini, min(ini + PEDACO, tamanho) - 1))
        headers = self._headers(formato)
        fd = os.open(caminho, os.O_WRONLY)
        try:
            async def trabalhador():
                while not fila.empty():
                    ini, fim = fila.get_nowait()
                    for tentativa in range(3):
                        try:
                            await self._pedaco(formato["url"], headers, ini, fim, fd, andou)
                            break
                        except (aiohttp.ClientError, TimeoutError, ErroYoutube) as e:
                            if tentativa == 2:
                                raise ErroYoutube(f"download falhou: {e}")
                            await asyncio.sleep(1)
            await asyncio.gather(*(trabalhador() for _ in range(PARALELO)))
        finally:
            os.close(fd)

    async def _pedaco(self, url, headers, ini, fim, fd, andou):
        pos = ini
        async with self.sessao.get(url, headers={**headers, "Range": f"bytes={ini}-{fim}"},
                                   timeout=aiohttp.ClientTimeout(total=120, sock_read=30)) as r:
            if r.status != 206:
                raise ErroYoutube(f"HTTP {r.status}")
            async for bloco in r.content.iter_chunked(256 * 1024):
                os.pwrite(fd, bloco, pos)
                pos += len(bloco)
                andou(len(bloco))
        if pos != fim + 1:
            andou(ini - pos)  # desfaz o progresso deste pedaço; ele vai ser baixado de novo
            raise ErroYoutube(f"pedaço incompleto ({pos - ini} de {fim - ini + 1} bytes)")

    # --- rotas ----------------------------------------------------------------

    def _autorizado(self, request):
        recebido = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return bool(TOKEN) and hmac.compare_digest(recebido, TOKEN)

    async def rota_preparar(self, request):
        if not self._autorizado(request):
            return web.json_response({"ok": False, "erro": "token inválido"}, status=401)
        try:
            corpo = await request.json()
            return web.json_response({"ok": True, **self.preparar(str(corpo.get("url") or ""))})
        except (ValueError, json.JSONDecodeError, AttributeError):
            return web.json_response({"ok": False, "erro": "corpo não é JSON"}, status=400)
        except ErroYoutube as e:
            return web.json_response({"ok": False, "erro": str(e)}, status=400)

    async def rota_playlist(self, request):
        if not self._autorizado(request):
            return web.json_response({"ok": False, "erro": "token inválido"}, status=401)
        try:
            corpo = await request.json()
            return web.json_response({"ok": True, **await self.playlist(str(corpo.get("url") or ""))})
        except (ValueError, json.JSONDecodeError, AttributeError):
            return web.json_response({"ok": False, "erro": "corpo não é JSON"}, status=400)
        except ErroYoutube as e:
            log.warning("playlist falhou: %s", e)
            return web.json_response({"ok": False, "erro": self._erro_curto(e)}, status=400)

    async def rota_estado(self, request):
        if not self._autorizado(request):
            return web.json_response({"ok": False, "erro": "token inválido"}, status=401)
        t = self.estado(request.match_info["id"])
        if not t:
            return web.json_response({"ok": False, "erro": "não conheço esse vídeo"}, status=404)
        return web.json_response({"ok": True, **t})

    async def rota_arquivo(self, request):
        nome = request.match_info["nome"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}-\d+\.mp4", nome) or not hmac.compare_digest(request.query.get("k", ""), chave_arquivo(nome)):
            raise web.HTTPForbidden()
        caminho = os.path.join(DADOS, nome)
        if not os.path.exists(caminho):
            raise web.HTTPNotFound()
        # FileResponse responde Range (206) e HEAD sozinho
        return web.FileResponse(caminho, headers={"Content-Type": "video/mp4", "Cache-Control": "no-store"})

    async def rota_saude(self, request):
        resposta = {"ok": True}
        if self._autorizado(request):
            uso = sum(os.path.getsize(os.path.join(DADOS, n)) for n in os.listdir(DADOS) if n.endswith(".mp4"))
            resposta.update(trabalhos=list(self.trabalhos.values()), mbGuardados=uso // 1048576,
                            cookies=self.cookies, ultimaCamada=self.ultima_camada)
        return web.json_response(resposta)


def preparar_ytdlp():
    """Copia o yt-dlp da imagem para o volume, se o de lá for mais novo (a imagem é refeita toda
    segunda). A cópia no volume é a que se atualiza sozinha (-U) entre uma imagem e outra."""
    os.makedirs(os.path.dirname(YTDLP), exist_ok=True)

    def versao(caminho):
        try:
            return subprocess.run([caminho, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    if versao(YTDLP_IMAGEM) > versao(YTDLP):
        shutil.copy2(YTDLP_IMAGEM, YTDLP)
    log.info("yt-dlp %s", versao(YTDLP))


def criar_app():
    servico = Servico()
    app = web.Application(client_max_size=64 * 1024)
    app.router.add_post("/preparar", servico.rota_preparar)
    app.router.add_post("/playlist", servico.rota_playlist)
    app.router.add_get("/estado/{id}", servico.rota_estado)
    app.router.add_route("GET", "/arquivo/{nome}", servico.rota_arquivo)
    app.router.add_get("/saude", servico.rota_saude)

    async def ao_iniciar(_app):
        servico.sessao = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=15))
        shutil.rmtree(os.path.join(DADOS, "tmp"), ignore_errors=True)
        servico.limpar()
        # Em segundo plano: só o --version do yt-dlp leva ~10 s neste Celeron
        _app["ytdlp"] = asyncio.create_task(asyncio.to_thread(preparar_ytdlp))

    async def ao_parar(_app):
        await servico.sessao.close()

    app.on_startup.append(ao_iniciar)
    app.on_cleanup.append(ao_parar)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    if not TOKEN:
        log.warning("VIDEO_TOKEN vazio: o serviço vai recusar todos os pedidos")
    os.environ.setdefault("XDG_CACHE_HOME", os.path.join(DADOS, "cache"))  # componentes do yt-dlp (EJS)
    web.run_app(criar_app(), host=ESCUTA, port=PORTA, access_log=None, print=None)
