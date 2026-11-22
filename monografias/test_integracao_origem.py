"""Garante IDs de origem idempotentes e download privado com tokens do principal."""
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Monografia


class OrigemMonografiaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_user('aluno-integracao-micro')
        cls.professor = get_user_model().objects.create_user('prof-integracao-micro')

    def setUp(self):
        self.client = APIClient()
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.tmp.name)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def autenticar(self, role='aluno', aluno_id=1, professor_id=None, scope='integracao:origem'):
        user = self.professor if role == 'orientador' else self.usuario
        self.client.force_authenticate(user=user, token={
            'role': role, 'aluno_id': aluno_id, 'professor_id': professor_id, 'scope': scope,
        })

    def criar(self, origem_id=58):
        return self.client.post('/api/monografias/', {
            'origem_id': origem_id, 'titulo': 'Monografia Principal', 'resumo': 'Resumo',
            'aluno_nome': 'Aluno', 'orientador_id': 11, 'curso_id': 3,
        }, format='json')

    def test_criacao_idempotente_com_escopo(self):
        self.autenticar()
        r1 = self.criar()
        self.assertEqual(r1.status_code, 201, r1.data)
        r2 = self.criar()
        self.assertEqual(r2.status_code, 200, r2.data)
        self.assertEqual(r1.data['id'], r2.data['id'])
        self.assertEqual(Monografia.objects.filter(origem_id=58).count(), 1)

    def test_nao_vincula_origem_sem_escopo(self):
        self.autenticar(scope='')
        self.assertEqual(self.criar().status_code, 403)
        self.assertFalse(Monografia.objects.exists())

    def test_nao_apropria_origem_de_outro_aluno(self):
        self.autenticar(aluno_id=1)
        self.assertEqual(self.criar().status_code, 201)
        self.autenticar(aluno_id=2)
        self.assertEqual(self.criar().status_code, 403)
        self.assertEqual(Monografia.objects.count(), 1)

    def test_edicao_vinculada_exige_escopo(self):
        self.autenticar()
        pk = self.criar().data['id']
        self.autenticar(scope='')
        self.assertEqual(self.client.patch(f'/api/monografias/{pk}/', {'titulo': 'Mudança'}, format='json').status_code, 403)
        self.autenticar()
        self.assertEqual(self.client.patch(f'/api/monografias/{pk}/', {'titulo': 'Mudança'}, format='json').status_code, 200)

    def test_pdf_privado_para_aluno_e_orientador(self):
        self.autenticar()
        pk = self.criar().data['id']
        m = Monografia.objects.get(pk=pk)
        m.arquivo = SimpleUploadedFile('trabalho.pdf', b'%PDF-1.7 arquivo privado')
        m.save()
        resposta_aluno = self.client.get(f'/api/monografias/{pk}/baixar-pdf/')
        self.assertEqual(resposta_aluno.status_code, 200)
        self.assertEqual(b''.join(resposta_aluno.streaming_content), b'%PDF-1.7 arquivo privado')
        self.autenticar(role='orientador', professor_id=11)
        resposta_prof = self.client.get(f'/api/monografias/{pk}/baixar-pdf/')
        self.assertEqual(resposta_prof.status_code, 200)
        self.assertEqual(b''.join(resposta_prof.streaming_content), b'%PDF-1.7 arquivo privado')
        self.autenticar(role='orientador', professor_id=999)
        self.assertEqual(self.client.get(f'/api/monografias/{pk}/baixar-pdf/').status_code, 404)
        self.autenticar(role='aluno', aluno_id=999)
        self.assertEqual(self.client.get(f'/api/monografias/{pk}/baixar-pdf/').status_code, 404)

    def test_acao_em_registro_vinculado_exige_ponte(self):
        self.autenticar()
        pk = self.criar().data['id']
        self.autenticar(scope='')
        pdf = SimpleUploadedFile('trabalho.pdf', b'%PDF-1.7 micro')
        self.assertEqual(self.client.post(f'/api/monografias/{pk}/upload/', {'arquivo': pdf}).status_code, 403)
        self.assertEqual(self.client.post(f'/api/monografias/{pk}/submeter/').status_code, 403)
        self.assertEqual(self.client.delete(f'/api/monografias/{pk}/').status_code, 403)
        self.autenticar(role='orientador', professor_id=11, scope='')
        self.assertEqual(self.client.post(f'/api/monografias/{pk}/aprovar-orientador/').status_code, 403)
