from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # API
    path('api/', include('land.urls_api')),
    path('api/', include('users.urls_api')),
    path('api/', include('calls.urls_api')),
    path('api/', include('payments.urls_api')),
    path('api/', include('accounts.urls_api')),
    
    # Веб-интерфейс
    path('', include('users.urls_web')),
    path('', include('land.urls_web')),
    path('', include('calls.urls_web')),
    path('', include('payments.urls_web')),
    path('', include('accounts.urls_web')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)