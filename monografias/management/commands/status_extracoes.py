from django.core.management.base import BaseCommand
from monografias.models import ExtracaoPDF


class Command(BaseCommand):
    help = 'Exibe estado das extrações privadas, sem imprimir texto ou credenciais.'

    def handle(self, *args, **options):
        self.stdout.write(f'Pendentes: {ExtracaoPDF.objects.filter(processado_em__isnull=True).count()}')
        self.stdout.write(f'Concluídas: {ExtracaoPDF.objects.filter(processado_em__isnull=False).count()}')
        self.stdout.write(f'Sem texto extraível: {ExtracaoPDF.objects.filter(sem_texto=True).count()}')
        self.stdout.write(f'Tentativas esgotadas: {ExtracaoPDF.objects.filter(processado_em__isnull=True, falhas__gte=3).count()}')
