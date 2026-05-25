import logging
import re
from datetime import datetime
from django.db import transaction, models
from openpyxl import load_workbook
from users.models import Owner, ContactInfo
from land.models import LandPlot
from organizations.models import Organization, OrganizationMembership

logger = logging.getLogger(__name__)


class ExcelImporter:
    """Импорт данных из Excel в CRM"""
    
    def __init__(self, file_path, organization_id=None):
        self.file_path = file_path
        self.organization_id = organization_id
        self.organization = None
        self.errors = []
        self.warnings = []
        self.stats = {
            'total_rows': 0,
            'owners_created': 0,
            'owners_found': 0,
            'plots_created': 0,
            'plots_found': 0,
            'contacts_added': 0,
            'cadastral_updated': 0,
            'errors': 0
        }
    
    def normalize_cadastral(self, value):
        """
        Нормализация кадастрового номера.
        Принимает различные форматы и приводит к единому виду.
        """
        if not value:
            return None
        
        cadastral = str(value).strip()
        
        # Если значение слишком короткое или не содержит цифр
        if len(cadastral) < 5 or not any(c.isdigit() for c in cadastral):
            return None
        
        # Заменяем точки на двоеточия
        cadastral = cadastral.replace('.', ':')
        
        # Убираем лишние пробелы
        cadastral = re.sub(r'\s+', '', cadastral)
        
        # Убираем лишние двоеточия
        cadastral = re.sub(r':+', ':', cadastral)
        
        # Убираем "дом", "уч" и другие слова
        cadastral = re.sub(r'дом.*$', '', cadastral, flags=re.IGNORECASE)
        cadastral = re.sub(r'уч.*$', '', cadastral, flags=re.IGNORECASE)
        
        # Если есть несколько номеров через запятую - берем первый
        if ',' in cadastral:
            cadastral = cadastral.split(',')[0].strip()
        
        # Если номер заканчивается на двоеточие - удаляем его
        if cadastral.endswith(':'):
            cadastral = cadastral[:-1]
        
        # Разбиваем на части
        parts = cadastral.split(':')
        
        # Обработка одночастных номеров типа "24500700439:229"
        if len(parts) == 1 and ':' not in cadastral and len(cadastral) > 10:
            # Пробуем разделить по позиции (первые 11 цифр - первая часть)
            if len(cadastral) > 11:
                cadastral = f"{cadastral[:11]}:{cadastral[11:]}"
                parts = cadastral.split(':')
        
        # Обработка двухчастных номеров
        if len(parts) == 2:
            # Добавляем недостающие части
            if len(parts[0]) >= 11:
                # Формат типа 24500700439:229
                # Разбиваем первую часть на регион и район/квартал
                region = parts[0][:2]
                rest = parts[0][2:]
                if len(rest) >= 6:
                    district = rest[:2]
                    quarter = rest[2:8] if len(rest) >= 8 else rest[2:].ljust(6, '0')
                else:
                    district = '00'
                    quarter = rest.ljust(6, '0')
                cadastral = f"{region}:{district}:{quarter}:{parts[1].zfill(3)}"
            else:
                # Общий случай
                cadastral = f"{parts[0].zfill(2)}:00:000000:{parts[1].zfill(3)}"
        
        # Обработка трехчастных номеров
        elif len(parts) == 3:
            cadastral = f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:{parts[2].zfill(6)}:0001"
        
        # Обработка четырехчастных номеров
        elif len(parts) == 4:
            # Очищаем каждую часть от нецифровых символов
            cleaned_parts = []
            for part in parts:
                clean = re.sub(r'\D', '', part)
                if clean:
                    cleaned_parts.append(clean)
                else:
                    cleaned_parts.append('0')
            
            if len(cleaned_parts) == 4:
                cadastral = f"{cleaned_parts[0].zfill(2)}:{cleaned_parts[1].zfill(2)}:{cleaned_parts[2].zfill(6)}:{cleaned_parts[3].zfill(3)}"
        
        # Проверяем формат
        final_parts = cadastral.split(':')
        if len(final_parts) == 4:
            # Проверяем, что все части содержат только цифры
            valid = True
            for i, part in enumerate(final_parts):
                if not part.isdigit():
                    valid = False
                    break
                # Проверяем длину частей
                if i == 0 and len(part) != 2:
                    valid = False
                elif i == 1 and len(part) != 2:
                    valid = False
                elif i == 2 and (len(part) < 6 or len(part) > 7):
                    # Квартал может быть 6 или 7 цифр
                    pass
                elif i == 3 and len(part) == 0:
                    valid = False
            
            if valid:
                # Форматируем квартал до 6 цифр
                if len(final_parts[2]) == 7:
                    final_parts[2] = final_parts[2][:6]
                cadastral = f"{final_parts[0]}:{final_parts[1]}:{final_parts[2]}:{final_parts[3]}"
                return cadastral
        
        # Если не удалось нормализовать, возвращаем None
        return None
    
    def parse_phone(self, value):
        """Парсинг и форматирование телефона"""
        if not value:
            return None
        
        # Извлекаем только цифры
        digits = re.sub(r'\D', '', str(value))
        
        if not digits:
            return None
        
        # Если есть 10-11 цифр, это номер телефона
        if len(digits) == 11 and digits.startswith('8'):
            digits = '7' + digits[1:]
        elif len(digits) == 10:
            digits = '7' + digits
        elif len(digits) == 11 and digits.startswith('7'):
            pass
        else:
            # Если меньше 10 цифр - вероятно не телефон
            if len(digits) < 10:
                return None
        
        # Форматируем: +7 (XXX) XXX-XX-XX
        if len(digits) == 11 and digits.startswith('7'):
            return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
        
        return None
    
    def extract_phones(self, text):
        """Извлечь все номера телефонов из текста"""
        if not text:
            return []
        
        phones = []
        text = str(text)
        
        # Ищем номера телефонов в разных форматах
        patterns = [
            r'(\+?7[-\s]?\(?\d{3}\)?[-\s]?\d{3}[-\s]?\d{2}[-\s]?\d{2})',
            r'(8[-\s]?\(?\d{3}\)?[-\s]?\d{3}[-\s]?\d{2}[-\s]?\d{2})',
            r'(\d{3}[-\s]?\d{3}[-\s]?\d{2}[-\s]?\d{2})',
            r'(\d{10,11})',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                phone = self.parse_phone(match)
                if phone and phone not in phones:
                    phones.append(phone)
        
        return phones
    
    def extract_email(self, text):
        """Извлечь email из текста"""
        if not text:
            return None
        
        pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        match = re.search(pattern, str(text))
        if match:
            return match.group().lower()
        return None
    
    def parse_area(self, value):
        """Парсинг площади"""
        if not value:
            return None
        
        try:
            area_str = str(value).replace(',', '.').strip()
            # Убираем все кроме цифр и точки
            area_str = re.sub(r'[^\d.]', '', area_str)
            if area_str:
                return round(float(area_str), 2)
        except:
            pass
        return None
    
    def parse_date(self, value):
        """Парсинг даты"""
        if not value:
            return None
        
        if isinstance(value, datetime):
            return value.date()
        
        date_str = str(value).strip()
        
        # Убираем "г" и другие символы
        date_str = re.sub(r'[^\d\.\-/]', '', date_str)
        
        formats = ['%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y', '%d.%m.%y', '%d.%m.%Y']
        
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt).date()
            except:
                continue
        
        return None
    
    def normalize_name(self, name):
        """Нормализация ФИО"""
        if not name:
            return None
        
        name = str(name).strip()
        
        # Убираем лишние символы и вопросы
        name = re.sub(r'[?!@#$%^&*()]', '', name)
        name = re.sub(r'\?+', '', name)
        
        # Убираем слова-маркеры
        markers = ['СПК', 'СТ', 'С.Т', 'СНТ', 'ПК', 'Строитель', 'Строителб']
        for marker in markers:
            name = re.sub(rf'\s+{marker}.*$', '', name, flags=re.IGNORECASE)
        
        # Убираем лишние пробелы
        name = ' '.join(name.split())
        
        # Приводим к правильному регистру
        name = name.title()
        
        return name if len(name) > 3 else None
    
    def _get_cell_value(self, row, index, default=None):
        """Безопасное получение значения ячейки"""
        if index < len(row) and row[index] is not None:
            val = row[index]
            if isinstance(val, str):
                return val.strip()
            return val
        return default
    
    @transaction.atomic
    def import_data(self):
        """Основной метод импорта данных"""
        try:
            wb = load_workbook(self.file_path, data_only=True)
            ws = wb.active
            
            # Получаем организацию
            if self.organization_id:
                try:
                    self.organization = Organization.objects.get(id=self.organization_id)
                except Organization.DoesNotExist:
                    self.errors.append(f"Организация с ID {self.organization_id} не найдена")
                    return self.stats
            
            print(f"Импорт в организацию: {self.organization.short_name if self.organization else 'Без организации'}")
            print("=" * 80)
            
            # Пропускаем заголовки (первая строка)
            start_row = 2
            
            # Словарь для отслеживания уже созданных участков
            created_plots = {}
            
            for row_idx, row in enumerate(ws.iter_rows(min_row=start_row, values_only=True), start=start_row):
                if not row or not any(row):
                    continue
                
                # Извлекаем данные по столбцам:
                # A(0) - №№, B(1) - №участка, C(2) - ФИО, D(3) - метраж, 
                # E(4) - дата, F(5) - кадастр, G(6) - email, H(7) - примечания
                plot_number = self._get_cell_value(row, 1)  # B
                owner_name_raw = self._get_cell_value(row, 2)  # C
                area_raw = self._get_cell_value(row, 3)  # D
                date_raw = self._get_cell_value(row, 4)  # E
                cadastral_raw = self._get_cell_value(row, 5)  # F
                email_raw = self._get_cell_value(row, 6)  # G
                notes_raw = self._get_cell_value(row, 7)  # H
                
                # Пропускаем строки без номера участка
                if not plot_number:
                    continue
                
                # Нормализуем ФИО
                owner_name = self.normalize_name(owner_name_raw)
                
                # Пропускаем служебные строки
                if owner_name and ('правление' in owner_name.lower() or '????' in owner_name):
                    continue
                
                self.stats['total_rows'] += 1
                print(f"\n[{row_idx}] Участок {plot_number} - {owner_name or 'Нет ФИО'}")
                
                try:
                    # Обработка площади
                    area = self.parse_area(area_raw)
                    if area:
                        print(f"  → Площадь: {area} м²")
                    
                    # Обработка даты
                    issue_date = self.parse_date(date_raw)
                    if issue_date:
                        print(f"  → Дата: {issue_date}")
                    
                    # Обработка кадастрового номера
                    cadastral = self.normalize_cadastral(cadastral_raw)
                    if cadastral:
                        print(f"  → Кадастровый номер: {cadastral}")
                    
                    # Создаем или получаем владельца (если есть ФИО)
                    owner = None
                    if owner_name:
                        owner = self._get_or_create_owner(owner_name)
                        if owner:
                            # Обрабатываем контакты
                            self._process_contacts(owner, email_raw, notes_raw)
                    
                    # Создаем или получаем участок
                    plot = self._get_or_create_plot(plot_number, cadastral, area, notes_raw)
                    
                    if plot:
                        # Обновляем кадастровый номер если он есть
                        if cadastral and not plot.cadastral_number:
                            plot.cadastral_number = cadastral
                            plot.save(update_fields=['cadastral_number'])
                            self.stats['cadastral_updated'] += 1
                            print(f"  → Кадастровый номер добавлен")
                        
                        # Обновляем площадь если она есть и отличается
                        if area and plot.area_sqm != area:
                            plot.area_sqm = area
                            plot.save(update_fields=['area_sqm'])
                            print(f"  → Площадь обновлена")
                        
                        # Связываем участок с владельцем
                        if owner and not plot.owners.filter(id=owner.id).exists():
                            from users.models import Ownership
                            Ownership.objects.create(
                                owner=owner,
                                land_plot=plot,
                                share='1/1',
                                ownership_since=issue_date,
                                document_basis=f"Импорт из Excel {datetime.now().strftime('%d.%m.%Y')}"
                            )
                            print(f"  → Связан с владельцем")
                    
                except Exception as e:
                    self._add_error(row_idx, f"Ошибка: {str(e)}")
                    logger.exception(f"Ошибка в строке {row_idx}")
            
            print("\n" + "=" * 80)
            print("РЕЗУЛЬТАТЫ ИМПОРТА:")
            print(f"  Всего строк: {self.stats['total_rows']}")
            print(f"  Создано владельцев: {self.stats['owners_created']}")
            print(f"  Найдено владельцев: {self.stats['owners_found']}")
            print(f"  Создано участков: {self.stats['plots_created']}")
            print(f"  Найдено участков: {self.stats['plots_found']}")
            print(f"  Добавлено контактов: {self.stats['contacts_added']}")
            print(f"  Обновлено кадастровых номеров: {self.stats['cadastral_updated']}")
            print(f"  Ошибок: {self.stats['errors']}")
            
            return self.stats
            
        except Exception as e:
            self.errors.append(f"Ошибка открытия файла: {str(e)}")
            return self.stats
    
    def _process_contacts(self, owner, email_raw, notes_raw):
        """Обработка контактов"""
        # Email из отдельного столбца
        if email_raw:
            # Проверяем, похоже ли на email
            if '@' in str(email_raw):
                email = self.extract_email(email_raw)
                if email:
                    self._add_contact(owner, 'em', email)
                    print(f"  → Email: {email}")
            else:
                # Возможно email в примечаниях
                email_from_notes = self.extract_email(email_raw)
                if email_from_notes:
                    self._add_contact(owner, 'em', email_from_notes)
                    print(f"  → Email: {email_from_notes}")
        
        # Телефоны из примечаний
        if notes_raw:
            phones = self.extract_phones(notes_raw)
            for phone in phones[:3]:
                if phone:
                    self._add_contact(owner, 'ph', phone)
                    print(f"  → Телефон: {phone}")
            
            # Если нет email, пробуем найти в примечаниях
            if not email_raw:
                email_from_notes = self.extract_email(notes_raw)
                if email_from_notes:
                    self._add_contact(owner, 'em', email_from_notes)
                    print(f"  → Email: {email_from_notes}")
    
    def _get_or_create_owner(self, full_name):
        """Получить или создать владельца"""
        # Очищаем имя для поиска
        search_name = full_name.replace('ё', 'е')
        
        # Ищем существующего владельца
        owner = Owner.objects.filter(
            models.Q(full_name__iexact=full_name) |
            models.Q(full_name__iregex=r'^' + re.escape(full_name[:20]))
        ).first()
        
        if owner:
            self.stats['owners_found'] += 1
            return owner
        
        # Создаем нового владельца
        owner = Owner.objects.create(full_name=full_name)
        self.stats['owners_created'] += 1
        print(f"  ✓ Создан владелец: {full_name}")
        
        # Если есть организация, создаем членство
        if self.organization:
            OrganizationMembership.objects.get_or_create(
                owner=owner,
                organization=self.organization,
                defaults={'status': 'active'}
            )
        
        return owner
    
    def _get_or_create_plot(self, plot_number, cadastral_number, area_sqm, notes):
        """Получить или создать участок"""
        # Очищаем номер участка
        plot_number = str(plot_number).strip().upper()
        
        # Ищем по номеру участка
        plot = LandPlot.objects.filter(plot_number=plot_number).first()
        
        if plot:
            self.stats['plots_found'] += 1
            return plot
        
        # Создаем новый участок
        try:
            plot = LandPlot.objects.create(
                plot_number=plot_number,
                cadastral_number=cadastral_number or '',
                area_sqm=area_sqm or 600,
                notes=f"Импортировано из Excel. {notes[:200] if notes else ''}",
                status='active',
                organization=self.organization
            )
            self.stats['plots_created'] += 1
            print(f"  ✓ Создан участок: {plot_number} (площадь: {area_sqm or 600} м²)")
            return plot
        except Exception as e:
            print(f"  ✗ Ошибка создания участка {plot_number}: {e}")
            return None
    
    def _add_contact(self, owner, contact_type, value):
        """Добавить контакт владельцу"""
        if not value:
            return
        
        # Проверяем, существует ли уже такой активный контакт
        existing = ContactInfo.objects.filter(
            owner=owner,
            type=contact_type,
            value=value,
            is_active=True
        ).first()
        
        if existing:
            return
        
        # Создаем новый контакт
        try:
            ContactInfo.objects.create(
                owner=owner,
                type=contact_type,
                value=value,
                is_active=True,
                is_verified=False,
                note="Импортировано из Excel"
            )
            self.stats['contacts_added'] += 1
        except Exception as e:
            pass
    
    def _add_error(self, row, message):
        """Добавить ошибку"""
        self.errors.append(f"Строка {row}: {message}")
        self.stats['errors'] += 1