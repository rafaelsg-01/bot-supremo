"""Gancho chamado quando a aba cai no desafio da Cloudflare ("Um momento…" / "Just a moment...").

O QUE FAZER AQUI É DECISÃO E RESPONSABILIDADE DO DONO DO PROJETO. Não implemente.

O serviço já faz o resto sozinho: registra o desafio em log, espera ele sumir até o timeout do
pedido e, se não sumir, responde com status "desafio" e um erro claro. Esta função roda numa
thread à parte, então pode demorar sem travar o pedido.
"""


def ao_detectar_desafio(contexto: dict) -> None:
    """contexto: {"url", "titulo", "aba", "horario"}. Intencionalmente vazia."""
    pass
