import json

import pika
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Publica um evento na exchange eventos_externos (simula outros serviços)"

    def add_arguments(self, parser):
        parser.add_argument("routing_key")
        parser.add_argument("payload", help="JSON, ex.: '{\"monografia_id\": 1}'")

    def handle(self, *args, **opts):
        try:
            json.loads(opts["payload"])
        except ValueError:
            raise CommandError("payload não é um JSON válido")
        props = pika.BasicProperties(delivery_mode=2, content_type="application/json")
        with pika.BlockingConnection(pika.URLParameters(settings.RABBITMQ_URL)) as conn:
            ch = conn.channel()
            ch.exchange_declare("eventos_externos", "topic", durable=True)
            ch.basic_publish("eventos_externos", opts["routing_key"],
                             opts["payload"].encode(), props)
        self.stdout.write(f"publicado: {opts['routing_key']} {opts['payload']}")
