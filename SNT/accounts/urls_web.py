from django.urls import path
from .views import LoginView, ProfileView, UsersListView

urlpatterns = [
    path('login/', LoginView.as_view(), name='login'),
    path('profile/', ProfileView.as_view(), name='profile'),
    path('users/', UsersListView.as_view(), name='users-list'),
]