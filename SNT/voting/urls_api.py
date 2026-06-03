from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import VotingSessionViewSet, QuestionViewSet, AnswerOptionViewSet

router = DefaultRouter()
router.register(r'sessions', VotingSessionViewSet, basename='voting-session')
router.register(r'questions', QuestionViewSet, basename='voting-question')
router.register(r'options', AnswerOptionViewSet, basename='voting-option')

urlpatterns = [
    path('', include(router.urls)),
]