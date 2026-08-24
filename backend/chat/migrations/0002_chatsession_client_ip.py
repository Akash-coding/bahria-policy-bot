from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatsession",
            name="client_ip",
            field=models.GenericIPAddressField(blank=True, db_index=True, null=True),
        ),
    ]
