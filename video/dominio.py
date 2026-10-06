"""Domínio do site, que muda de tempos em tempos (.af, .press, .ae...).

Dois campos, guardados na tabela meta do SQLite e editados pelo painel:
- dominio1: só o dono escreve. Pode ficar vazio.
- dominio2: o sistema escreve sozinho quando descobre um domínio novo (e o dono também pode).

Um pedido tenta o dominio1 e, se der falha de domínio, o dominio2. Se a página funcionar mas terminar
em outro redecanais.* (06/10: .press/final_mapa.txt -> redecanais.ae/<caminho em hex>), esse outro vira
o dominio2. Se os dois falharem, abre a raiz do domínio que falhou (ex.: https://redecanais.press/), que costuma redirecionar para o novo
(06/10: .press/ -> google.com/url -> redecanais.ae/). Só aceita o novo se mudou apenas o que vem
depois do primeiro ponto (redecanais.press -> redecanais.ae), para nunca adotar google.com e afins.
"""
import asyncio
import json
import logging
import time
from urllib.parse import urlsplit

log = logging.getLogger("video.dominio")

# Depois de uma falha de domínio no dominio1, ele é pulado por este tempo (o dominio2 vai direto).
PULAR_DOMINIO1_S = 1800
ERRO_FORA_DO_SITE = "a página saiu do site"


def normalizar(texto):
    """'redecanais.ae', 'https://redecanais.ae/' -> 'https://redecanais.ae'. Vazio -> ''."""
    texto = (texto or "").strip()
    if not texto:
        return ""
    if "://" not in texto:
        texto = "https://" + texto
    p = urlsplit(texto)
    if p.scheme not in ("http", "https") or not p.hostname or "." not in p.hostname:
        raise ValueError(f"domínio inválido: {texto!r}")
    return f"{p.scheme}://{p.hostname.lower()}"


def _partes(host):
    host = (host or "").lower().removeprefix("www.")
    nome, _, resto = host.partition(".")
    return nome, resto


def candidato(dominio_antigo, url):
    """Domínio novo a partir de uma URL por onde a página passou, ou None. Só vale se o nome antes
    do primeiro ponto é o mesmo e só o resto mudou (redecanais.press -> redecanais.ae)."""
    host = urlsplit(url or "").hostname or ""
    nome_novo, resto_novo = _partes(host)
    nome_antigo, resto_antigo = _partes(urlsplit(dominio_antigo).hostname)
    if not nome_novo or nome_novo != nome_antigo or not resto_novo or resto_novo == resto_antigo:
        return None
    return f"https://{host.lower()}"


def falha_de_dominio(resposta):
    """A resposta do bot mostra que o domínio não serve mais? (saiu do site, erro HTTP do documento
    ou o elemento esperado nunca apareceu). Não conta o player que reagiu e não deu vídeo: isso é o
    limite do site por IP, e outro domínio não ajuda."""
    if any(str(e).startswith(ERRO_FORA_DO_SITE) for e in resposta.get("erros") or []):
        return True
    if (resposta.get("statusHttp") or 0) >= 400:
        return True
    return resposta.get("status") == "erro" and any(
        a.get("resultado") == "nao_existia" and a.get("quando") == "carregada" for a in resposta.get("acoes") or []
    )


def saiu_do_site(resposta):
    return any(str(e).startswith(ERRO_FORA_DO_SITE) for e in resposta.get("erros") or [])


class ErroDominio(Exception):
    """Nenhum domínio serviu, e não foi possível descobrir um novo."""

    def __init__(self, mensagem, ultimo=None, so_fora_do_site=False):
        super().__init__(mensagem)
        self.ultimo = ultimo  # o que a última tentativa devolveu (resposta do bot ou erro)
        self.so_fora_do_site = so_fora_do_site


