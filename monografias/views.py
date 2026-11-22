from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import FileResponse
from io import BytesIO
import hashlib
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from . import services
from .models import Monografia, ExtracaoPDF
from .serializers import MonografiaSerializer
from .throttles import SubmissaoThrottle


class MonografiaViewSet(viewsets.ModelViewSet):
    serializer_class = MonografiaSerializer
    filterset_fields = ["status", "curso_id", "orientador_id", "aluno_id"]
    search_fields = ["titulo", "resumo", "palavras_chave"]

    # --- identidade vinda do token (emitido pelo CRUD) ---
    def _claim(self, nome):
        token = self.request.auth
        return token.get(nome) if token else None

    @property
    def papel(self):
        return self._claim("role")

    @property
    def aluno_perfil_id(self):
        """Aluno.pk (não confundir com auth.User.pk)."""
        return self._claim("aluno_id")

    @property
    def professor_perfil_id(self):
        """Professor.pk (não confundir com auth.User.pk)."""
        return self._claim("professor_id")

    def _exigir_papel(self, *papeis):
        if self.papel not in papeis:
            raise PermissionDenied("Seu papel não permite esta ação.")

    def _exigir_integracao(self, monografia):
        if monografia.origem_id is not None and self._claim('scope') != 'integracao:origem':
            raise PermissionDenied('Acoes em monografias vinculadas exigem a integracao com o principal.')

    def get_queryset(self):
        qs = Monografia.objects.all().order_by("-criado_em")
        if self.papel == "aluno":
            if self.aluno_perfil_id is None:
                return qs.none()
            return qs.filter(aluno_id=self.aluno_perfil_id)
        if self.papel == "orientador":
            if self.professor_perfil_id is None:
                return qs.none()
            return qs.filter(orientador_id=self.professor_perfil_id)
        if self.papel == "coordenador":
            cursos = self._claim("curso_ids")
            return qs.filter(curso_id__in=cursos) if isinstance(cursos, list) else qs.none()
        if self.papel == "admin":
            return qs
        return qs.none()

    def _executar(self, fn, *args):
        try:
            fn(*args)
        except (services.RegraDeNegocio, ValidationError) as e:
            msg = e.messages if isinstance(e, ValidationError) else [str(e)]
            return Response({"erro": msg}, status=status.HTTP_400_BAD_REQUEST)
        return None

    # --- CRUD ---
    def create(self, request, *args, **kwargs):
        origem = request.data.get('origem_id')
        if origem is not None:
            # Somente a ponte servidor-servidor emite JWT com esse escopo.
            if self.papel != 'aluno' or self._claim('scope') != 'integracao:origem':
                raise PermissionDenied('Somente a integracao pode vincular IDs de origem.')
            try:
                origem = int(origem)
                if origem <= 0:
                    raise ValueError()
            except (TypeError, ValueError):
                return Response({'erro': ['ID de origem invalido.']}, status=400)
            existente = Monografia.objects.filter(origem_id=origem).first()
            if existente is not None:
                if existente.aluno_id != self.aluno_perfil_id:
                    raise PermissionDenied('Este vinculo ja pertence a outro aluno.')
                return Response(self.get_serializer(existente).data, status=200)
        if origem is None:
            return super().create(request, *args, **kwargs)
        # Duas tentativas simultaneas com mesma origem nao criam registros duplicados.
        try:
            with transaction.atomic():
                return super().create(request, *args, **kwargs)
        except IntegrityError:
            existente = Monografia.objects.filter(origem_id=origem).first()
            if existente is not None and existente.aluno_id == self.aluno_perfil_id:
                return Response(self.get_serializer(existente).data, status=200)
            raise PermissionDenied('O vinculo de origem nao esta disponivel.')

    def perform_create(self, serializer):
        if self.papel != "aluno":
            raise PermissionDenied("Apenas alunos criam monografias.")
        if self.aluno_perfil_id is None:
            raise PermissionDenied("Token sem aluno_id.")
        origem = self.request.data.get('origem_id')
        curso = self._claim('curso_id')
        orientador = self._claim('orientador_id')
        if not curso or not orientador:
            raise PermissionDenied('Token sem os vínculos acadêmicos autorizados.')
        if serializer.validated_data.get('curso_id') != curso or serializer.validated_data.get('orientador_id') != orientador:
            raise PermissionDenied('Curso e orientador devem corresponder aos vínculos do token.')
        serializer.save(aluno_id=self.aluno_perfil_id,
                        **({'origem_id': int(origem)} if origem is not None else {}))

    @transaction.atomic
    def perform_update(self, serializer):
        self._exigir_papel("aluno")
        serializer.instance = Monografia.objects.select_for_update().get(pk=serializer.instance.pk)
        if serializer.instance.origem_id is not None and (
            self.papel != 'aluno' or self._claim('scope') != 'integracao:origem'
        ):
            raise PermissionDenied('Edicao de monografia vinculada exige a integracao.')
        if serializer.instance.status not in Monografia.EDITAVEIS:
            raise PermissionDenied(
                "Só é possível editar em rascunho ou quando o orientador pediu ajustes."
            )
        for campo in ('curso_id', 'orientador_id'):
            if campo in serializer.validated_data and serializer.validated_data[campo] != self._claim(campo):
                raise PermissionDenied('Vínculo acadêmico divergente do token.')
        serializer.save()

    @transaction.atomic
    def perform_destroy(self, instance):
        self._exigir_papel("aluno")
        instance = Monografia.objects.select_for_update().get(pk=instance.pk)
        if instance.origem_id is not None:
            raise PermissionDenied('A exclusao de monografias vinculadas nao e permitida neste servico.')
        if instance.status != Monografia.Status.RASCUNHO or instance.submetida_em:
            raise PermissionDenied("Somente rascunhos nunca submetidos podem ser excluídos.")
        instance.delete()

    # --- ações do fluxo ---
    @action(detail=True, methods=["post"])
    def upload(self, request, pk=None):
        self._exigir_papel("aluno")
        m = self.get_object()
        self._exigir_integracao(m)
        arquivo = request.FILES.get("arquivo")
        if not arquivo:
            return Response({"erro": ["Envie o campo 'arquivo'."]}, status=400)
        erro = self._executar(services.anexar_arquivo, m, arquivo)
        m.refresh_from_db()
        return erro or Response(self.get_serializer(m).data)

    @action(detail=True, methods=["post"], throttle_classes=[SubmissaoThrottle])
    def submeter(self, request, pk=None):
        self._exigir_papel("aluno")
        m = self.get_object()
        self._exigir_integracao(m)
        erro = self._executar(services.submeter, m)
        m.refresh_from_db()
        return erro or Response(self.get_serializer(m).data)

    @action(detail=True, methods=["post"], url_path="aprovar-orientador")
    def aprovar_orientador(self, request, pk=None):
        self._exigir_papel("orientador")
        if self.professor_perfil_id is None:
            raise PermissionDenied("Token sem professor_id.")
        m = self.get_object()
        self._exigir_integracao(m)
        erro = self._executar(services.aprovar_orientador, m, self.professor_perfil_id)
        m.refresh_from_db()
        return erro or Response(self.get_serializer(m).data)

    @action(detail=True, methods=["post"], url_path="solicitar-ajustes")
    def solicitar_ajustes(self, request, pk=None):
        self._exigir_papel("orientador")
        if self.professor_perfil_id is None:
            raise PermissionDenied("Token sem professor_id.")
        m = self.get_object()
        self._exigir_integracao(m)
        motivo = str(request.data.get("motivo", ""))[:500]
        erro = self._executar(
            services.solicitar_ajustes, m, self.professor_perfil_id, motivo
        )
        m.refresh_from_db()
        return erro or Response(self.get_serializer(m).data)

    @action(detail=True, methods=["get"], url_path="verificar-integridade")
    def verificar_integridade(self, request, pk=None):
        m = self.get_object()
        return Response({"integro": services.verificar_integridade(m),
                         "hash_armazenado": m.hash_sha256})


    @action(detail=True, methods=["get"], url_path="baixar-pdf")
    def baixar_pdf(self, request, pk=None):
        """Download privado apenas para o aluno dono ou o orientador vinculado."""
        self._exigir_papel("aluno", "orientador")
        monografia = self.get_object()  # get_queryset limita aluno_id / professor_id
        if not monografia.arquivo:
            return Response({"erro": ["O PDF ainda nao foi enviado."]}, status=404)
        with monografia.arquivo.open('rb') as arquivo:
            conteudo = arquivo.read()
        if hashlib.sha256(conteudo).hexdigest() != monografia.hash_sha256:
            return Response({'erro': ['Integridade do PDF comprometida.']}, status=409)
        resposta = FileResponse(
            BytesIO(conteudo), content_type="application/pdf",
            as_attachment=False, filename=f"monografia-{monografia.pk}.pdf",
        )
        resposta["X-Content-Type-Options"] = "nosniff"
        resposta["Cache-Control"] = "private, no-store"
        return resposta

    @action(detail=True, methods=['get'], url_path='extracoes')
    def extracoes(self, request, pk=None):
        self._exigir_papel('aluno', 'orientador')
        m = self.get_object()
        # Índices privados; nunca expor caminhos físicos do armazenamento.
        registros = m.extracoes.order_by('-criado_em').values(
            'id', 'sha256', 'criado_em', 'processado_em', 'sem_texto', 'falhas', 'ultimo_erro', 'texto')
        return Response({'versoes': list(registros)})
