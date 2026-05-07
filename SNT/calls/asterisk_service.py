import logging
import os
import subprocess
from datetime import datetime
from typing import Optional
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger('calls.asterisk')

# Попытка импорта библиотек Asterisk (необязательно, если работаете через AGI/скрипты)
try:
    from asterisk.ami import AMIClient, SimpleAction
    AMI_AVAILABLE = True
except ImportError:
    AMI_AVAILABLE = False
    logger.warning('asterisk.ami не установлен — AMI-клиент недоступен.')


class AsteriskService:
    """
    Сервис для взаимодействия с Asterisk.
    
    Можно расширять под свои нужды: AMI-команды, скачивание записей,
    получение статуса каналов и т.д.
    
    Для продакшена часто используют не прямой AMI, а скрипт на событии 
    Hangup (через AGI или ARI), который сам создаёт запись через API CRM.
    """

    def __init__(self):
        self.ami_host = getattr(settings, 'ASTERISK_AMI_HOST', '127.0.0.1')
        self.ami_port = getattr(settings, 'ASTERISK_AMI_PORT', 5038)
        self.ami_user = getattr(settings, 'ASTERISK_AMI_USER', 'crm_user')
        self.ami_secret = getattr(settings, 'ASTERISK_AMI_SECRET', '')
        self.recording_base_path = getattr(
            settings,
            'ASTERISK_RECORDING_PATH',
            '/var/spool/asterisk/monitor/',
        )

    def fetch_recording(self, asterisk_filename: str) -> Optional[str]:
        """
        Копирует файл записи из хранилища Asterisk в медиа-каталог Django.
        
        Возвращает относительный путь, пригодный для FileField.save().
        """
        src_path = os.path.join(self.recording_base_path, asterisk_filename)
        if not os.path.exists(src_path):
            logger.error(f'Файл записи не найден: {src_path}')
            return None

        # Формируем путь назначения: media/call_recordings/YYYY/MM/filename
        now = timezone.now()
        dest_dir = os.path.join(
            settings.MEDIA_ROOT,
            'call_recordings',
            str(now.year),
            f'{now.month:02d}',
        )
        os.makedirs(dest_dir, exist_ok=True)

        # Уникальное имя, чтобы избежать конфликтов
        base_name = os.path.basename(asterisk_filename)
        dest_filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{base_name}"
        dest_path = os.path.join(dest_dir, dest_filename)

        try:
            subprocess.run(
                ['cp', src_path, dest_path],
                check=True,
                capture_output=True,
            )
            # Права, если нужно
            os.chmod(dest_path, 0o644)
        except subprocess.CalledProcessError as e:
            logger.error(f'Ошибка копирования файла: {e.stderr.decode()}')
            return None

        # Относительный путь для Django FileField
        relative_path = os.path.relpath(dest_path, settings.MEDIA_ROOT)
        logger.info(f'Запись сохранена: {relative_path}')
        return relative_path

    def originate_call(self, caller_number: str, extension: str, 
                       land_plot_id: Optional[int] = None) -> Optional[str]:
        """
        Инициирует исходящий звонок через AMI (Originate).
        
        Возвращает UniqueID канала или None при ошибке.
        Требует установленного asterisk.ami.
        """
        if not AMI_AVAILABLE:
            logger.error('AMI недоступен — звонок не инициирован.')
            return None

        client = AMIClient(
            address=self.ami_host,
            port=self.ami_port,
        )
        try:
            client.login(username=self.ami_user, secret=self.ami_secret)

            # Переменные канала для последующей идентификации в CRM
            channel_vars = {
                'CRM_CALLER_NUMBER': caller_number,
            }
            if land_plot_id:
                channel_vars['CRM_LAND_PLOT_ID'] = str(land_plot_id)

            action = SimpleAction(
                'Originate',
                Channel=f'Local/{caller_number}@from-crm',
                Exten=extension,
                Priority=1,
                Context='crm-outgoing',
                CallerID=caller_number,
                Variable=','.join(f'{k}={v}' for k, v in channel_vars.items()),
                Async='true',
            )
            response = client.send_action(action)
            client.logoff()
            logger.info(f'Результат Originate: {response}')
            # UniqueID обычно возвращается в событии, но не в ответе на Originate
            return response.keys().get('uniqueid')
        except Exception as e:
            logger.error(f'Ошибка AMI: {e}')
            return None

    def hangup_channel(self, channel: str) -> bool:
        """Завершить вызов на указанном канале."""
        if not AMI_AVAILABLE:
            return False
        client = AMIClient(address=self.ami_host, port=self.ami_port)
        try:
            client.login(username=self.ami_user, secret=self.ami_secret)
            action = SimpleAction('Hangup', Channel=channel)
            client.send_action(action)
            client.logoff()
            return True
        except Exception as e:
            logger.error(f'Ошибка Hangup: {e}')
            return False