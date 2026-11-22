"""Extração privada por versão; somente o worker lê o conteúdo do PDF."""
import hashlib
from datetime import timedelta
from io import BytesIO
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from pypdf import PdfReader

from .models import ExtracaoPDF, Monografia


class MensagemInvalida(ValueError):
    pass


def dados_evento(evento):
    return {'versao': 1, 'evento_id': str(evento.pk), 'monografia_id': evento.monografia_id, 'sha256': evento.sha256}


def obter_evento(dados):
    if not isinstance(dados, dict):
        raise MensagemInvalida('Objeto JSON obrigatório.')
    try:
        pk = UUID(dados.get('evento_id', ''))
    except (ValueError, TypeError, AttributeError):
        raise MensagemInvalida('Identificador inválido.') from None
    evento = ExtracaoPDF.objects.filter(pk=pk).first()
    if not evento or dados != dados_evento(evento):
        raise MensagemInvalida('Evento divergente da versão registrada.')
    return evento


def extrair_evento(dados):
    evento = obter_evento(dados)
    if evento.processado_em:
        return False
    storage = Monografia._meta.get_field('arquivo').storage
    with storage.open(evento.arquivo_nome, 'rb') as arquivo:
        conteudo = arquivo.read(settings.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(conteudo) > settings.MAX_UPLOAD_MB * 1024 * 1024 or hashlib.sha256(conteudo).hexdigest() != evento.sha256:
        raise ValueError('Integridade da versão comprometida.')
    pdf = PdfReader(BytesIO(conteudo))
    if pdf.is_encrypted or not pdf.pages:
        raise ValueError('PDF não permite extração.')
    partes, total = [], 0
    for pagina in pdf.pages:
        texto = (pagina.extract_text() or '').replace('\x00', '')
        restante = settings.EXTRACOES_MAX_TEXTO - total
        partes.append(texto[:restante])
        total += len(texto) + 1
        if total >= settings.EXTRACOES_MAX_TEXTO:
            break
    texto = '\n'.join(partes)[:settings.EXTRACOES_MAX_TEXTO]
    with transaction.atomic():
        evento = ExtracaoPDF.objects.select_for_update().get(pk=evento.pk)
        if evento.processado_em:
            return False
        evento.texto = texto
        evento.sem_texto = not texto.strip()
        evento.processado_em = timezone.now()
        evento.ultimo_erro = ''
        evento.save(update_fields=['texto', 'sem_texto', 'processado_em', 'ultimo_erro'])
    return True


def registrar_falha(dados, erro):
    try:
        evento = obter_evento(dados)
    except MensagemInvalida:
        return
    with transaction.atomic():
        evento = ExtracaoPDF.objects.select_for_update().get(pk=evento.pk)
        if evento.processado_em:
            return
        evento.falhas += 1
        evento.ultimo_erro = type(erro).__name__[:255]
        evento.proxima_tentativa = timezone.now() + timedelta(seconds=30 * 2 ** min(evento.falhas - 1, 6))
        evento.save(update_fields=['falhas', 'ultimo_erro', 'proxima_tentativa'])
