from django.core.management.base import BaseCommand, CommandError
from rest_framework_simplejwt.tokens import AccessToken


class Command(BaseCommand):
    help = "Gera um JWT de desenvolvimento (simula o emissor do CRUD)"

    def add_arguments(self, parser):
        parser.add_argument("--id", type=int, required=True,
                            help="auth.User.pk (claim user_id)")
        parser.add_argument("--role", required=True,
                            choices=["aluno", "orientador", "coordenador", "admin"])
        parser.add_argument("--aluno-id", type=int, default=None,
                            help="Aluno.pk (padrao: o valor de --id, se role=aluno)")
        parser.add_argument("--professor-id", type=int, default=None,
                            help="Professor.pk (padrao: o valor de --id, se role=orientador/coordenador)")

    def handle(self, *args, **opts):
        role = opts["role"]
        aluno_id = opts["aluno_id"]
        professor_id = opts["professor_id"]

        # Atalho de desenvolvimento: se nao informar, assume o mesmo numero de --id
        if role == "aluno" and aluno_id is None:
            aluno_id = opts["id"]
        if role in ("orientador", "coordenador") and professor_id is None:
            professor_id = opts["id"]

        if role == "aluno" and aluno_id is None:
            raise CommandError("role=aluno exige --aluno-id")

        t = AccessToken()
        t["user_id"] = opts["id"]
        t["role"] = role
        t["aluno_id"] = aluno_id
        t["professor_id"] = professor_id
        self.stdout.write(str(t))
