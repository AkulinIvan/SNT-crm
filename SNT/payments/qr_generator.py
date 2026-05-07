"""
Генератор QR-кодов для квитанций по стандарту ГОСТ Р 56042-2014.
Формат: ST00012|Name=...|PersonalAcc=...|BankName=...|BIC=...|CorrespAcc=...|...
"""

import re
from decimal import Decimal
from typing import Optional
from django.conf import settings


class QRCodeGenerator:
    """
    Генератор QR-кодов для банковских квитанций.
    
    Стандарт ГОСТ Р 56042-2014 определяет формат данных для QR-кода,
    который понимают все российские банковские приложения.
    """
    
    def __init__(self):
        # Реквизиты СНТ (должны быть в settings)
        self.snt_name = getattr(settings, 'SNT_NAME', 'СНТ "Садовод"')
        self.snt_inn = getattr(settings, 'SNT_INN', '')
        self.snt_kpp = getattr(settings, 'SNT_KPP', '')
        self.snt_account = getattr(settings, 'SNT_ACCOUNT', '')  # Расчётный счёт
        self.snt_bank_name = getattr(settings, 'SNT_BANK_NAME', '')
        self.snt_bank_bik = getattr(settings, 'SNT_BANK_BIK', '')
        self.snt_bank_corr = getattr(settings, 'SNT_BANK_CORR', '')  # Корр. счёт
    
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
            period: Период (например, "2024 год")
            category_name: Категория взноса
        
        Returns:
            Строка для кодирования в QR-код
        """
        # Уникальный идентификатор платежа (используем ID начисления)
        payment_id = f"SNT-{assessment_id:06d}"
        
        # Назначение платежа — самая важная часть для автоматического распознавания
        purpose = (
            f"Оплата {category_name} за {period}. "
            f"Уч.№{plot_number}, Владелец: {owner_name}, "
            f"ID:{payment_id}. Без НДС."
        )
        # Обрезаем до 210 символов (ограничение банков)
        if len(purpose) > 210:
            purpose = purpose[:207] + "..."
        
        # Формируем строку по ГОСТ Р 56042-2014
        fields = [
            ("ST00012", ""),  # Версия стандарта
            ("Name", self.snt_name[:160]),  # Наименование получателя (макс 160)
            ("PersonalAcc", self.snt_account),  # Расчётный счёт
            ("BankName", self.snt_bank_name[:45]),  # Банк получателя (макс 45)
            ("BIC", self.snt_bank_bik),  # БИК банка
            ("CorrespAcc", self.snt_bank_corr),  # Корр. счёт
        ]
        
        # Необязательные поля
        if self.snt_inn:
            fields.append(("INN", self.snt_inn))
        if self.snt_kpp:
            fields.append(("KPP", self.snt_kpp))
        
        # Платёжные реквизиты
        fields.extend([
            ("PayeeINN", self.snt_inn),  # ИНН получателя
            ("Sum", str(int(amount * 100))),  # Сумма в копейках
            ("Purpose", purpose),  # Назначение платежа
            ("LastName", owner_name.split()[:1][0] if owner_name else ""),  # Фамилия
            ("FirstName", owner_name.split()[1:2][0] if len(owner_name.split()) > 1 else ""),
            ("Contract", f"SNT-{assessment_id:06d}"),  # Номер начисления
        ])
        
        # Собираем строку: разделитель между полями — "|", ключ=значение
        qr_string = "|".join(f"{key}={value}" for key, value in fields if value)
        
        return qr_string
    
    def generate_qr_image(self, qr_data: str, size: int = 300) -> Optional[bytes]:
        """
        Генерирует PNG-изображение QR-кода.
        
        Args:
            qr_data: Строка данных для QR-кода
            size: Размер изображения в пикселях
        
        Returns:
            PNG-изображение в виде bytes
        """
        try:
            import qrcode
            from io import BytesIO
            
            qr = qrcode.QRCode(
                version=2,  # Версия QR (1-40, определяет размер)
                error_correction=qrcode.constants.ERROR_CORRECT_M,  # Средний уровень коррекции
                box_size=10,
                border=4,
            )
            qr.add_data(qr_data)
            qr.make(fit=True)
            
            img = qr.make_image(fill_color="black", back_color="white")
            
            # Конвертируем в PNG bytes
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            return buffer.getvalue()
            
        except ImportError:
            # Если qrcode не установлен, генерируем через API
            return self._generate_qr_via_api(qr_data, size)
    
    def _generate_qr_via_api(self, qr_data: str, size: int = 300) -> Optional[bytes]:
        """
        Резервный метод: генерация QR через внешний API.
        Использует Google Charts API (бесплатно, без ограничений).
        """
        import urllib.request
        import urllib.parse
        
        url = "https://chart.googleapis.com/chart"
        params = {
            "cht": "qr",
            "chs": f"{size}x{size}",
            "chl": qr_data,
            "choe": "UTF-8",
            "chld": "M|0",
        }
        full_url = f"{url}?{urllib.parse.urlencode(params)}"
        
        try:
            with urllib.request.urlopen(full_url) as response:
                return response.read()
        except Exception:
            return None
    
    def get_qr_data_uri(self, qr_data: str, size: int = 300) -> str:
        """
        Возвращает Data URI для вставки QR-кода в HTML (без сохранения файла).
        """
        image_bytes = self.generate_qr_image(qr_data, size)
        if image_bytes:
            import base64
            b64 = base64.b64encode(image_bytes).decode('utf-8')
            return f"data:image/png;base64,{b64}"
        return ""


class SNTDetailsGenerator:
    """Генератор полных реквизитов СНТ для квитанции"""
    
    def __init__(self):
        self.name = getattr(settings, 'SNT_NAME', 'СНТ "Садовод"')
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
    # Удаляем всё, кроме цифр
    digits = re.sub(r'\D', '', account)
    # Разбиваем на группы по 4
    groups = [digits[i:i+4] for i in range(0, len(digits), 4)]
    return ' '.join(groups)