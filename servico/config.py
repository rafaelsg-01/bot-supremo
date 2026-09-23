"""Configuração lida do ambiente (arquivo .env no notebook)."""
import os


def _int(nome, padrao):
    return int(os.environ.get(nome, padrao))


TOKEN = os.environ.get("BOT_TOKEN", "")

PORTA_API = _int("PORTA_API", 8080)
# Extensão <-> serviço. Só escuta em 127.0.0.1.
PORTA_PONTE = _int("PORTA_PONTE", 8081)
URL_BASE_PONTE = f"http://127.0.0.1:{PORTA_PONTE}"

DISPLAY = os.environ.get("DISPLAY", ":0")
DIR_PERFIL = os.environ.get("DIR_PERFIL", "/perfil")
DIR_ESTADO = os.environ.get("DIR_ESTADO", "/estado")
DIR_EXTENSAO = os.environ.get("DIR_EXTENSAO", "/opt/bot-supremo/extensao")
ARQ_POLITICA = "/etc/opt/chrome/policies/managed/bot-supremo.json"
ARQ_INFO_EXTENSAO = os.path.join(DIR_ESTADO, "extensao.json")

# Acima disso (soma dos processos do Chrome), o Chrome é reaberto entre dois pedidos.
LIMITE_MEMORIA_CHROME_MB = _int("LIMITE_MEMORIA_CHROME_MB", 1100)

FILA_MAXIMA = _int("FILA_MAXIMA", 5)
TIMEOUT_PADRAO_MS = _int("TIMEOUT_PADRAO_MS", 60000)
TIMEOUT_MAXIMO_MS = _int("TIMEOUT_MAXIMO_MS", 240000)

ATRASO_DIGITACAO_MS = _int("ATRASO_DIGITACAO_MS", 25)
# "Carregou" = frame principal completo e nenhuma request começando/terminando por este tempo.
REDE_QUIETA_MS = _int("REDE_QUIETA_MS", 1000)
# Depois do "completo", no máximo este tempo esperando a rede aquietar.
ESPERA_MAXIMA_REDE_QUIETA_MS = _int("ESPERA_MAXIMA_REDE_QUIETA_MS", 8000)

ID_UBO_LITE = "ddkjiahejlhfcafbddmgiahcphecmpfh"
