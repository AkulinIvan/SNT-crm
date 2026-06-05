# SNT/payments/qr_generator.py - с подробным логированием и обработкой ошибок

"""
Генератор QR-кодов для квитанций по стандарту ГОСТ Р 56042-2014.
Формат: ST00012|Name=...|PersonalAcc=...|BankName=...|BIC=...|CorrespAcc=...|...
"""

import re
import base64
import logging
import traceback
from decimal import Decimal
from typing import Optional, Dict, Any
from io import BytesIO
from django.conf import settings

logger = logging.getLogger(__name__)


def get_current_organization_details(request=None) -> Optional[Dict[str, Any]]:
    """
    Получение реквизитов текущей организации.
    
    Args:
        request: HTTP request object с атрибутом current_organization
        
    Returns:
        Dict с реквизитами организации или None
    """
    logger.debug("Getting current organization details")
    
    try:
        from organizations.models import Organization
        
        org = None
        
        # Пытаемся получить организацию из запроса
        if request and hasattr(request, 'current_organization') and request.current_organization:
            org = request.current_organization
            logger.debug(f"Organization from request: {org.name if org else 'None'}")
        
        # Если не нашли в запросе, ищем в БД
        if not org:
            try:
                from django.db import connection
                # Проверяем, существует ли таблица организаций
                with connection.cursor() as cursor:
                    cursor.execute("""
                        SELECT EXISTS (
                            SELECT FROM information_schema.tables 
                            WHERE table_name = 'organizations_organization'
                        )
                    """)
                    table_exists = cursor.fetchone()[0]
                
                if table_exists:
                    org = Organization.objects.filter(is_active=True).first()
                    if org:
                        logger.info(f"Found active organization in DB: {org.name}")
                    else:
                        logger.warning("No active organization found in DB")
                else:
                    logger.warning("organizations_organization table does not exist")
                    
            except Exception as e:
                logger.warning(f"Error checking organization table: {e}")
        
        if not org:
            logger.warning("No organization found, using settings fallback")
            return None
        
        # Формируем словарь с реквизитами
        details = {
            'name': org.name,
            'short_name': org.short_name if hasattr(org, 'short_name') and org.short_name else org.name,
            'inn': org.inn if hasattr(org, 'inn') else '',
            'kpp': org.kpp if hasattr(org, 'kpp') else '',
            'account': org.bank_account if hasattr(org, 'bank_account') else '',
            'bank_name': org.bank_name if hasattr(org, 'bank_name') else '',
            'bank_bik': org.bank_bik if hasattr(org, 'bank_bik') else '',
            'bank_corr': org.bank_corr_account if hasattr(org, 'bank_corr_account') else '',
            'chairman': org.chairman.full_name if hasattr(org, 'chairman') and org.chairman else 'Председатель',
        }
        
        logger.info(f"Organization details retrieved: {details['short_name']}, INN: {details['inn']}")
        return details
        
    except Exception as e:
        logger.error(f"Error getting organization details: {e}\n{traceback.format_exc()}")
        return None


