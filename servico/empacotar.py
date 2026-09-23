"""Empacota a extensão própria como CRX3 e escreve a política do Chrome que a instala.

Roda como root na subida do container (docker/entrada.sh), antes do Chrome abrir.

Por quê: o Google Chrome oficial não aceita mais --load-extension. O jeito suportado de instalar
uma extensão fora da loja é por política ("force_installed"), apontando para um update.xml que o
próprio serviço serve em 127.0.0.1. A chave que assina a extensão é gerada aqui na primeira vez e
fica no volume /estado (nunca vai para o git), então o ID da extensão não muda entre recriações.

Uso: python3 -m servico.empacotar
"""
import hashlib
import io
import json
import os
import struct
import zipfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from . import config

ARQ_CHAVE = os.path.join(config.DIR_ESTADO, "chave-extensao.pem")
ARQ_CRX = os.path.join(config.DIR_ESTADO, "extensao.crx")
ARQ_UPDATE = os.path.join(config.DIR_ESTADO, "update.xml")
URL_UPDATE = f"{config.URL_BASE_PONTE}/extensao/update.xml"
URL_CRX = f"{config.URL_BASE_PONTE}/extensao/extensao.crx"


def _carregar_chave():
    if os.path.exists(ARQ_CHAVE):
        with open(ARQ_CHAVE, "rb") as f:
            return serialization.load_pem_private_key(f.read(), None)
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = chave.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    fd = os.open(ARQ_CHAVE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return chave


def _arquivos():
    lista = []
    for raiz, _, nomes in os.walk(config.DIR_EXTENSAO):
        for nome in nomes:
            caminho = os.path.join(raiz, nome)
            lista.append((os.path.relpath(caminho, config.DIR_EXTENSAO).replace(os.sep, "/"), caminho))
    return sorted(lista)


def _hash(arquivos):
    h = hashlib.sha256()
    for rel, caminho in arquivos:
        h.update(rel.encode())
        with open(caminho, "rb") as f:
            h.update(f.read())
    return h.hexdigest()


def _zip(arquivos, versao):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, caminho in arquivos:
            with open(caminho, "rb") as f:
                dados = f.read()
            if rel == "manifest.json":
                manifesto = json.loads(dados)
                manifesto["version"] = versao
                manifesto["update_url"] = URL_UPDATE
                dados = json.dumps(manifesto, ensure_ascii=False, indent=2).encode()
            z.writestr(rel, dados)
    return buf.getvalue()


# Protobuf mínimo do cabeçalho CRX3 (só campos "bytes").
def _varint(n):
    saida = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            saida.append(b | 0x80)
        else:
            saida.append(b)
            return bytes(saida)


def _campo(numero, dados):
    return _varint((numero << 3) | 2) + _varint(len(dados)) + dados


def _crx3(chave, der_publica, crx_id, zip_bytes):
    signed_data = _campo(1, crx_id)
    mensagem = b"CRX3 SignedData\x00" + struct.pack("<I", len(signed_data)) + signed_data + zip_bytes
    assinatura = chave.sign(mensagem, padding.PKCS1v15(), hashes.SHA256())
    prova = _campo(1, der_publica) + _campo(2, assinatura)
    cabecalho = _campo(2, prova) + _campo(10000, signed_data)
    return b"Cr24" + struct.pack("<II", 3, len(cabecalho)) + cabecalho + zip_bytes


def _escrever_politica(id_extensao):
    politica = {
        "ExtensionSettings": {
            id_extensao: {"installation_mode": "force_installed", "update_url": URL_UPDATE},
            # uBlock Origin Lite da Chrome Web Store, como no navegador do dono.
            config.ID_UBO_LITE: {
                "installation_mode": "force_installed",
                "update_url": "https://clients2.google.com/service/update2/crx",
            },
        },
        # Tira janelas e bolhas do Chrome que atrapalhariam a digitação e os cliques.
        # Nada disso é visível para os sites.
        "DefaultBrowserSettingEnabled": False,
        "PasswordManagerEnabled": False,
        "AutofillAddressEnabled": False,
        "AutofillCreditCardEnabled": False,
        "TranslateEnabled": False,
        "PromotionalTabsEnabled": False,
        "BackgroundModeEnabled": False,
        "HardwareAccelerationModeEnabled": True,
        "RestoreOnStartup": 4,
        "RestoreOnStartupURLs": ["about:blank"],
    }
    os.makedirs(os.path.dirname(config.ARQ_POLITICA), exist_ok=True)
    with open(config.ARQ_POLITICA, "w") as f:
        json.dump(politica, f, indent=2)


def principal():
    os.makedirs(config.DIR_ESTADO, exist_ok=True)
    chave = _carregar_chave()
    der = chave.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    crx_id = hashlib.sha256(der).digest()[:16]
    id_extensao = "".join(chr(ord("a") + int(c, 16)) for c in crx_id.hex())

    arquivos = _arquivos()
    hash_atual = _hash(arquivos)
    info = {}
    if os.path.exists(config.ARQ_INFO_EXTENSAO):
        with open(config.ARQ_INFO_EXTENSAO) as f:
            info = json.load(f)

    if info.get("hash") != hash_atual or info.get("id") != id_extensao or not os.path.exists(ARQ_CRX):
        numero = info.get("numero", 0) + 1
        versao = f"1.0.{numero}"
        with open(ARQ_CRX, "wb") as f:
            f.write(_crx3(chave, der, crx_id, _zip(arquivos, versao)))
        with open(ARQ_UPDATE, "w") as f:
            f.write(
                "<?xml version='1.0' encoding='UTF-8'?>\n"
                "<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>\n"
                f"  <app appid='{id_extensao}'>\n"
                f"    <updatecheck codebase='{URL_CRX}' version='{versao}' />\n"
                "  </app>\n"
                "</gupdate>\n"
            )
        info = {"id": id_extensao, "versao": versao, "numero": numero, "hash": hash_atual}
        with open(config.ARQ_INFO_EXTENSAO, "w") as f:
            json.dump(info, f, indent=2)
        print(f"[empacotar] extensão {id_extensao} empacotada na versão {versao}", flush=True)
    else:
        print(f"[empacotar] extensão {id_extensao} sem mudanças (versão {info['versao']})", flush=True)

    for arq in (ARQ_CRX, ARQ_UPDATE, config.ARQ_INFO_EXTENSAO):
        os.chmod(arq, 0o644)
    _escrever_politica(id_extensao)


if __name__ == "__main__":
    principal()
