# urls.py
from django.urls import path
from deploy.views import github_webhook

urlpatterns = [
    path('webhook/github/', github_webhook, name='github_webhook'),
]