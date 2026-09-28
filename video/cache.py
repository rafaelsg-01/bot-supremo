"""Cache dos links de vídeo, num SQLite no volume do host (sobrevive a reiniciar tudo).

Não tem prazo: o link só sai daqui quando o teste antes da entrega mostra que ele morreu.
A chave é a página no site (`/serie-x-episodio-8_abc.html`), sem o domínio, que muda de tempos
em tempos. Guardamos também a URL completa usada da última vez e o último domínio visto, para
buscar um link novo quando só a página é conhecida (recuperação no meio de um vídeo).
"""
import asyncio
import json
import os
import sqlite3
import threading
import time


class Cache:
    def __init__(self, arquivo):
        os.makedirs(os.path.dirname(arquivo), exist_ok=True)
        self._db = sqlite3.connect(arquivo, check_same_thread=False, isolation_level=None)
        self._trava = threading.Lock()
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS mp4 (pagina TEXT PRIMARY KEY, links TEXT NOT NULL, url TEXT,"
            " criado REAL NOT NULL, ultimo_ok REAL, usos INTEGER NOT NULL DEFAULT 0)"
        )
        self._db.execute("CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT)")

    def _rodar(self, sql, args=()):
        with self._trava:
            return self._db.execute(sql, args).fetchall()

    async def _em_thread(self, sql, args=()):
        # O HD do notebook é lento: nada de travar o loop esperando o disco.
        return await asyncio.to_thread(self._rodar, sql, args)

    async def ler(self, pagina):
        linhas = await self._em_thread("SELECT links, url, criado FROM mp4 WHERE pagina = ?", (pagina,))
        if not linhas:
            return None
        links, url, criado = linhas[0]
        return {"links": json.loads(links), "url": url, "criado": criado}

    async def guardar(self, pagina, links, url):
        agora = time.time()
        await self._em_thread(
            "INSERT OR REPLACE INTO mp4 (pagina, links, url, criado, ultimo_ok, usos) VALUES (?, ?, ?, ?, ?, 1)",
            (pagina, json.dumps(links), url, agora, agora),
        )

    async def marcar_ok(self, pagina):
        await self._em_thread("UPDATE mp4 SET ultimo_ok = ?, usos = usos + 1 WHERE pagina = ?", (time.time(), pagina))

    async def apagar(self, pagina):
        await self._em_thread("DELETE FROM mp4 WHERE pagina = ?", (pagina,))

    async def total(self):
        return (await self._em_thread("SELECT COUNT(*) FROM mp4"))[0][0]

    async def meta(self, chave):
        linhas = await self._em_thread("SELECT valor FROM meta WHERE chave = ?", (chave,))
        return linhas[0][0] if linhas else None

    async def definir_meta(self, chave, valor):
        await self._em_thread("INSERT OR REPLACE INTO meta (chave, valor) VALUES (?, ?)", (chave, valor))
