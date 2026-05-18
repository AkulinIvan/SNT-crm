# SNT/payments/qr_generator.py - исправленная версия

"""
Генератор QR-кодов для квитанций по стандарту ГОСТ Р 56042-2014.
Формат: ST00012|Name=...|PersonalAcc=...|BankName=...|BIC=...|CorrespAcc=...|...
"""

import re
import base64
from decimal import Decimal
from typing import Optional
from io import BytesIO
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


class QRCodeGenerator:
    """
    Генератор QR-кодов для банковских квитанций.
    
    Стандарт ГОСТ Р 56042-2014 определяет формат данных для QR-кода,
    который понимают все российские банковские приложения.
    """
    
    def __init__(self):
        # Реквизиты СНТ (должны быть в settings)
        self.snt_name = getattr(settings, 'SNT_NAME', 'СНТ "Строитель-43"')
        self.snt_inn = getattr(settings, 'SNT_INN', '')
        self.snt_kpp = getattr(settings, 'SNT_KPP', '')
        self.snt_account = re.sub(r'\D', '', getattr(settings, 'SNT_ACCOUNT', ''))  # Только цифры
        self.snt_bank_name = getattr(settings, 'SNT_BANK_NAME', '')
        self.snt_bank_bik = getattr(settings, 'SNT_BANK_BIK', '')
        self.snt_bank_corr = re.sub(r'\D', '', getattr(settings, 'SNT_BANK_CORR', ''))  # Только цифры
        
        # Логируем реквизиты для отладки
        logger.info(f"QRCodeGenerator initialized with:")
        logger.info(f"  SNT_NAME: {self.snt_name}")
        logger.info(f"  SNT_ACCOUNT: {self.snt_account}")
        logger.info(f"  SNT_BANK_BIK: {self.snt_bank_bik}")
        logger.info(f"  SNT_BANK_CORR: {self.snt_bank_corr}")
    
    def generate_qr_data(self, 
                         owner_name: str,
                         plot_number: str,
                         amount: Decimal,
                         assessment_id: int,
                         period: str,
                         category_name: str) -> str:
        """
        Генерирует строку данных для QR-кода по ГОСТ Р 56042-2014.
        """
        # Уникальный идентификатор платежа
        payment_id = f"SNT-{assessment_id:06d}"
        
        # Назначение платежа
        purpose = (
            f"Оплата {category_name} за {period}. "
            f"Уч.№{plot_number}, Владелец: {owner_name}, "
            f"ID:{payment_id}. Без НДС."
        )
        # Обрезаем до 210 символов
        if len(purpose) > 210:
            purpose = purpose[:207] + "..."
        
        # Формируем строку по ГОСТ Р 56042-2014
        fields = []
        
        # Обязательные поля
        fields.append("ST00012")  # Версия стандарта
        fields.append(f"Name={self.snt_name[:160]}")
        fields.append(f"PersonalAcc={self.snt_account}")
        fields.append(f"BankName={self.snt_bank_name[:45]}")
        fields.append(f"BIC={self.snt_bank_bik}")
        fields.append(f"CorrespAcc={self.snt_bank_corr}")
        
        # Необязательные поля
        if self.snt_inn:
            fields.append(f"INN={self.snt_inn}")
        if self.snt_kpp:
            fields.append(f"KPP={self.snt_kpp}")
        
        # Платёжные реквизиты
        fields.append(f"PayeeINN={self.snt_inn}")
        fields.append(f"Sum={int(amount * 100)}")  # Сумма в копейках
        fields.append(f"Purpose={purpose}")
        
        # Данные плательщика
        name_parts = owner_name.split()
        if len(name_parts) > 0:
            fields.append(f"LastName={name_parts[0]}")
        if len(name_parts) > 1:
            fields.append(f"FirstName={name_parts[1]}")
        if len(name_parts) > 2:
            fields.append(f"MiddleName={name_parts[2]}")
        
        fields.append(f"Contract={payment_id}")
        
        # Собираем строку
        qr_string = "|".join(fields)
        
        logger.info(f"Generated QR data length: {len(qr_string)}")
        return qr_string
    
    def generate_qr_image(self, qr_data: str, size: int = 300) -> Optional[bytes]:
        """
        Генерирует PNG-изображение QR-кода.
        """
        try:
            import qrcode
            
            qr = qrcode.QRCode(
                version=3,  # Увеличил версию для большего объёма данных
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=8,
                border=2,
            )
            qr.add_data(qr_data)
            qr.make(fit=True)
            
            # Создаём изображение
            img = qr.make_image(fill_color="black", back_color="white")
            
            # Сохраняем в буфер
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            buffer.seek(0)
            
            logger.info(f"QR image generated successfully, size: {buffer.getbuffer().nbytes} bytes")
            return buffer.getvalue()
            
        except ImportError as e:
            logger.error(f"qrcode library not installed: {e}")
            return self._generate_qr_via_api(qr_data, size)
        except Exception as e:
            logger.error(f"Error generating QR image: {e}")
            return self._generate_qr_via_api(qr_data, size)
    
    def _generate_qr_via_api(self, qr_data: str, size: int = 300) -> Optional[bytes]:
        """
        Резервный метод: генерация QR через внешний API.
        """
        import urllib.request
        import urllib.parse
        
        # Кодируем данные для URL
        encoded_data = urllib.parse.quote(qr_data)
        url = f"https://quickchart.io/qr?text={encoded_data}&size={size}&margin=2"
        
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                result = response.read()
                logger.info(f"QR image generated via API, size: {len(result)} bytes")
                return result
        except Exception as e:
            logger.error(f"Error generating QR via API: {e}")
            return None
    
    def get_qr_data_uri(self, qr_data: str, size: int = 300) -> str:
        """
        Возвращает Data URI для вставки QR-кода в HTML.
        """
        image_bytes = self.generate_qr_image(qr_data, size)
        if image_bytes:
            b64 = base64.b64encode(image_bytes).decode('utf-8')
            return f"data:image/png;base64,{b64}"
        
        # Если не удалось сгенерировать QR, возвращаем заглушку
        logger.warning("Failed to generate QR image, returning placeholder")
        return ""


