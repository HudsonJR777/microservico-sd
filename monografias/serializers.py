from rest_framework import serializers
from .models import Monografia


class MonografiaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Monografia
        fields = "__all__"
        read_only_fields = ["aluno_id", "arquivo", "hash_sha256", "tamanho_bytes",
                            "status", "aprovada_orientador", "aprovada_orientador_em",
                            "criado_em", "submetida_em"]