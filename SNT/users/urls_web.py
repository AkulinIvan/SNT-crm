from django.urls import path

from land.views import DashboardView
from .views import OwnerListView, OwnerDetailView

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('owners/', OwnerListView.as_view(), name='owner-list'),
    path('owners/<int:pk>/', OwnerDetailView.as_view(), name='owner-detail'),
]