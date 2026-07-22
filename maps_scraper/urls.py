from django.contrib import admin
from django.urls import path
from scraper import views

urlpatterns = [
    path('admin/', admin.site.urls),

    # Dashboard
    path('', views.home, name='home'),

    # Bing Maps scraping
    path('bing-maps/', views.bing_maps_home, name='bing_maps_home'),

    # Search engine scraping
    path('search/', views.search_home, name='search_home'),
    path('search/<int:job_id>/', views.search_job_detail, name='search_job_detail'),

    # Leads
    path('leads/', views.leads, name='leads'),
    path('leads/<int:listing_id>/delete/', views.delete_lead, name='delete_lead'),
    path('results/', views.results, name='results'),  # legacy redirect

    # Email campaigns
    path('campaigns/', views.campaigns, name='campaigns'),
    path('campaigns/new/', views.new_campaign, name='new_campaign'),
    path('campaigns/<int:campaign_id>/', views.campaign_detail, name='campaign_detail'),
    path('campaigns/<int:campaign_id>/send/', views.send_campaign_view, name='send_campaign'),
    path('campaigns/<int:campaign_id>/stop/', views.stop_campaign_view, name='stop_campaign'),
    path('campaigns/<int:campaign_id>/resend/', views.resend_campaign, name='resend_campaign'),
    path('campaigns/<int:campaign_id>/schedule/', views.schedule_campaign, name='schedule_campaign'),
    path('campaigns/<int:campaign_id>/unschedule/', views.unschedule_campaign, name='unschedule_campaign'),
    path('campaigns/<int:campaign_id>/delete/', views.delete_campaign, name='delete_campaign'),

    # SMTP Profiles
    path('smtp/', views.smtp_profiles, name='smtp_profiles'),
    path('smtp/create/', views.create_smtp_profile, name='create_smtp_profile'),
    path('smtp/<int:profile_id>/delete/', views.delete_smtp_profile, name='delete_smtp_profile'),
    path('api/smtp/<int:profile_id>/', views.api_smtp_profile, name='api_smtp_profile'),

    # Downloads – all leads
    path('download/', views.download_csv, name='download_csv'),
    path('download/phone/', views.download_phone_csv, name='download_phone_csv'),
    path('download/email/', views.download_email_csv, name='download_email_csv'),
    path('download/website/', views.download_website_csv, name='download_website_csv'),

    # API
    path('api/jobs/recent/', views.api_recent_jobs, name='api_recent_jobs'),
    path('api/jobs/<int:job_id>/', views.api_job_status, name='api_job_status'),
    path('api/ai/templates/', views.api_ai_templates, name='api_ai_templates'),
    path('api/ai/scores/', views.api_lead_scores, name='api_lead_scores'),

    # Job detail & controls (maps)
    path('jobs/<int:job_id>/', views.job_detail, name='job_detail'),
    path('jobs/<int:job_id>/download/', views.download_job_csv, name='download_job_csv'),
    path('jobs/<int:job_id>/download/phone/', views.download_job_phone_csv, name='download_job_phone_csv'),
    path('jobs/<int:job_id>/download/email/', views.download_job_email_csv, name='download_job_email_csv'),
    path('jobs/<int:job_id>/download/website/', views.download_job_website_csv, name='download_job_website_csv'),
    path('jobs/<int:job_id>/pause/', views.pause_job, name='pause_job'),
    path('jobs/<int:job_id>/resume/', views.resume_job, name='resume_job'),
    path('jobs/<int:job_id>/stop/', views.stop_job, name='stop_job'),
    path('jobs/<int:job_id>/delete/', views.delete_job, name='delete_job'),
]
