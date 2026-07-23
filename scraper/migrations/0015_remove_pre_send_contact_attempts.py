from django.db import migrations


def remove_pre_send_contact_attempts(apps, schema_editor):
    ContactAttempt = apps.get_model("scraper", "ContactAttempt")
    ContactAttempt.objects.filter(notes__startswith="Added to campaign:").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("scraper", "0014_businesslisting_follow_up_stage"),
    ]

    operations = [
        migrations.RunPython(remove_pre_send_contact_attempts, migrations.RunPython.noop),
    ]