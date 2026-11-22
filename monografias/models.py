from django.db import models
from simple_history.models import HistoricalRecords


class Monografia(models.Model):
    class Status(models.TextChoices):
        RASCUNHO = "RASCUNHO", "Rascunho"
        SUBMETIDA = "SUBMETIDA", "Submetida"
        EM_ANALISE_ORIENTADOR = "EM_ANALISE_ORIENTADOR", "Em análise pelo orientador"
        AJUSTES_SOLICITADOS = "AJUSTES_SOLICITADOS", "Ajustes solicitados"
        APROVADA_ORIENTADOR = "APROVADA_ORIENTADOR", "Aprovada pelo orientador"
        BANCA_AGENDADA = "BANCA_AGENDADA", "Banca agendada"
        DEFESA_REALIZADA = "DEFESA_REALIZADA", "Defesa realizada"
        REPROVADA = "REPROVADA", "Reprovada"
        APROVADA_COM_RESSALVAS = "APROVADA_COM_RESSALVAS", "Aprovada com ressalvas"
        CORRECOES_ENTREGUES = "CORRECOES_ENTREGUES", "Correções entregues"
        APROVADA = "APROVADA", "Aprovada"
        PUBLICADA = "PUBLICADA", "Publicada"

    # Estados em que o aluno ainda pode editar os dados e trocar o PDF
    EDITAVEIS = (Status.RASCUNHO, Status.AJUSTES_SOLICITADOS)

    titulo = models.CharField(max_length=300)
    resumo = models.TextField(blank=True)
    palavras_chave = models.CharField(max_length=300, blank=True)

    # Referências a outros serviços: apenas IDs (e nomes copiados para exibição)
    aluno_id = models.PositiveIntegerField(db_index=True)
    origem_id = models.PositiveIntegerField(null=True, blank=True, unique=True, db_index=True)
    aluno_nome = models.CharField(max_length=200, blank=True)
    orientador_id = models.PositiveIntegerField(db_index=True)
    orientador_nome = models.CharField(max_length=200, blank=True)
    curso_id = models.PositiveIntegerField(db_index=True)

    arquivo = models.FileField(upload_to="monografias/%Y/%m/", blank=True)
    hash_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    tamanho_bytes = models.PositiveBigIntegerField(null=True, blank=True)

    status = models.CharField(max_length=30, choices=Status.choices,
                              default=Status.RASCUNHO, db_index=True)
    aprovada_orientador = models.BooleanField(default=False)
    aprovada_orientador_em = models.DateTimeField(null=True, blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    submetida_em = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords(
        user_model=None,
        history_user_id_field=models.PositiveIntegerField(null=True, blank=True),
        history_user_getter=lambda historical: historical.history_user_id,
        history_user_setter=lambda historical, user: setattr(
            historical, "history_user_id", getattr(user, "id", None)
        ),
    ) # quem, o quê e quando

    def _get_user_id(historical):
        return historical.history_user_id


    def _set_user_id(historical, user):
        historical.history_user_id = getattr(user, "id", None)

    def __str__(self):
        return self.titulo


class DocumentoComplementar(models.Model):
    class Tipo(models.TextChoices):
        ATA = "ata", "Ata de defesa"
        TERMO = "termo_publicacao", "Termo de autorização de publicação"
        ORTOGRAFIA = "ortografia", "Comprovante de correção ortográfica"

    monografia = models.ForeignKey(Monografia, on_delete=models.CASCADE,
                                   related_name="documentos")
    tipo = models.CharField(max_length=30, choices=Tipo.choices)
    arquivo = models.FileField(upload_to="documentos/%Y/%m/")
    hash_sha256 = models.CharField(max_length=64, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords()