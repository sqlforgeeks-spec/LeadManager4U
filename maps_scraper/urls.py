"""
URL configuration for maps_scraper project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
 
from scraper import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home, name='home'),
    path('results/', views.results, name='results'),
    path('download/', views.download_csv, name='download_csv'),
    path('download/phone/', views.download_phone_csv, name='download_phone_csv'),
    path('download/email/', views.download_email_csv, name='download_email_csv'),
    path('download/website/', views.download_website_csv, name='download_website_csv'),
    path('api/jobs/recent/', views.api_recent_jobs, name='api_recent_jobs'),
    path('api/jobs/<int:job_id>/', views.api_job_status, name='api_job_status'),
    path('jobs/<int:job_id>/', views.job_detail, name='job_detail'),
    path('jobs/<int:job_id>/download/', views.download_job_csv, name='download_job_csv'),
    path('jobs/<int:job_id>/download/phone/', views.download_job_phone_csv, name='download_job_phone_csv'),
    path('jobs/<int:job_id>/download/email/', views.download_job_email_csv, name='download_job_email_csv'),
    path('jobs/<int:job_id>/download/website/', views.download_job_website_csv, name='download_job_website_csv'),
    path('jobs/<int:job_id>/pause/', views.pause_job, name='pause_job'),
    path('jobs/<int:job_id>/resume/', views.resume_job, name='resume_job'),
    path('jobs/<int:job_id>/stop/', views.stop_job, name='stop_job'),
]
