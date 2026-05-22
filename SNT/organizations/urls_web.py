from django.urls import path
from . import views

urlpatterns = [
    path('', views.OrganizationListView.as_view(), name='organizations-list'),
    path('<int:organization_id>/', views.OrganizationDetailView.as_view(), name='organizations-detail'),
]