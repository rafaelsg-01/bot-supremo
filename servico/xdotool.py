"""Teclado e mouse de verdade, dados de fora do navegador (xdotool -> extensão XTEST do X).

Para a página, esses eventos são iguais aos de uma pessoa (isTrusted = true): passam pelo
servidor X e pelo Chrome como qualquer tecla ou clique físico.
"""
import asyncio
import math
import random


class ErroXdotool(Exception):
    pass


async def _xdo(*args):
    proc = await asyncio.create_subprocess_exec(
        "xdotool", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    saida, erro = await proc.communicate()
    if proc.returncode != 0:
        raise ErroXdotool(f"xdotool {' '.join(args)}: {erro.decode(errors='replace').strip()}")
    return saida.decode(errors="replace")


async def janela_chrome():
    """ID X da janela principal do Chrome (a maior visível), ou None."""
    try:
        saida = await _xdo("search", "--onlyvisible", "--class", "google-chrome")
    except ErroXdotool:
        return None
    melhor, maior_area = None, -1
    for janela in saida.split():
        try:
            geo = await geometria(janela)
        except ErroXdotool:
            continue
        area = geo["WIDTH"] * geo["HEIGHT"]
        if area > maior_area:
            melhor, maior_area = janela, area
    return melhor


async def geometria(janela):
    saida = await _xdo("getwindowgeometry", "--shell", janela)
    valores = dict(linha.split("=", 1) for linha in saida.split() if "=" in linha)
    return {k: int(v) for k, v in valores.items() if k in ("X", "Y", "WIDTH", "HEIGHT")}


async def focar(janela):
    await _xdo("windowactivate", "--sync", janela)


async def teclas(*sequencia):
    await _xdo("key", "--clearmodifiers", *sequencia)


async def digitar(texto, atraso_ms):
    await _xdo("type", "--clearmodifiers", "--delay", str(atraso_ms), "--", texto)


async def posicao_mouse():
    saida = await _xdo("getmouselocation", "--shell")
    valores = dict(linha.split("=", 1) for linha in saida.split() if "=" in linha)
    return int(valores["X"]), int(valores["Y"])


async def mover(x, y):
    """Move o mouse numa curva com pequenas variações, como uma mão, e termina exato no alvo."""
    x0, y0 = await posicao_mouse()
    distancia = math.hypot(x - x0, y - y0)
    if distancia < 2:
        return
    passos = max(8, min(40, int(distancia / 25)))
    duracao = random.uniform(0.25, 0.55) + distancia / 4000
    # Ponto de controle da curva (Bézier quadrática), deslocado para um dos lados.
    cx = (x0 + x) / 2 + random.uniform(-0.25, 0.25) * distancia
    cy = (y0 + y) / 2 + random.uniform(-0.25, 0.25) * distancia
    for i in range(1, passos + 1):
        t = i / passos
        t = t * t * (3 - 2 * t)  # acelera e desacelera
        px = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t * t * x
        py = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t * t * y
        if i < passos:
            px += random.uniform(-1.5, 1.5)
            py += random.uniform(-1.5, 1.5)
        else:
            px, py = x, y
        await _xdo("mousemove", str(round(px)), str(round(py)))
        await asyncio.sleep(duracao / passos)


async def clicar(x, y):
    await mover(x, y)
    await asyncio.sleep(random.uniform(0.06, 0.18))
    await _xdo("mousedown", "1")
    await asyncio.sleep(random.uniform(0.05, 0.12))
    await _xdo("mouseup", "1")


async def rolar(cliques, para_baixo=True):
    """Roda do mouse. Cada clique rola ~100 px no Chrome."""
    botao = "5" if para_baixo else "4"
    for _ in range(cliques):
        await _xdo("click", botao)
        await asyncio.sleep(random.uniform(0.04, 0.1))
