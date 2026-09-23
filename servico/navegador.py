"""Sobe e vigia o Openbox e o Google Chrome dentro da tela (Xorg ou Xvfb, aberta pelo entrada.sh)."""
import asyncio
import json
import logging
import os
import signal

import psutil

from . import config

log = logging.getLogger("navegador")

# Só flags que não mudam o que o site enxerga. Nada de automação, headless ou --no-sandbox.
FLAGS_CHROME = [
    f"--user-data-dir={config.DIR_PERFIL}",
    "--no-first-run",
    "--no-default-browser-check",
    "--start-maximized",
    "--password-store=basic",
]


class Navegador:
    def __init__(self):
        self.chrome = None
        self.openbox = None
        self._reiniciando = False
        self._vigia = None

    def vivo(self):
        return self.chrome is not None and self.chrome.returncode is None

    async def iniciar(self):
        await self._abrir_openbox()
        await self._abrir_chrome()
        self._vigia = asyncio.create_task(self._vigiar())

    async def _abrir_openbox(self):
        self.openbox = await asyncio.create_subprocess_exec(
            "openbox", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )

    async def _abrir_chrome(self):
        _remover_travas_do_perfil()
        _marcar_saida_limpa()
        log.info("abrindo o Chrome")
        self.chrome = await asyncio.create_subprocess_exec(
            "google-chrome-stable",
            *FLAGS_CHROME,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )

    async def fechar_chrome(self):
        if not self.vivo():
            return
        # SIGTERM fecha o Chrome do jeito normal (salva cookies e sessão).
        self.chrome.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(self.chrome.wait(), 20)
        except TimeoutError:
            log.warning("o Chrome não fechou em 20s; matando")
            try:
                os.killpg(self.chrome.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await self.chrome.wait()

    async def reiniciar_chrome(self):
        self._reiniciando = True
        try:
            await self.fechar_chrome()
            await self._abrir_chrome()
        finally:
            self._reiniciando = False

    async def _vigiar(self):
        while True:
            await asyncio.sleep(5)
            if self.openbox.returncode is not None:
                log.warning("o Openbox caiu; reabrindo")
                await self._abrir_openbox()
            if not self._reiniciando and not self.vivo():
                log.warning("o Chrome fechou (código %s); reabrindo", self.chrome.returncode)
                await asyncio.sleep(2)
                await self._abrir_chrome()

    def memoria_mb(self):
        """Memória exclusiva (USS) somada do Chrome e dos processos filhos.

        RSS não serve: cada processo do Chrome conta de novo as bibliotecas compartilhadas, e a
        soma dá ~3x o uso real.
        """
        if not self.vivo():
            return 0
        try:
            principal = psutil.Process(self.chrome.pid)
            processos = [principal, *principal.children(recursive=True)]
        except psutil.Error:
            return 0
        total = 0
        for p in processos:
            try:
                total += p.memory_full_info().uss
            except psutil.Error:
                pass
        return round(total / 1024 / 1024)


def _remover_travas_do_perfil():
    """Apaga o SingletonLock/Cookie/Socket que um Chrome anterior deixou no perfil.

    A trava guarda o nome da máquina. Como o container usa a rede do "warp", o nome é o do warp,
    que muda quando o warp é recriado (todo boot). Aí o Chrome acha que o perfil está aberto
    "em outro computador" e para num aviso, sem abrir nada. Só existe um Chrome aqui, então apagar
    é seguro (e o Chrome já foi fechado quando chegamos aqui).
    """
    for nome in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        caminho = os.path.join(config.DIR_PERFIL, nome)
        try:
            os.unlink(caminho)
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warning("não consegui apagar %s: %s", caminho, e)


def _marcar_saida_limpa():
    """Evita a bolha "O Chrome não foi encerrado corretamente" depois de uma queda."""
    caminho = os.path.join(config.DIR_PERFIL, "Default", "Preferences")
    try:
        with open(caminho) as f:
            prefs = json.load(f)
    except (OSError, ValueError):
        return
    perfil = prefs.setdefault("profile", {})
    if perfil.get("exit_type") == "Normal" and perfil.get("exited_cleanly") is True:
        return
    perfil["exit_type"] = "Normal"
    perfil["exited_cleanly"] = True
    with open(caminho, "w") as f:
        json.dump(prefs, f)
