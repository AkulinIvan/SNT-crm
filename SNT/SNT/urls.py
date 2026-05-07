from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # API
    path('api/', include('land.urls_api')),
    path('api/', include('users.urls_api')),
    path('api/', include('calls.urls_api')),
    path('api/', include('payments.urls_api')),
    
    # Веб-интерфейс
    path('', include('users.urls_web')),
    path('', include('land.urls_web')),
    path('', include('calls.urls_web')),
    path('', include('payments.urls_web')),
]