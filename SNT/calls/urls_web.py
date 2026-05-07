from django.urls import path
from .views import CallListView, CallDetailView, CallStatsView

urlpatterns = [
    path('calls/', CallListView.as_view(), name='call-list'),
    path('calls/<int:pk>/', CallDetailView.as_view(), name='call-detail'),
    path('stats/', CallStatsView.as_view(), name='call-stats'),
]