class QRCodeGenerator:
    """
    Генератор QR-кодов для банковских квитанций.
    Реквизиты берутся из модели Organization.
    """
    
    def __init__(self, request=None):
        """Инициализация генератора QR-кодов."""
        logger.info("Initializing QRCodeGenerator")
        
        try:
            self.request = request
            self.snt_details = get_current_organization_details(request)
            
            if self.snt_details:
                self.snt_name = self.snt_details.get('short_name', 'СНТ')
                self.snt_inn = self.snt_details.get('inn', '')
                self.snt_kpp = self.snt_details.get('kpp', '')
                self.snt_account = re.sub(r'\D', '', self.snt_details.get('account', ''))
                self.snt_bank_name = self.snt_details.get('bank_name', '')
                self.snt_bank_bik = self.snt_details.get('bank_bik', '')
                self.snt_bank_corr = re.sub(r'\D', '', self.snt_details.get('bank_corr', ''))
                
                logger.info(f"QRCodeGenerator initialized with organization: {self.snt_name}")
                logger.debug(f"Bank details: {self.snt_bank_name}, BIK: {self.snt_bank_bik}")
            else:
                # Fallback на настройки, если нет организации в БД
                logger.warning("No organization found, using settings fallback")
                self._init_from_settings()
                
        except Exception as e:
            logger.error(f"Error initializing QRCodeGenerator: {e}\n{traceback.format_exc()}")
            # Инициализируем с настройками по умолчанию
            self._init_from_settings()
    
    def _init_from_settings(self):
        """Инициализация из настроек Django."""
        try:
            self.snt_name = getattr(settings, 'SNT_NAME', 'СНТ')
            self.snt_inn = getattr(settings, 'SNT_INN', '')
            self.snt_kpp = getattr(settings, 'SNT_KPP', '')
            self.snt_account = re.sub(r'\D', '', getattr(settings, 'SNT_ACCOUNT', ''))
            self.snt_bank_name = getattr(settings, 'SNT_BANK_NAME', '')
            self.snt_bank_bik = getattr(settings, 'SNT_BANK_BIK', '')
            self.snt_bank_corr = re.sub(r'\D', '', getattr(settings, 'SNT_BANK_CORR', ''))
            
            logger.info(f"QRCodeGenerator initialized from settings: {self.snt_name}")
            logger.debug(f"Account: {self.snt_account[:4]}...{self.snt_account[-4:] if len(self.snt_account) > 8 else ''}")
        except Exception as e:
            logger.error(f"Error initializing from settings: {e}")
            # Устанавливаем значения по умолчанию
            self.snt_name = 'СНТ'
            self.snt_inn = ''
            self.snt_kpp = ''
            self.snt_account = ''
            self.snt_bank_name = ''
            self.snt_bank_bik = ''
            self.snt_bank_corr = ''
    
    def generate_qr_data(self, 
                         owner_name: str,
                         plot_number: str,
                         amount: Decimal,
                         assessment_id: int,
                         period: str,
                         category_name: str) -> str:
        """
        Генерирует строку данных для QR-кода по ГОСТ Р 56042-2014.
        
        Args:
            owner_name: ФИО владельца
            plot_number: Номер участка
            amount: Сумма к оплате
            assessment_id: ID начисления
            period: Период оплаты
            category_name: Название категории взноса
            
        Returns:
            Строка с данными для QR-кода
        """
        logger.info(f"Generating QR data for assessment {assessment_id}")
        logger.debug(f"Params: owner={owner_name}, plot={plot_number}, amount={amount}, period={period}, category={category_name}")
        
        try:
            payment_id = f"SNT-{assessment_id:06d}"
            logger.debug(f"Payment ID: {payment_id}")
            
            # Формируем назначение платежа
            purpose = (
                f"Оплата {category_name} за {period}. "
                f"Уч.№{plot_number}, Владелец: {owner_name}, "
                f"ID:{payment_id}. Без НДС."
            )
            
            # Обрезаем если слишком длинное
            original_length = len(purpose)
            if len(purpose) > 210:
                purpose = purpose[:207] + "..."
                logger.warning(f"Purpose truncated from {original_length} to 210 characters")
            
            logger.debug(f"Purpose: {purpose[:100]}...")
            
            # Проверяем обязательные реквизиты
            if not self.snt_account:
                logger.error("Missing bank account number for QR code")
                raise ValueError("Bank account number is required for QR code generation")
            
            if not self.snt_bank_bik:
                logger.error("Missing bank BIK for QR code")
                raise ValueError("Bank BIK is required for QR code generation")
            
            # Формируем поля QR-кода
            fields = []
            
            fields.append("ST00012")
            fields.append(f"Name={self.snt_name[:160]}")
            fields.append(f"PersonalAcc={self.snt_account}")
            fields.append(f"BankName={self.snt_bank_name[:45]}")
            fields.append(f"BIC={self.snt_bank_bik}")
            fields.append(f"CorrespAcc={self.snt_bank_corr}")
            
            if self.snt_inn:
                fields.append(f"INN={self.snt_inn}")
            if self.snt_kpp:
                fields.append(f"KPP={self.snt_kpp}")
            
            fields.append(f"PayeeINN={self.snt_inn}")
            fields.append(f"Sum={int(amount * 100)}")
            fields.append(f"Purpose={purpose}")
            
            # Добавляем информацию о плательщике
            name_parts = owner_name.split()
            if len(name_parts) > 0:
                fields.append(f"LastName={name_parts[0]}")
            if len(name_parts) > 1:
                fields.append(f"FirstName={name_parts[1]}")
            if len(name_parts) > 2:
                fields.append(f"MiddleName={name_parts[2]}")
            
            fields.append(f"Contract={payment_id}")
            
            qr_string = "|".join(fields)
            
            logger.info(f"QR data generated successfully, length: {len(qr_string)} characters")
            logger.debug(f"QR data preview: {qr_string[:200]}...")
            
            return qr_string
            
        except Exception as e:
            logger.error(f"Error generating QR data: {e}\n{traceback.format_exc()}")
            raise
    
    def generate_qr_image(self, qr_data: str, size: int = 300) -> Optional[bytes]:
        """
        Генерирует PNG-изображение QR-кода.
        
        Args:
            qr_data: Строка с данными для QR-кода
            size: Размер изображения в пикселях
            
        Returns:
            Байты PNG-изображения или None при ошибке
        """
        logger.info(f"Generating QR image, size={size}, data length={len(qr_data)}")
        
        if not qr_data:
            logger.error("QR data is empty")
            return None
        
        # Проверяем длину данных
        if len(qr_data) > 1000:
            logger.warning(f"QR data is quite long: {len(qr_data)} characters")
        
        try:
            import qrcode
            
            logger.debug("Using qrcode library")
            
            # Настройки QR-кода
            version = 3
            box_size = 8
            border = 2
            
            # Автоматически увеличиваем версию для длинных данных
            if len(qr_data) > 500:
                version = 5
                box_size = 6
                logger.debug(f"Increased version to {version} for long data")
            
            qr = qrcode.QRCode(
                version=version,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=box_size,
                border=border,
            )
            qr.add_data(qr_data)
            qr.make(fit=True)
            
            logger.debug(f"QR version used: {qr.version}, box size: {box_size}")
            
            # Создаём изображение
            img = qr.make_image(fill_color="black", back_color="white")
            
            # Сохраняем в буфер
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            buffer.seek(0)
            
            image_bytes = buffer.getvalue()
            logger.info(f"QR image generated successfully, size: {len(image_bytes)} bytes")
            
            return image_bytes
            
        except ImportError as e:
            logger.error(f"qrcode library not installed: {e}")
            logger.warning("Falling back to API-based QR generation")
            return self._generate_qr_via_api(qr_data, size)
            
        except Exception as e:
            logger.error(f"Error generating QR image with qrcode: {e}\n{traceback.format_exc()}")
            logger.warning("Falling back to API-based QR generation")
            return self._generate_qr_via_api(qr_data, size)
    
    def _generate_qr_via_api(self, qr_data: str, size: int = 300) -> Optional[bytes]:
        """
        Резервный метод: генерация QR через внешний API.
        
        Args:
            qr_data: Строка с данными для QR-кода
            size: Размер изображения в пикселях
            
        Returns:
            Байты PNG-изображения или None при ошибке
        """
        logger.info(f"Generating QR via API, size={size}")
        
        try:
            import urllib.request
            import urllib.parse
            
            # Кодируем данные для URL
            encoded_data = urllib.parse.quote(qr_data)
            
            # Используем несколько API для надёжности
            apis = [
                f"https://quickchart.io/qr?text={encoded_data}&size={size}&margin=2",
                f"https://api.qrserver.com/v1/create-qr-code/?size={size}x{size}&data={encoded_data}",
            ]
            
            for i, api_url in enumerate(apis):
                try:
                    logger.debug(f"Trying API {i+1}: {api_url[:50]}...")
                    
                    req = urllib.request.Request(
                        api_url, 
                        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                    )
                    
                    with urllib.request.urlopen(req, timeout=10) as response:
                        result = response.read()
                        
                        # Проверяем, что получили изображение (начинается с PNG signature)
                        if result and result[:8] == b'\x89PNG\r\n\x1a\n':
                            logger.info(f"QR image generated via API {i+1}, size: {len(result)} bytes")
                            return result
                        else:
                            logger.warning(f"API {i+1} returned non-PNG data")
                            
                except urllib.error.URLError as e:
                    logger.warning(f"API {i+1} URL error: {e}")
                except Exception as e:
                    logger.warning(f"API {i+1} error: {e}")
            
            logger.error("All QR API attempts failed")
            return None
            
        except Exception as e:
            logger.error(f"Error in API QR generation: {e}\n{traceback.format_exc()}")
            return None
    
    def get_qr_data_uri(self, qr_data: str, size: int = 300) -> str:
        """
        Возвращает Data URI для вставки QR-кода в HTML.
        
        Args:
            qr_data: Строка с данными для QR-кода
            size: Размер изображения в пикселях
            
        Returns:
            Data URI строка или пустая строка при ошибке
        """
        logger.debug(f"Generating QR data URI, size={size}")
        
        try:
            if not qr_data:
                logger.error("QR data is empty")
                return ""
            
            image_bytes = self.generate_qr_image(qr_data, size)
            
            if image_bytes:
                b64 = base64.b64encode(image_bytes).decode('utf-8')
                data_uri = f"data:image/png;base64,{b64}"
                logger.info(f"QR data URI generated successfully, length: {len(data_uri)} characters")
                return data_uri
            else:
                logger.error("Failed to generate QR image bytes")
                return ""
                
        except Exception as e:
            logger.error(f"Error generating QR data URI: {e}\n{traceback.format_exc()}")
            return ""


