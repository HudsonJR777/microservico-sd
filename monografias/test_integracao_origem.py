"""Garante IDs de origem idempotentes e download privado com tokens do principal."""
import hashlib
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
            'curso_id': 3, 'orientador_id': 11,
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
        m.hash_sha256 = hashlib.sha256(b'%PDF-1.7 arquivo privado').hexdigest()
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

    def test_nao_altera_vinculos_fora_dos_claims(self):
        self.autenticar()
        pk = self.criar().data['id']
        for campo in ('curso_id', 'orientador_id'):
            resposta = self.client.patch(f'/api/monografias/{pk}/', {campo: 999}, format='json')
            self.assertEqual(resposta.status_code, 403)

    def test_download_detecta_manipulacao_e_indice_e_privado(self):
        self.autenticar()
        pk = self.criar().data['id']
        m = Monografia.objects.get(pk=pk)
        m.arquivo = SimpleUploadedFile('trabalho.pdf', b'%PDF-1.7 adulterado')
        m.hash_sha256 = '0' * 64
        m.save()
        self.assertEqual(self.client.get(f'/api/monografias/{pk}/baixar-pdf/').status_code, 409)
        self.autenticar(aluno_id=999)
        self.assertEqual(self.client.get(f'/api/monografias/{pk}/extracoes/').status_code, 404)

    def test_coordenador_consulta_apenas_cursos_do_token(self):
        Monografia.objects.create(titulo='Curso autorizado', aluno_id=1, orientador_id=11, curso_id=3)
        Monografia.objects.create(titulo='Outro curso', aluno_id=2, orientador_id=22, curso_id=4)
        self.client.force_authenticate(user=self.usuario, token={'role': 'coordenador', 'curso_ids': [3]})
        resposta = self.client.get('/api/monografias/')
        self.assertEqual(len(resposta.data), 1)
        self.assertEqual(resposta.data[0]['curso_id'], 3)
        self.assertEqual(self.client.patch(f"/api/monografias/{resposta.data[0]['id']}/", {'titulo': 'Invadido'}).status_code, 403)

    def test_jwt_invalido_e_expirado_nao_acessam_pdf(self):
        from datetime import timedelta
        from rest_framework_simplejwt.tokens import AccessToken
        token = AccessToken()
        token['user_id'] = self.usuario.pk
        token['role'] = 'aluno'
        token['aluno_id'] = 1
        token.set_exp(lifetime=timedelta(seconds=-1))
        self.client.force_authenticate(user=None)
        for valor in ('invalido', str(token)):
            self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + valor)
            self.assertEqual(self.client.get('/api/monografias/1/baixar-pdf/').status_code, 401)

    def test_admin_do_microservico_bloqueia_rede_externa_e_xff_forjado(self):
        self.assertEqual(self.client.get('/admin/login/', REMOTE_ADDR='203.0.113.9',
            HTTP_X_FORWARDED_FOR='127.0.0.1').status_code, 403)

    def test_emissor_local_inclui_vinculos_exigidos_sem_escopo_de_integracao(self):
        from io import StringIO
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from rest_framework_simplejwt.tokens import AccessToken
        with self.assertRaises(CommandError):
            call_command('gerar_token', id=1, role='aluno', stdout=StringIO())
        saida = StringIO()
        call_command('gerar_token', id=1, role='aluno', curso_id=3, orientador_id=11, stdout=saida)
        token = AccessToken(saida.getvalue().strip())
        self.assertEqual(token['curso_id'], 3)
        self.assertEqual(token['orientador_id'], 11)
        self.assertNotIn('scope', token)
