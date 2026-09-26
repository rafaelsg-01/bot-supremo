"""Cliente mínimo da API do Docker, pelo socket unix montado no container do painel.

Só o que o painel usa: listar containers, ver um container, memória, logs, reiniciar e o
"reiniciar o notebook" (um container privilegiado de vida curta que chama o systemd do host).
"""
import json
import struct

import aiohttp

SOCKET = "/var/run/docker.sock"
BASE = "http://docker"


class ErroDocker(Exception):
    pass


class Docker:
    def __init__(self):
        self._sessao = None

    def _s(self):
        if self._sessao is None or self._sessao.closed:
            self._sessao = aiohttp.ClientSession(
                connector=aiohttp.UnixConnector(path=SOCKET), timeout=aiohttp.ClientTimeout(total=60)
            )
        return self._sessao

    async def _pedir(self, metodo, caminho, corpo=None, bruto=False):
        async with self._s().request(metodo, BASE + caminho, json=corpo) as r:
            dados = await r.read()
            if r.status >= 400:
                raise ErroDocker(f"{metodo} {caminho}: HTTP {r.status} {dados[:200].decode(errors='replace')}")
            if bruto:
                return dados
            return json.loads(dados) if dados else None

    async def containers(self):
        return await self._pedir("GET", "/containers/json?all=1")

    async def container(self, nome):
        return await self._pedir("GET", f"/containers/{nome}/json")

    async def memoria_mb(self, nome):
        s = await self._pedir("GET", f"/containers/{nome}/stats?stream=false&one-shot=true")
        m = s.get("memory_stats") or {}
        uso = m.get("usage") or 0
        # Como o "docker stats": tira o cache de arquivos (inactive_file no cgroup v2).
        uso -= (m.get("stats") or {}).get("inactive_file", 0)
        return round(uso / 1048576) if uso > 0 else None

    async def logs(self, nome, linhas):
        dados = await self._pedir("GET", f"/containers/{nome}/logs?stdout=1&stderr=1&tail={linhas}", bruto=True)
        return _desmultiplexar(dados)

    async def reiniciar(self, nome, espera_s=30):
        await self._pedir("POST", f"/containers/{nome}/restart?t={espera_s}")

    async def reiniciar_notebook(self, imagem):
        """Roda `systemctl reboot` no host, por um container privilegiado no espaço de processos dele."""
        criado = await self._pedir("POST", "/containers/create?name=painel-reinicia-notebook", {
            "Image": imagem,
            "Entrypoint": ["nsenter"],
            "Cmd": ["-t", "1", "-m", "-u", "-i", "-n", "-p", "--", "systemctl", "reboot"],
            "HostConfig": {"Privileged": True, "PidMode": "host", "AutoRemove": True},
        })
        await self._pedir("POST", f"/containers/{criado['Id']}/start")

    async def fechar(self):
        if self._sessao is not None:
            await self._sessao.close()


def _desmultiplexar(dados):
    """Logs de container sem TTY vêm em quadros: 8 bytes de cabeçalho (tipo, 0, 0, 0, tamanho) + texto."""
    partes = []
    i = 0
    while i + 8 <= len(dados):
        tamanho = struct.unpack(">I", dados[i + 4:i + 8])[0]
        partes.append(dados[i + 8:i + 8 + tamanho])
        i += 8 + tamanho
    if i == 0 and dados:
        partes = [dados]  # container com TTY: texto puro
    return b"".join(partes).decode(errors="replace")
