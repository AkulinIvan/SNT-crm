from django.urls import path
from django.views.generic import TemplateView
from .views import VotingSessionViewSet

urlpatterns = [
    # Список голосований
    path('voting/', TemplateView.as_view(template_name='voting/list.html'), name='voting-list'),
    
    # Детали голосования
    path('voting/<int:voting_id>/', TemplateView.as_view(template_name='voting/detail.html'), name='voting-detail'),
    
    # Страница голосования
    path('voting/<int:voting_id>/vote/', TemplateView.as_view(template_name='voting/vote.html'), name='voting-vote'),
    
    # Результаты голосования
    path('voting/<int:voting_id>/results/', TemplateView.as_view(template_name='voting/results.html'), name='voting-results'),
    
    # Публичное голосование по токену
    path('voting/public/<str:token>/', TemplateView.as_view(template_name='voting/public_vote.html'), name='voting-public'),
    
    # Создание голосования
    path('voting/create/', TemplateView.as_view(template_name='voting/create.html'), name='voting-create'),
    
    # Редактирование голосования
    path('voting/<int:voting_id>/edit/', TemplateView.as_view(template_name='voting/edit.html'), name='voting-edit'),
]