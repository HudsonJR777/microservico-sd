from django.db import migrations


def preparar(apps, schema_editor):
    Monografia = apps.get_model('monografias', 'Monografia')
    Extracao = apps.get_model('monografias', 'ExtracaoPDF')
    registros = Monografia.objects.using(schema_editor.connection.alias).filter(
        submetida_em__isnull=False,
    ).exclude(status__in=['RASCUNHO', 'AJUSTES_SOLICITADOS']).exclude(arquivo='').exclude(hash_sha256='')
    for m in registros.iterator():
        Extracao.objects.using(schema_editor.connection.alias).get_or_create(
            monografia_id=m.pk, arquivo_nome=str(m.arquivo), sha256=m.hash_sha256,
            defaults={'criado_em': m.submetida_em},
        )


class Migration(migrations.Migration):
    dependencies = [('monografias', '0005_extracaopdf')]
    operations = [migrations.RunPython(preparar, migrations.RunPython.noop)]
