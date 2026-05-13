from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import AuthViewSet, UserViewSet, UserActionLogViewSet

router = DefaultRouter()
router.register(r'users', UserViewSet, basename='user')
router.register(r'audit-log', UserActionLogViewSet, basename='audit-log')

urlpatterns = [
    path('', include(router.urls)),
    path('auth/', AuthViewSet.as_view({
        'post': 'login_view',
        'get': 'me',
    }), name='auth'),
    path('auth/login/', AuthViewSet.as_view({'post': 'login_view'}), name='auth-login'),
    path('auth/logout/', AuthViewSet.as_view({'post': 'logout_view'}), name='auth-logout'),
    path('auth/me/', AuthViewSet.as_view({'get': 'me'}), name='auth-me'),
    path('auth/change-password/', AuthViewSet.as_view({'post': 'change_password'}), name='auth-change-password'),
]