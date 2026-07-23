from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("scraper", "0013_autoconfig_smtp_rotation_limit"),
    ]

    operations = [
        migrations.AddField(
            model_name="businesslisting",
            name="follow_up_stage",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="Follow-up cadence stage: 1 tomorrow, 2 in 7 days, 3 in 14 days, then stopped.",
            ),
        ),
    ]