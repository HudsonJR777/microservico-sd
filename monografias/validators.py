import hashlib
from django.conf import settings
from django.core.exceptions import ValidationError
from pypdf import PdfReader


def validar_pdf(arquivo):
    if not arquivo.name.lower().endswith(".pdf"):
        raise ValidationError("Somente arquivos PDF são aceitos.")
    limite = settings.MAX_UPLOAD_MB * 1024 * 1024
    if arquivo.size > limite:
        raise ValidationError(f"Arquivo maior que {settings.MAX_UPLOAD_MB} MB.")
    # A extensão é fácil de burlar: confere a assinatura real do PDF
    arquivo.seek(0)
    cabecalho = arquivo.read(5)
    arquivo.seek(0)
    if cabecalho != b"%PDF-":
        raise ValidationError("O conteúdo do arquivo não é um PDF válido.")
    try:
        pdf = PdfReader(arquivo)
        if pdf.is_encrypted or not pdf.pages:
            raise ValueError('PDF vazio ou protegido.')
    except Exception as exc:
        raise ValidationError('Envie um PDF legível, com páginas e sem senha.') from exc
    finally:
        arquivo.seek(0)


def calcular_sha256(arquivo):
    h = hashlib.sha256()
    arquivo.seek(0)
    for chunk in arquivo.chunks():  # lê em partes, sem carregar tudo na memória
        h.update(chunk)
    arquivo.seek(0)
    return h.hexdigest()
