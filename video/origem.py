"""Conexão com o servidor de vídeo do site, sempre pelo WARP (este container usa a rede dele).

O site prende o link do vídeo ao IP de quem o gerou (`ip=` dentro da assinatura). O Chrome do bot
gera o link pelo IPv6 do WARP, então o vídeo também tem que ser pedido pelo IPv6. Dois detalhes:

- o DNS do WARP não devolve o IPv6 de hosts com nome esquisito (`-_kerberos-...null-null.shop`),
  só o IPv4, que recebe 404. Por isso o endereço vem do DNS por HTTPS da Cloudflare quando precisa;
- o `ssl` do Python recusa esse nome, embora o certificado seja válido para `*.null-null.shop`.
  Por isso a cadeia é verificada normalmente e o nome é conferido aqui, à mão;
- desde 2026-10-09 o servidor responde 404 ("Not Found") se o pedido vier sem `Referer` (qualquer
  valor serve, mas mandamos o do domínio da vez, igual ao Chrome).
"""
import asyncio
import ipaddress
import logging
import re
import socket
import ssl
import time
from urllib.parse import unquote

import aiohttp
from aiohttp.abc import AbstractResolver
from yarl import URL

log = logging.getLogger("video.origem")

DOH = "https://cloudflare-dns.com/dns-query"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/154.0.0.0 Safari/537.36")
# Os headers que o player do site sempre mandou. Os dois primeiros o servidor não confere; o
# `Referer` (posto em `abrir`, com o domínio da vez) ele confere desde 2026-10-09.
CABECALHOS_SITE = {
    "h31ffadrg3bb7": "h31ffadrg3fj345a",
    "x-requested-with": "RC-Site-Requests",
    "User-Agent": UA,
    "Accept": "*/*",
    "Accept-Encoding": "identity",
}

RE_IP = re.compile(r"[?&]ip=([^&]+)")


class ErroOrigem(Exception):
    pass


def familia_do_link(url):
    """AF_INET6 ou AF_INET, conforme o `ip=` do link. Sem `ip=`: 0 (qualquer uma)."""
    m = RE_IP.search(url)
    if not m:
        return 0
    try:
        ip = ipaddress.ip_address(unquote(m.group(1)))
    except ValueError:
        return 0
    return socket.AF_INET6 if ip.version == 6 else socket.AF_INET


class Resolvedor(AbstractResolver):
    """Resolve só na família pedida. Tenta o DNS do sistema e, se não vier nada, o DoH."""

    def __init__(self, familia):
        self.familia = familia
        self._cache = {}
        self._doh = None

    async def _por_doh(self, host, familia):
        if self._doh is None:
            self._doh = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        tipo = "AAAA" if familia == socket.AF_INET6 else "A"
        async with self._doh.get(DOH, params={"name": host, "type": tipo},
                                 headers={"accept": "application/dns-json"}) as r:
            dados = await r.json(content_type=None)
        numero = 28 if tipo == "AAAA" else 1
        respostas = [a for a in dados.get("Answer") or [] if a.get("type") == numero]
        ttl = min([a.get("TTL", 60) for a in respostas] or [60])
        return [a["data"] for a in respostas], max(ttl, 30)

    async def _pelo_sistema(self, host, familia):
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, 443, family=familia, type=socket.SOCK_STREAM)
        except socket.gaierror:
            return []
        return list(dict.fromkeys(i[4][0] for i in infos))

    async def _enderecos(self, host, familia):
        chave = (host, familia)
        guardado = self._cache.get(chave)
        if guardado and guardado[1] > time.monotonic():
            return guardado[0]
        ips = await self._pelo_sistema(host, familia)
        ttl = 60
        if not ips:
            ips, ttl = await self._por_doh(host, familia)
        if ips:
            self._cache[chave] = (ips, time.monotonic() + ttl)
        return ips

    async def resolve(self, host, port=0, family=socket.AF_INET):
        familias = [self.familia] if self.familia else [socket.AF_INET6, socket.AF_INET]
        resultado = []
        for familia in familias:
            for ip in await self._enderecos(host, familia):
                resultado.append({"hostname": host, "host": ip, "port": port, "family": familia,
                                  "proto": 0, "flags": socket.AI_NUMERICHOST})
        if not resultado:
            raise OSError(f"sem endereço {'IPv6' if self.familia == socket.AF_INET6 else 'IPv4' if self.familia else ''} para {host}")
        return resultado

    async def close(self):
        if self._doh is not None:
            await self._doh.close()


