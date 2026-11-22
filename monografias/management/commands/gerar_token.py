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
        parser.add_argument('--curso-id', type=int, help='Curso autorizado do aluno.')
        parser.add_argument('--orientador-id', type=int, help='Orientador autorizado do aluno.')
        parser.add_argument('--curso-autorizado', type=int, action='append', default=[],
                            help='Curso da coordenação; pode ser repetido.')

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
        if role == 'aluno' and (not opts['curso_id'] or not opts['orientador_id']):
            raise CommandError('role=aluno exige --curso-id e --orientador-id.')
        if role == 'coordenador' and not opts['curso_autorizado']:
            raise CommandError('role=coordenador exige --curso-autorizado.')

        t = AccessToken()
        t["user_id"] = opts["id"]
        t["role"] = role
        t["aluno_id"] = aluno_id
        t["professor_id"] = professor_id
        t['curso_id'] = opts['curso_id']
        t['orientador_id'] = opts['orientador_id']
        t['curso_ids'] = opts['curso_autorizado'] if role == 'coordenador' else []
        self.stdout.write(str(t))