class SNTDetailsGenerator:
    """Генератор полных реквизитов СНТ для квитанции"""
    
    def __init__(self):
        self.name = getattr(settings, 'SNT_NAME', 'СНТ "Строитель-43"')
        self.inn = getattr(settings, 'SNT_INN', '')
        self.kpp = getattr(settings, 'SNT_KPP', '')
        self.account = getattr(settings, 'SNT_ACCOUNT', '')
        self.bank_name = getattr(settings, 'SNT_BANK_NAME', '')
        self.bank_bik = getattr(settings, 'SNT_BANK_BIK', '')
        self.bank_corr = getattr(settings, 'SNT_BANK_CORR', '')
        self.oktmo = getattr(settings, 'SNT_OKTMO', '')
        self.kbk = getattr(settings, 'SNT_KBK', '')
        self.chairman = getattr(settings, 'SNT_CHAIRMAN', 'Председатель')
    
    def get_details(self) -> dict:
        """Возвращает полные реквизиты СНТ для квитанции"""
        return {
            'name': self.name,
            'inn': self.inn,
            'kpp': self.kpp,
            'account': format_account(self.account),
            'bank_name': self.bank_name,
            'bank_bik': self.bank_bik,
            'bank_corr': format_account(self.bank_corr),
            'oktmo': self.oktmo,
            'kbk': self.kbk,
            'chairman': self.chairman,
        }


def format_account(account: str) -> str:
    """Форматирует номер счёта для квитанции (группами по 4 цифры)"""
    if not account:
        return ''
    digits = re.sub(r'\D', '', account)
    groups = [digits[i:i+4] for i in range(0, len(digits), 4)]
    return ' '.join(groups)