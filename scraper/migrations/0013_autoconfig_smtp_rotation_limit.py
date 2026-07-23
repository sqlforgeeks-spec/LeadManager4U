from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('scraper', '0012_add_global_limits_ai_variation'),
    ]

    operations = [
        migrations.AddField(
            model_name='autoconfig',
            name='smtp_rotation_limit',
            field=models.PositiveIntegerField(
                default=0,
                help_text="Rotate to next SMTP after this many emails. 0 = Auto (uses each profile's own daily limit).",
            ),
        ),
    ]