class SNTDetailsGenerator:
    """Генератор полных реквизитов СНТ для квитанции"""
    
    def __init__(self, request=None):
        """Инициализация генератора реквизитов."""
        logger.info("Initializing SNTDetailsGenerator")
        self.request = request
        self.snt_details = get_current_organization_details(request)
        
        if self.snt_details:
            logger.info(f"SNTDetailsGenerator initialized with organization: {self.snt_details.get('name', 'Unknown')}")
        else:
            logger.warning("SNTDetailsGenerator initialized with fallback settings")
    
    def get_details(self) -> Dict[str, str]:
        """
        Возвращает полные реквизиты СНТ для квитанции.
        
        Returns:
            Словарь с реквизитами организации
        """
        logger.debug("Getting SNT details for receipt")
        
        try:
            if self.snt_details:
                details = {
                    'name': self.snt_details.get('name', 'СНТ'),
                    'inn': self.snt_details.get('inn', ''),
                    'kpp': self.snt_details.get('kpp', ''),
                    'account': format_account(self.snt_details.get('account', '')),
                    'bank_name': self.snt_details.get('bank_name', ''),
                    'bank_bik': self.snt_details.get('bank_bik', ''),
                    'bank_corr': format_account(self.snt_details.get('bank_corr', '')),
                    'chairman': self.snt_details.get('chairman', 'Председатель'),
                }
                logger.info(f"SNT details from organization: {details['name']}")
                logger.debug(f"Account: {details['account']}, BIK: {details['bank_bik']}")
                return details
            
            # Fallback на настройки
            logger.warning("Using fallback settings for SNT details")
            details = {
                'name': getattr(settings, 'SNT_NAME', 'СНТ'),
                'inn': getattr(settings, 'SNT_INN', ''),
                'kpp': getattr(settings, 'SNT_KPP', ''),
                'account': format_account(getattr(settings, 'SNT_ACCOUNT', '')),
                'bank_name': getattr(settings, 'SNT_BANK_NAME', ''),
                'bank_bik': getattr(settings, 'SNT_BANK_BIK', ''),
                'bank_corr': format_account(getattr(settings, 'SNT_BANK_CORR', '')),
                'chairman': getattr(settings, 'SNT_CHAIRMAN', 'Председатель'),
            }
            logger.info(f"SNT details from settings: {details['name']}")
            return details
            
        except Exception as e:
            logger.error(f"Error getting SNT details: {e}\n{traceback.format_exc()}")
            # Возвращаем значения по умолчанию
            return {
                'name': 'СНТ',
                'inn': '',
                'kpp': '',
                'account': '',
                'bank_name': '',
                'bank_bik': '',
                'bank_corr': '',
                'chairman': 'Председатель',
            }


def format_account(account: str) -> str:
    """
    Форматирует номер счёта для квитанции (группами по 4 цифры).
    
    Args:
        account: Номер счёта (может содержать пробелы и другие символы)
        
    Returns:
        Отформатированный номер счёта
    """
    if not account:
        logger.debug("Empty account number provided")
        return ''
    
    try:
        # Удаляем все нецифровые символы
        digits = re.sub(r'\D', '', account)
        
        if not digits:
            logger.warning("No digits found in account number")
            return ''
        
        # Разбиваем на группы по 4 цифры
        groups = [digits[i:i+4] for i in range(0, len(digits), 4)]
        formatted = ' '.join(groups)
        
        logger.debug(f"Account formatted: {account[:10]}... -> {formatted[:20]}...")
        return formatted
        
    except Exception as e:
        logger.error(f"Error formatting account: {e}")
        return account