class Dominios:
    def __init__(self, cache, busca, padrao):
        self.cache = cache
        self.busca = busca
        self.padrao = normalizar(padrao)
        self._trava = asyncio.Lock()
        self.pular_dominio1_ate = 0.0
        self.ultimo_erro = None  # {"horario", "erro"} da última vez que nada serviu

    async def iniciar(self):
        """Na primeira vez, o dominio2 vem do último domínio visto (versão antiga) ou do padrão."""
        if await self.cache.meta("dominio2") is None and await self.cache.meta("dominio1") is None:
            antigo = await self.cache.meta("dominio")
            try:
                inicial = normalizar(antigo) if antigo else self.padrao
            except ValueError:
                inicial = self.padrao
            await self.cache.definir_meta("dominio1", "")
            await self.cache.definir_meta("dominio2", inicial)
            log.info("domínios iniciados: dominio2=%s", inicial)

    async def ler(self):
        troca = await self.cache.meta("dominio_troca")
        pulando = self.pular_dominio1_ate > time.monotonic()
        return {
            "dominio1": await self.cache.meta("dominio1") or "",
            "dominio2": await self.cache.meta("dominio2") or "",
            "ultimaTroca": json.loads(troca) if troca else None,
            "pulandoDominio1": pulando,
            "pulandoDominio1Min": round((self.pular_dominio1_ate - time.monotonic()) / 60) if pulando else 0,
            "ultimoErro": self.ultimo_erro,
        }

    async def gravar(self, dominio1=None, dominio2=None):
        """Pelo painel (o dono). None = não mexe naquele campo."""
        if dominio1 is not None:
            await self.cache.definir_meta("dominio1", normalizar(dominio1))
            self.pular_dominio1_ate = 0.0
        if dominio2 is not None:
            await self.cache.definir_meta("dominio2", normalizar(dominio2))
        self.ultimo_erro = None
        log.info("domínios gravados pelo painel: %s", await self.ler())

    async def ordem(self):
        d1 = await self.cache.meta("dominio1") or ""
        d2 = await self.cache.meta("dominio2") or ""
        lista = []
        if d1 and self.pular_dominio1_ate <= time.monotonic():
            lista.append(d1)
        if d2 and d2 not in lista:
            lista.append(d2)
        if d1 and d1 not in lista:
            lista.append(d1)  # pulado, mas ainda vale como último recurso
        return lista or [self.padrao]

    async def principal(self):
        return (await self.ordem())[0]

    async def executar(self, caminho, tentar):
        """Roda tentar(url) no domínio da vez, trocando de domínio se preciso.

        tentar(url) devolve (valor, resposta_do_bot, falhou_por_dominio). Devolve (valor, url) da
        tentativa que deu certo, ou levanta ErroDominio."""
        caminho = caminho if caminho.startswith("/") else "/" + caminho
        d1 = await self.cache.meta("dominio1") or ""
        tentados = []
        falhas = []  # (dominio, resposta)
        ultimo = None
        for dominio in await self.ordem():
            if len(tentados) == 2:
                break
            valor, resposta, falhou = await tentar(dominio + caminho)
            tentados.append(dominio)
            if not falhou:
                if falhas:
                    log.info("%s: %s falhou, %s serviu", caminho, falhas[-1][0], dominio)
                await self._aprender(dominio, resposta)
                return valor, dominio + caminho
            ultimo = valor
            falhas.append((dominio, resposta))
            log.warning("%s: falha de domínio em %s (%s)", caminho, dominio, (resposta or {}).get("erros"))
            if dominio == d1:
                self.pular_dominio1_ate = time.monotonic() + PULAR_DOMINIO1_S

        novo = await self._descobrir(falhas, tentados)
        if novo:
            valor, resposta, falhou = await tentar(novo + caminho)
            if not falhou:
                await self._aprender(novo, resposta)
                return valor, novo + caminho
            ultimo = valor
            falhas.append((novo, resposta))
        so_fora = all(saiu_do_site(r or {}) for _, r in falhas)
        mensagem = (f"o domínio do site parece ter mudado e não consegui descobrir o novo (tentei "
                    f"{', '.join(d for d, _ in falhas)}): preencha o domínio no painel")
        self.ultimo_erro = {"horario": time.strftime("%Y-%m-%d %H:%M:%S"), "erro": mensagem}
        raise ErroDominio(mensagem, ultimo, so_fora)

    async def _aprender(self, dominio, resposta):
        """A página funcionou, mas terminou em outro redecanais.*: o site está mandando para o domínio
        novo. Grava no dominio2 (se ainda não for ele e não for um domínio abandonado)."""
        novo = candidato(dominio, (resposta or {}).get("urlFinal"))
        if not novo:
            return
        async with self._trava:
            velhos = set(json.loads(await self.cache.meta("dominios_velhos") or "[]"))
            atuais = {await self.cache.meta("dominio1") or "", await self.cache.meta("dominio2") or ""}
            if novo in velhos or novo in atuais:
                return
            await self._trocar(dominio, novo, f"a página de {dominio} foi parar em {novo}", velhos)

    async def _descobrir(self, falhas, tentados):
        """Acha o domínio novo, grava no dominio2 e devolve. None se não achou nada novo.
        Nunca volta sozinho para um domínio que já foi abandonado (o dono pode, pelo painel)."""
        async with self._trava:
            # Outra busca pode ter trocado o dominio2 enquanto esperávamos a trava.
            d2 = await self.cache.meta("dominio2") or ""
            if d2 and d2 not in tentados:
                return d2
            velhos = set(json.loads(await self.cache.meta("dominios_velhos") or "[]"))
            proibidos = velhos | set(tentados)

            async def achar(dominio, resposta, origem):
                for url in [*((resposta or {}).get("navegacao") or []), (resposta or {}).get("urlFinal")]:
                    novo = candidato(dominio, url)
                    if novo and novo not in proibidos:
                        await self._trocar(dominio, novo, origem(url, novo), velhos)
                        return novo
                return None

            for dominio, resposta in falhas:
                novo = await achar(dominio, resposta, lambda url, _n: f"redirecionamento da página ({url[:80]})")
                if novo:
                    return novo
            for dominio, _ in reversed(falhas):
                try:
                    resposta = await self.busca.abrir_raiz(dominio)
                except Exception as e:
                    log.warning("não consegui abrir a raiz de %s: %s", dominio, e)
                    continue
                novo = await achar(dominio, resposta, lambda _u, n, d=dominio: f"a raiz de {d} levou a {n}")
                if novo:
                    return novo
                log.warning("a raiz de %s não levou a outro domínio (%s)", dominio, resposta.get("urlFinal"))
            return None

    async def _trocar(self, antigo, novo, origem, velhos):
        log.warning("DOMÍNIO NOVO: %s -> %s (%s)", antigo, novo, origem)
        await self.cache.definir_meta("dominio2", novo)
        await self.cache.definir_meta("dominios_velhos", json.dumps(sorted((velhos | {antigo}) - {novo})))
        await self.cache.definir_meta("dominio_troca", json.dumps({
            "de": antigo, "para": novo, "horario": time.strftime("%Y-%m-%d %H:%M:%S"), "origem": origem,
        }))
