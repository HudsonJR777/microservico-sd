from django.core.exceptions import ValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from . import services
from .models import Monografia
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
        if self.papel in ("coordenador", "admin"):
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
    def perform_create(self, serializer):
        if self.papel != "aluno":
            raise PermissionDenied("Apenas alunos criam monografias.")
        if self.aluno_perfil_id is None:
            raise PermissionDenied("Token sem aluno_id.")
        serializer.save(aluno_id=self.aluno_perfil_id)

    def perform_update(self, serializer):
        self._exigir_papel("aluno", "coordenador", "admin")
        if serializer.instance.status not in Monografia.EDITAVEIS:
            raise PermissionDenied(
                "Só é possível editar em rascunho ou quando o orientador pediu ajustes."
            )
        serializer.save()

    def perform_destroy(self, instance):
        self._exigir_papel("aluno", "coordenador", "admin")
        if instance.status == Monografia.Status.PUBLICADA:
            raise PermissionDenied("Monografias publicadas não podem ser excluídas.")
        instance.delete()

    # --- ações do fluxo ---
    @action(detail=True, methods=["post"])
    def upload(self, request, pk=None):
        self._exigir_papel("aluno")
        m = self.get_object()
        arquivo = request.FILES.get("arquivo")
        if not arquivo:
            return Response({"erro": ["Envie o campo 'arquivo'."]}, status=400)
        erro = self._executar(services.anexar_arquivo, m, arquivo)
        return erro or Response(self.get_serializer(m).data)

    @action(detail=True, methods=["post"], throttle_classes=[SubmissaoThrottle])
    def submeter(self, request, pk=None):
        self._exigir_papel("aluno")
        m = self.get_object()
        erro = self._executar(services.submeter, m)
        return erro or Response(self.get_serializer(m).data)

    @action(detail=True, methods=["post"], url_path="aprovar-orientador")
    def aprovar_orientador(self, request, pk=None):
        self._exigir_papel("orientador")
        if self.professor_perfil_id is None:
            raise PermissionDenied("Token sem professor_id.")
        m = self.get_object()
        erro = self._executar(services.aprovar_orientador, m, self.professor_perfil_id)
        return erro or Response(self.get_serializer(m).data)

    @action(detail=True, methods=["post"], url_path="solicitar-ajustes")
    def solicitar_ajustes(self, request, pk=None):
        self._exigir_papel("orientador")
        if self.professor_perfil_id is None:
            raise PermissionDenied("Token sem professor_id.")
        m = self.get_object()
        motivo = str(request.data.get("motivo", ""))[:500]
        erro = self._executar(
            services.solicitar_ajustes, m, self.professor_perfil_id, motivo
        )
        return erro or Response(self.get_serializer(m).data)

    @action(detail=True, methods=["get"], url_path="verificar-integridade")
    def verificar_integridade(self, request, pk=None):
        m = self.get_object()
        return Response({"integro": services.verificar_integridade(m),
                         "hash_armazenado": m.hash_sha256})
