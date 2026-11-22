import hashlib
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from .extracao import dados_evento, extrair_evento, registrar_falha, MensagemInvalida
from .messaging.extracoes import despachar_pendentes
from .models import ExtracaoPDF, Monografia
from .services import anexar_arquivo, aprovar_orientador, solicitar_ajustes, submeter


def pdf(texto='Versao original'):
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    if texto:
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f'BT /F1 12 Tf 50 780 Td ({texto}) Tj ET'.encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ExtracaoTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        config = override_settings(MEDIA_ROOT=temp.name)
        config.enable()
        self.addCleanup(config.disable)
        self.m = Monografia.objects.create(titulo='Teste', aluno_id=1, orientador_id=11, curso_id=3)

    def enviar(self, conteudo=None):
        anexar_arquivo(self.m, SimpleUploadedFile('trabalho.pdf', conteudo if conteudo is not None else pdf()))
        submeter(self.m)
        self.m.refresh_from_db()
        return self.m.extracoes.latest('criado_em')

    def test_submissao_nao_extrai_nem_conecta_broker(self):
        with (patch('monografias.extracao.PdfReader', side_effect=AssertionError('Extração no HTTP')),
              patch('monografias.services.publicar', side_effect=AssertionError('Broker no HTTP'))):
            evento = self.enviar()
        self.assertEqual(self.m.status, 'SUBMETIDA')
        self.assertIsNone(evento.processado_em)
        self.assertEqual(evento.texto, '')

    def test_extraido_idempotente_e_vinculado_ao_hash(self):
        evento = self.enviar()
        self.assertTrue(extrair_evento(dados_evento(evento)))
        self.assertFalse(extrair_evento(dados_evento(evento)))
        evento.refresh_from_db()
        self.assertIn('Versao original', evento.texto)
        self.assertEqual(evento.sha256, self.m.hash_sha256)
        self.assertFalse(evento.sem_texto)

    def test_pdf_sem_texto_finaliza_sem_ocr(self):
        evento = self.enviar(pdf(''))
        extrair_evento(dados_evento(evento))
        evento.refresh_from_db()
        self.assertTrue(evento.sem_texto)
        self.assertIsNotNone(evento.processado_em)

    def test_reenvio_preserva_versao_antiga_antes_de_extrair(self):
        primeiro = self.enviar()
        solicitar_ajustes(self.m, 11, 'Revisar')
        segundo = self.enviar(pdf('Versao revisada'))
        self.assertNotEqual(primeiro.arquivo_nome, segundo.arquivo_nome)
        for evento, texto in ((primeiro, 'original'), (segundo, 'revisada')):
            extrair_evento(dados_evento(evento))
            evento.refresh_from_db()
            self.assertIn(texto, evento.texto)
        self.assertEqual(ExtracaoPDF.objects.count(), 2)

    def test_falha_temporaria_registrada_e_retoma_sem_rejeitar_submissao(self):
        evento = self.enviar()
        with patch('monografias.extracao.PdfReader', side_effect=OSError('temporária')):
            with self.assertRaises(OSError) as erro:
                extrair_evento(dados_evento(evento))
        registrar_falha(dados_evento(evento), erro.exception)
        evento.refresh_from_db()
        self.assertEqual(evento.falhas, 1)
        self.assertEqual(self.m.status, 'SUBMETIDA')
        self.assertTrue(extrair_evento(dados_evento(evento)))

    def test_broker_indisponivel_mantem_outbox(self):
        evento = self.enviar()
        canal = Mock()
        canal.basic_publish.side_effect = OSError('offline')
        self.assertEqual(despachar_pendentes(canal), 0)
        evento.refresh_from_db()
        self.assertIsNone(evento.enviado_em)
        self.assertEqual(evento.ultimo_erro, 'OSError')

    def test_hash_adulterado_impede_extracao_e_aprovacao(self):
        evento = self.enviar()
        with self.m.arquivo.storage.open(evento.arquivo_nome, 'wb') as arquivo:
            arquivo.write(pdf('Manipulado'))
        with self.assertRaises(ValueError):
            extrair_evento(dados_evento(evento))
        from .services import RegraDeNegocio
        with self.assertRaises(RegraDeNegocio):
            aprovar_orientador(self.m, 11)
        with self.assertRaises(MensagemInvalida):
            extrair_evento({**dados_evento(evento), 'sha256': '0' * 64})

    def test_pdf_invalido_nao_cria_versao_nem_submissao(self):
        from django.core.exceptions import ValidationError
        for conteudo in (b'%PDF-1.7 falso', b'nao pdf'):
            with self.assertRaises(ValidationError):
                self.enviar(conteudo)
        self.m.refresh_from_db()
        self.assertEqual(self.m.status, 'RASCUNHO')
        self.assertFalse(ExtracaoPDF.objects.exists())
