import json
import logging
import uuid
import pika
from django.conf import settings

log = logging.getLogger(__name__)
EXCHANGE = "monografias"


def publicar(routing_key: str, payload: dict):
    props = pika.BasicProperties(
        delivery_mode=2,                      # mensagem persistente
        content_type="application/json",
        message_id=str(uuid.uuid4()),
    )
    try:
        with pika.BlockingConnection(pika.URLParameters(settings.RABBITMQ_URL)) as conn:
            ch = conn.channel()
            ch.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)
            ch.basic_publish(EXCHANGE, routing_key, json.dumps(payload).encode(), props)
        log.info("Evento publicado: %s %s", routing_key, payload)
    except Exception:
        # Em produção, use o padrão Outbox para não perder o evento
        log.exception("Falha ao publicar %s", routing_key)