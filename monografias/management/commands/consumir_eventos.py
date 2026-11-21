import json
import logging
import time

import pika
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from monografias import services

log = logging.getLogger("consumidor")

EXCHANGE = "eventos_externos"
DLX = "eventos_externos.dlx"
FILA = "monografias.eventos_recebidos"
FILA_DLQ = "monografias.eventos_recebidos.dlq"
CHAVES = (
    "banca.agendada",
    "banca.cancelada",
    "banca.defesa_realizada",
    "banca.resultado_final",
    "repositorio.extracao_concluida",
)


def _callback(channel, method, props, body):
    chave = method.routing_key
    close_old_connections()  # evita conexão de banco "velha" num processo longo
    try:
        dados = json.loads(body)
        if not isinstance(dados, dict):
            raise services.EventoInvalido("payload não é um objeto JSON")
        resultado = services.aplicar_evento(chave, dados)
        log.info("%s -> %s", chave, resultado)
        channel.basic_ack(method.delivery_tag)
    except (ValueError, services.EventoInvalido) as e:
        log.warning("Evento inválido %s: %s (enviado para a DLQ)", chave, e)
        channel.basic_nack(method.delivery_tag, requeue=False)
    except Exception:
        log.exception("Erro processando %s (enviado para a DLQ)", chave)
        channel.basic_nack(method.delivery_tag, requeue=False)


def _preparar(ch):
    # Dead-letter: mensagens recusadas (nack sem requeue) caem na DLQ para inspeção
    ch.exchange_declare(DLX, "fanout", durable=True)
    ch.queue_declare(FILA_DLQ, durable=True)
    ch.queue_bind(FILA_DLQ, DLX)

    ch.exchange_declare(EXCHANGE, "topic", durable=True)
    ch.queue_declare(FILA, durable=True, arguments={"x-dead-letter-exchange": DLX})
    for chave in CHAVES:
        ch.queue_bind(FILA, EXCHANGE, chave)
    ch.basic_qos(prefetch_count=10)


class Command(BaseCommand):
    help = "Consome eventos de outros serviços (bancas, repositório)"

    def handle(self, *args, **opts):
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(name)s %(levelname)s %(message)s")
        while True:
            try:
                conn = pika.BlockingConnection(pika.URLParameters(settings.RABBITMQ_URL))
                ch = conn.channel()
                _preparar(ch)
                ch.basic_consume(FILA, _callback)
                log.info("Aguardando eventos em %s ...", FILA)
                ch.start_consuming()
            except pika.exceptions.AMQPConnectionError:
                log.warning("RabbitMQ indisponível, tentando de novo em 5s")
                time.sleep(5)
            except KeyboardInterrupt:
                log.info("Encerrando")
                break