def _contexto_tls():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False  # o nome é conferido em _conferir_nome
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def _nome_confere(host, cert):
    nomes = [v.lower() for k, v in cert.get("subjectAltName", ()) if k == "DNS"]
    host = host.lower().rstrip(".")
    for nome in nomes:
        if nome == host:
            return True
        if nome.startswith("*."):
            sufixo = nome[1:]  # ".dominio.tld"
            if host.endswith(sufixo) and "." not in host[: -len(sufixo)]:
                return True
    return False


class Origem:
    """Uma sessão por família de IP (a família vem do `ip=` de cada link)."""

    def __init__(self, site=None):
        self._sessoes = {}
        self._site = site  # async () -> 'https://redecanais.xx', o domínio da vez
        self._tls = _contexto_tls()

    def _sessao(self, familia):
        s = self._sessoes.get(familia)
        if s is None or s.closed:
            conector = aiohttp.TCPConnector(resolver=Resolvedor(familia), ssl=self._tls, limit=40,
                                            ttl_dns_cache=0, keepalive_timeout=15)
            s = aiohttp.ClientSession(
                connector=conector,
                auto_decompress=False,
                timeout=aiohttp.ClientTimeout(total=None, connect=15, sock_read=60),
            )
            self._sessoes[familia] = s
        return s

    async def abrir(self, url, extra=None):
        """GET no link, pelo WARP. Devolve a resposta aberta (quem chama fecha com release())."""
        cab = dict(CABECALHOS_SITE)
        site = await self._site() if self._site else "https://redecanais.ae"
        cab.update({"Origin": site, "Referer": site + "/"})
        cab.update(extra or {})
        sessao = self._sessao(familia_do_link(url))
        resp = await sessao.get(URL(url, encoded=True), headers=cab, allow_redirects=True, max_redirects=3)
        if resp.url.scheme == "https":
            cert = resp.connection.transport.get_extra_info("peercert") if resp.connection else None
            if cert is not None and not _nome_confere(resp.url.host, cert):
                resp.release()
                raise ErroOrigem(f"certificado não vale para {resp.url.host}")
        return resp

    async def testar(self, url, timeout_s=10):
        """O link entrega vídeo? Pede só o 1º byte. Devolve (ok, status ou erro)."""
        try:
            async with asyncio.timeout(timeout_s):
                resp = await self.abrir(url, {"Range": "bytes=0-0"})
                try:
                    return resp.status in (200, 206), resp.status
                finally:
                    resp.close()
        except (TimeoutError, aiohttp.ClientError, OSError, ErroOrigem) as e:
            return False, f"{type(e).__name__}: {e}"

    async def ip_publico(self):
        """IP de saída do WARP (o IPv6, que é o que vai nos links)."""
        try:
            # A sessão não descomprime (o vídeo passa como veio): pede sem compressão.
            async with self._sessao(socket.AF_INET6).get("https://cloudflare.com/cdn-cgi/trace",
                                                         headers={"Accept-Encoding": "identity"},
                                                         timeout=aiohttp.ClientTimeout(total=15)) as r:
                texto = await r.text()
        except (aiohttp.ClientError, OSError, TimeoutError, UnicodeDecodeError) as e:
            log.warning("não deu para ler o IP público: %s", e)
            return None
        m = re.search(r"^ip=(.+)$", texto, re.M)
        return m.group(1).strip() if m else None

    async def fechar(self):
        for s in self._sessoes.values():
            await s.close()
