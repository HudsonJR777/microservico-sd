from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('monografias', '0003_alter_historicalmonografia_status_and_more')]

    operations = [
        migrations.AddField(
            model_name='monografia', name='origem_id',
            field=models.PositiveIntegerField(null=True, blank=True, unique=True, db_index=True),
        ),
        migrations.AddField(
            model_name='historicalmonografia', name='origem_id',
            field=models.PositiveIntegerField(null=True, blank=True, db_index=True),
        ),
    ]
