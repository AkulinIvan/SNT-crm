import os
from django.http import FileResponse, Http404
from django.conf import settings
from django.shortcuts import render
from django.views import View
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from .models import CallRecord
from .serializers import (
    CallRecordListSerializer,
    CallRecordDetailSerializer,
    CallRecordCreateSerializer,
    CallRecordUpdateSerializer,
)
from .asterisk_service import AsteriskService


class CallRecordViewSet(viewsets.ModelViewSet):
    """
    ViewSet для работы с записями звонков.
    
    list            — список всех звонков
    retrieve        — детальная карточка звонка
    create          — создание (вебхук Asterisk)
    partial_update  — редактирование оператором
    destroy         — удаление
    
    Дополнительные actions:
    {id}/download/      — скачать аудиофайл
    {id}/fetch-recording/ — загрузить запись из Asterisk
    {id}/add-tag/       — добавить тег
    {id}/remove-tag/    — удалить тег
    originate/          — инициировать исходящий звонок через AMI
    stats/              — статистика звонков
    """
    queryset = CallRecord.objects.select_related('owner', 'land_plot')
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = [
        'direction', 'status', 'is_important', 'owner', 'land_plot',
    ]
    search_fields = [
        'caller_number', 'called_number', 'operator_note', 'tags',
        'owner__full_name',
    ]
    ordering_fields = ['started_at', 'duration_seconds', 'created_at']
    ordering = ['-started_at']

    def get_serializer_class(self):
        if self.action == 'list':
            return CallRecordListSerializer
        elif self.action == 'create':
            return CallRecordCreateSerializer
        elif self.action in ('update', 'partial_update'):
            return CallRecordUpdateSerializer
        return CallRecordDetailSerializer

    # ------------------------------------------------------------------
    # Скачивание и загрузка записей
    # ------------------------------------------------------------------

    @action(detail=True, methods=['get'], url_path='download')
    def download(self, request, pk=None):
        """
        GET /api/calls/{id}/download/
        Скачать аудиофайл записи.
        """
        call = self.get_object()
        if not call.audio_file:
            raise Http404('Аудиофайл отсутствует.')
        file_path = os.path.join(settings.MEDIA_ROOT, call.audio_file.name)
        if not os.path.exists(file_path):
            raise Http404('Файл не найден на диске.')
        return FileResponse(
            open(file_path, 'rb'),
            content_type='audio/wav',
            as_attachment=True,
            filename=os.path.basename(file_path),
        )

    @action(detail=True, methods=['post'], url_path='fetch-recording')
    def fetch_recording(self, request, pk=None):
        """
        POST /api/calls/{id}/fetch-recording/
        Загрузить запись из Asterisk по asterisk_recording_file.
        """
        call = self.get_object()
        if not call.asterisk_recording_file:
            return Response(
                {'detail': 'Не указан asterisk_recording_file.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        service = AsteriskService()
        relative_path = service.fetch_recording(call.asterisk_recording_file)
        if not relative_path:
            return Response(
                {'detail': 'Не удалось загрузить файл с сервера Asterisk.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        call.audio_file.name = relative_path
        call.save(update_fields=['audio_file'])
        return Response(CallRecordDetailSerializer(call).data)

    # ------------------------------------------------------------------
    # Управление тегами
    # ------------------------------------------------------------------

    @action(detail=True, methods=['post'], url_path='add-tag')
    def add_tag(self, request, pk=None):
        """
        POST /api/calls/{id}/add-tag/
        Тело: {"tag": "жалоба"}
        """
        call = self.get_object()
        tag = request.data.get('tag', '').strip()
        if not tag:
            return Response(
                {'detail': 'Необходимо указать tag.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        call.add_tag(tag)
        call.save(update_fields=['tags'])
        return Response(CallRecordDetailSerializer(call).data)

    @action(detail=True, methods=['post'], url_path='remove-tag')
    def remove_tag(self, request, pk=None):
        """
        POST /api/calls/{id}/remove-tag/
        Тело: {"tag": "жалоба"}
        """
        call = self.get_object()
        tag = request.data.get('tag', '').strip()
        if not tag:
            return Response(
                {'detail': 'Необходимо указать tag.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        call.remove_tag(tag)
        call.save(update_fields=['tags'])
        return Response(CallRecordDetailSerializer(call).data)

    # ------------------------------------------------------------------
    # Исходящий звонок
    # ------------------------------------------------------------------

    @action(detail=False, methods=['post'], url_path='originate')
    def originate(self, request):
        """
        POST /api/calls/originate/
        Инициировать исходящий звонок через Asterisk AMI.
        Тело: {"caller_number": "79161234567", "extension": "100", "land_plot_id": 1}
        """
        caller_number = request.data.get('caller_number')
        extension = request.data.get('extension')
        land_plot_id = request.data.get('land_plot_id')
        if not caller_number or not extension:
            return Response(
                {'detail': 'Необходимо указать caller_number и extension.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        service = AsteriskService()
        uniqueid = service.originate_call(caller_number, extension, land_plot_id)
        return Response(
            {'detail': 'Звонок инициирован.', 'asterisk_uniqueid': uniqueid},
            status=status.HTTP_202_ACCEPTED,
        )

    # ------------------------------------------------------------------
    # Статистика
    # ------------------------------------------------------------------

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        """
        GET /api/calls/stats/
        Статистика по звонкам: общее количество, по направлениям, по статусам.
        """
        from django.db.models import Count, Sum
        total = CallRecord.objects.count()
        base_qs = CallRecord.objects
        data = {
            'total': total,
            'total_duration_seconds': base_qs.aggregate(s=Sum('duration_seconds'))['s'] or 0,
            'by_direction': dict(base_qs.values_list('direction').annotate(c=Count('id'))),
            'by_status': dict(base_qs.values_list('status').annotate(c=Count('id'))),
            'important': base_qs.filter(is_important=True).count(),
            'with_recording': base_qs.exclude(audio_file='').count(),
        }
        return Response(data)

    # ------------------------------------------------------------------
    # Вебхук для Asterisk (создание записи при Hangup)
    # ------------------------------------------------------------------

    @action(detail=False, methods=['post'], url_path='webhook/hangup')
    def webhook_hangup(self, request):
        """
        POST /api/calls/webhook/hangup/
        
        Принимает данные о завершённом звонке от Asterisk (AGI-скрипт или ARI).
        Ожидаемые поля:
        {
            "caller_number": "79161234567",
            "called_number": "102",
            "direction": "in",
            "started_at": "2024-01-15T12:00:00Z",
            "answered_at": "2024-01-15T12:00:10Z",
            "ended_at": "2024-01-15T12:05:00Z",
            "duration_seconds": 290,
            "asterisk_uniqueid": "1234567890.123",
            "asterisk_channel": "SIP/trunk-00000001",
            "asterisk_recording_file": "2024/01/15/in-1234567890.123.wav"
        }
        """
        serializer = CallRecordCreateSerializer(data=request.data)
        if serializer.is_valid():
            call = serializer.save()
            # Автоматически пытаемся загрузить запись
            if call.asterisk_recording_file:
                service = AsteriskService()
                relative_path = service.fetch_recording(call.asterisk_recording_file)
                if relative_path:
                    call.audio_file.name = relative_path
                    call.save(update_fields=['audio_file'])
            return Response(
                CallRecordDetailSerializer(call).data,
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    
class CallListView(View):
    """Страница со списком звонков."""
    def get(self, request):
        return render(request, 'calls/list.html', {'active_page': 'calls'})


class CallDetailView(View):
    """Страница карточки звонка."""
    def get(self, request, pk):
        return render(request, 'calls/detail.html', {
            'active_page': 'calls',
            'call_id': pk,
        })


class CallStatsView(View):
    """Страница статистики звонков."""
    def get(self, request):
        return render(request, 'calls/stats.html', {'active_page': 'stats'})