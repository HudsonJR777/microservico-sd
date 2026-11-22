import hashlib

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from .messaging.publisher import publicar
from .models import Monografia, ExtracaoPDF
from .validators import calcular_sha256, validar_pdf

S = Monografia.Status


class RegraDeNegocio(Exception):
    """Violação de regra de negócio (a view converte em HTTP 400)."""


def _payload(m, **extra):
    dados = {
        "monografia_id": m.pk,
        "origem_id": m.origem_id,
        "aluno_id": m.aluno_id,
        "orientador_id": m.orientador_id,
        "curso_id": m.curso_id,
        "status": str(m.status),
    }
    dados.update(extra)
    return dados


@transaction.atomic
def anexar_arquivo(m: Monografia, arquivo):
    m = Monografia.objects.select_for_update().get(pk=m.pk)
    if m.status not in Monografia.EDITAVEIS:
        raise RegraDeNegocio(
            "Só é possível enviar arquivo em rascunho ou quando o orientador pediu ajustes."
        )
    validar_pdf(arquivo)
    h = calcular_sha256(arquivo)
    if Monografia.objects.filter(hash_sha256=h).exclude(pk=m.pk).exists():
        raise RegraDeNegocio("Este arquivo já foi enviado em outra monografia.")
    m.arquivo = arquivo
    m.hash_sha256 = h
    m.tamanho_bytes = arquivo.size
    m.save()


@transaction.atomic
def submeter(m: Monografia):
    m = Monografia.objects.select_for_update().get(pk=m.pk)
    if m.status not in Monografia.EDITAVEIS:
        raise RegraDeNegocio(
            "Apenas rascunhos ou monografias com ajustes solicitados podem ser submetidas."
        )
    if not m.arquivo:
        raise RegraDeNegocio("Envie o PDF antes de submeter.")
    # O lock transacional da monografia serializa submissões. O índice tem
    # versão própria; não bloqueia reenvios enquanto aguarda o consumidor.

    with transaction.atomic():
        m.status = S.SUBMETIDA
        m.aprovada_orientador = False
        m.aprovada_orientador_em = None
        m.submetida_em = timezone.now()
        m.save()
        ExtracaoPDF.objects.create(monografia=m, arquivo_nome=m.arquivo.name, sha256=m.hash_sha256)


def _checar_orientador_em_analise(m: Monografia, professor_id: int):
    if m.orientador_id != professor_id:
        raise RegraDeNegocio("Somente o orientador desta monografia pode avaliá-la.")
    if m.status not in (S.SUBMETIDA, S.EM_ANALISE_ORIENTADOR):
        raise RegraDeNegocio("A monografia não está aguardando análise do orientador.")


@transaction.atomic
def aprovar_orientador(m: Monografia, professor_id: int):
    m = Monografia.objects.select_for_update().get(pk=m.pk)
    if not verificar_integridade(m):
        raise RegraDeNegocio("O PDF diverge do SHA-256 registrado; aprovação bloqueada.")
    _checar_orientador_em_analise(m, professor_id)
    with transaction.atomic():
        m.status = S.APROVADA_ORIENTADOR
        m.aprovada_orientador = True
        m.aprovada_orientador_em = timezone.now()
        m.save()
        payload = _payload(m)
        transaction.on_commit(lambda: publicar("monografia.aprovada_orientador", payload))


@transaction.atomic
def solicitar_ajustes(m: Monografia, professor_id: int, motivo: str = ""):
    m = Monografia.objects.select_for_update().get(pk=m.pk)
    _checar_orientador_em_analise(m, professor_id)
    with transaction.atomic():
        m.status = S.AJUSTES_SOLICITADOS
        m.aprovada_orientador = False
        m.aprovada_orientador_em = None
        m.save()
        # Libera o lock: o aluno vai trocar o PDF e precisa poder submeter de novo
        cache.delete(f"upload_em_andamento:{m.pk}")
        payload = _payload(m, motivo=motivo)
        transaction.on_commit(lambda: publicar("monografia.ajustes_solicitados", payload))


def verificar_integridade(m: Monografia) -> bool:
    if not m.arquivo:
        return False
    with m.arquivo.open("rb") as f:
        h = hashlib.sha256()
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest() == m.hash_sha256


# ---------------------------------------------------------------------------
# Eventos vindos de outros servicos (consumidos por consumir_eventos)
# ---------------------------------------------------------------------------
class EventoInvalido(Exception):
    """Payload malformado ou evento desconhecido (vai para a DLQ)."""


_TRANSICOES = {
    "banca.agendada": ({S.APROVADA_ORIENTADOR}, S.BANCA_AGENDADA),
    "banca.cancelada": ({S.BANCA_AGENDADA}, S.APROVADA_ORIENTADOR),
    "banca.defesa_realizada": ({S.BANCA_AGENDADA}, S.DEFESA_REALIZADA),
}
_RESULTADOS = {
    "aprovada": S.APROVADA,
    "aprovada_com_ressalvas": S.APROVADA_COM_RESSALVAS,
    "reprovada": S.REPROVADA,
}


def aplicar_evento(routing_key: str, dados: dict) -> str:
    """Aplica um evento externo. Idempotente: repetir o mesmo evento não muda nada."""
    try:
        mid = int(dados["monografia_id"])
    except (KeyError, TypeError, ValueError):
        raise EventoInvalido("monografia_id ausente ou inválido")

    if routing_key == "repositorio.extracao_concluida":
        cache.delete(f"upload_em_andamento:{mid}")
        return f"monografia {mid}: lock liberado"

    if routing_key == "banca.resultado_final":
        resultado = str(dados.get("resultado", "")).lower()
        if resultado not in _RESULTADOS:
            raise EventoInvalido(f"resultado desconhecido: {resultado!r}")
        origem = {S.BANCA_AGENDADA, S.DEFESA_REALIZADA}
        destino = _RESULTADOS[resultado]
    elif routing_key in _TRANSICOES:
        origem, destino = _TRANSICOES[routing_key]
    else:
        raise EventoInvalido(f"evento desconhecido: {routing_key}")

    with transaction.atomic():
        # select_for_update evita corrida entre dois eventos da mesma monografia
        m = Monografia.objects.select_for_update().filter(pk=mid).first()
        if m is None:
            return f"monografia {mid}: inexistente (ignorado)"
        if m.status == destino:
            return f"monografia {mid}: já estava em {destino} (ignorado)"
        if m.status not in origem:
            return f"monografia {mid}: transição inválida a partir de {m.status} (ignorado)"
        m.status = destino
        m._change_reason = routing_key  # fica no histórico (auditoria)
        m.save()
        payload = _payload(m)
        transaction.on_commit(lambda: publicar("monografia.status_alterado", payload))
    return f"monografia {mid}: {destino}"
