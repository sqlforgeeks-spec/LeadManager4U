from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('scraper', '0003_businesslisting_maps_url_and_more'),
    ]

    operations = [
        # Add source to ScrapeJob
        migrations.AddField(
            model_name='scrapejob',
            name='source',
            field=models.CharField(
                choices=[
                    ('maps', 'Google Maps'),
                    ('google', 'Google Search'),
                    ('bing', 'Bing'),
                    ('yahoo', 'Yahoo'),
                    ('duckduckgo', 'DuckDuckGo'),
                    ('yandex', 'Yandex'),
                ],
                default='maps',
                max_length=32,
            ),
        ),
        # Allow blank locations for search-engine jobs
        migrations.AlterField(
            model_name='scrapejob',
            name='locations',
            field=models.TextField(blank=True),
        ),
        # Add address and source to BusinessListing
        migrations.AddField(
            model_name='businesslisting',
            name='address',
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name='businesslisting',
            name='source',
            field=models.CharField(default='maps', max_length=32),
        ),
        # Extend website/maps_url max_length
        migrations.AlterField(
            model_name='businesslisting',
            name='website',
            field=models.URLField(blank=True, max_length=500),
        ),
        migrations.AlterField(
            model_name='businesslisting',
            name='maps_url',
            field=models.URLField(blank=True, max_length=500),
        ),
        # EmailCampaign
        migrations.CreateModel(
            name='EmailCampaign',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('subject', models.CharField(max_length=500)),
                ('body', models.TextField(help_text='Use {name}, {email}, {phone}, {website}, {location} as placeholders.')),
                ('from_name', models.CharField(blank=True, max_length=255)),
                ('from_email', models.EmailField()),
                ('reply_to', models.EmailField(blank=True)),
                ('smtp_host', models.CharField(default='smtp.gmail.com', max_length=255)),
                ('smtp_port', models.PositiveIntegerField(default=587)),
                ('smtp_user', models.CharField(max_length=255)),
                ('smtp_password', models.CharField(max_length=500)),
                ('use_tls', models.BooleanField(default=True)),
                ('status', models.CharField(
                    choices=[('draft', 'Draft'), ('sending', 'Sending'), ('sent', 'Sent'), ('failed', 'Failed')],
                    default='draft', max_length=32,
                )),
                ('total_sent', models.PositiveIntegerField(default=0)),
                ('total_failed', models.PositiveIntegerField(default=0)),
                ('total_skipped', models.PositiveIntegerField(default=0)),
                ('job_filter', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    help_text='If set, only send to leads from this job.',
                    to='scraper.scrapejob',
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        # EmailSend
        migrations.CreateModel(
            name='EmailSend',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('campaign', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='sends', to='scraper.emailcampaign',
                )),
                ('listing', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='email_sends', to='scraper.businesslisting',
                )),
                ('status', models.CharField(
                    choices=[('pending', 'Pending'), ('sent', 'Sent'), ('failed', 'Failed'), ('skipped', 'Skipped')],
                    default='pending', max_length=32,
                )),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('error', models.TextField(blank=True)),
            ],
            options={
                'unique_together': {('campaign', 'listing')},
            },
        ),
    ]
