# Adds the "cancelled" status to IngestJob. Only the allowed choices change, not the table.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("prefkit", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ingestjob",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("running", "Running"),
                    ("succeeded", "Succeeded"),
                    ("failed", "Failed"),
                    ("cancelled", "Cancelled"),
                ],
                default="queued",
                max_length=10,
            ),
        ),
    ]
