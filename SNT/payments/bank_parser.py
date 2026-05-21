import re
import csv
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Any
from decimal import Decimal

logger = logging.getLogger('payments.bank_parser')


class BankStatementParser:
    """
    Универсальный парсер банковских выписок.
    Поддерживает форматы: CSV, Excel, 1С, JSON, PDF.
    """
    
    # Паттерны для определения банка по формату файла
    BANK_PATTERNS = {
        'sberbank': {
            'date_col': ['Дата операции', 'Дата', 'date'],
            'amount_col': ['Сумма', 'Сумма операции', 'amount'],
            'payer_col': ['Плательщик', 'Контрагент', 'payer', 'Наименование'],
            'account_col': ['Счет плательщика', 'Счет', 'account'],
            'inn_col': ['ИНН плательщика', 'ИНН', 'inn'],
            'purpose_col': ['Назначение платежа', 'Назначение', 'purpose'],
        },
        'tinkoff': {
            'date_col': ['Дата операции', 'date'],
            'amount_col': ['Сумма', 'amount'],
            'payer_col': ['Отправитель', 'Контрагент', 'name'],
            'account_col': ['Счет отправителя', 'account'],
            'inn_col': ['ИНН', 'inn'],
            'purpose_col': ['Назначение', 'purpose'],
        },
        'alfa': {
            'date_col': ['Дата', 'date'],
            'amount_col': ['Сумма в валюте счета', 'amount'],
            'payer_col': ['Наименование получателя/плательщика', 'name'],
            'account_col': ['Счет', 'account'],
            'inn_col': ['ИНН', 'inn'],
            'purpose_col': ['Назначение платежа', 'purpose'],
        },
    }
    
    def __init__(self, bank_name: Optional[str] = None):
        self.bank_name = bank_name
        self.bank_pattern = self.BANK_PATTERNS.get(bank_name, {})
    
    def detect_bank(self, file_path: str) -> str:
        """Автоматически определить банк по структуре файла"""
        if file_path.endswith('.csv'):
            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                headers = [h.lower().strip() for h in next(reader)]
                
                for bank, patterns in self.BANK_PATTERNS.items():
                    matches = 0
                    for pattern_list in patterns.values():
                        if any(p.lower() in headers for p in pattern_list):
                            matches += 1
                    if matches >= 3:
                        return bank
        
        return 'unknown'
    
    def parse_file(self, file_path: str) -> List[Dict[str, Any]]:
        """Парсинг файла выписки"""
        if file_path.endswith('.csv'):
            return self._parse_csv(file_path)
        elif file_path.endswith('.xlsx') or file_path.endswith('.xls'):
            return self._parse_excel(file_path)
        elif file_path.endswith('.json'):
            return self._parse_json(file_path)
        elif file_path.endswith('.txt'):
            return self._parse_1c(file_path)
        elif file_path.endswith('.pdf'):
            return self._parse_pdf(file_path)
        else:
            raise ValueError(f'Неподдерживаемый формат файла: {file_path}')
    
    def _parse_pdf(self, file_path: str) -> List[Dict[str, Any]]:
        """Парсинг PDF выписки (сначала таблицы, потом текст)"""
        
        # Сначала пробуем извлечь таблицы
        transactions = self._parse_pdf_table(file_path)
        if transactions:
            logger.info(f'Извлечено {len(transactions)} транзакций из таблиц PDF')
            return transactions
        
        # Если таблиц нет — извлекаем текст
        text = self._extract_text_from_pdf(file_path)
        if not text:
            raise ValueError('Не удалось извлечь текст из PDF')
        
        logger.info(f'Извлечён текст из PDF ({len(text)} символов)')
        logger.debug(f'Первые 500 символов: {text[:500]}')
        
        # Определяем банк по содержимому
        text_lower = text.lower()
        if 'сбербанк' in text_lower or 'sberbank' in text_lower:
            return self._parse_sberbank_text(text)
        elif 'тинькофф' in text_lower or 'tinkoff' in text_lower:
            return self._parse_tinkoff_text(text)
        elif 'альфа' in text_lower or 'alfa' in text_lower:
            return self._parse_alfa_text(text)
        else:
            return self._parse_generic_text(text)
    
    def _extract_text_from_pdf(self, file_path: str) -> str:
        """Извлечение текста из PDF"""
        text = ""
        
        # Метод 1: pdfplumber (лучше для таблиц)
        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            if text.strip():
                logger.info('Текст извлечён через pdfplumber')
                return text
        except ImportError:
            logger.info('pdfplumber не установлен')
        except Exception as e:
            logger.warning(f'Ошибка pdfplumber: {e}')
        
        # Метод 2: PyPDF2
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(file_path)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
            if text.strip():
                logger.info('Текст извлечён через PyPDF2')
                return text
        except ImportError:
            logger.info('PyPDF2 не установлен')
        except Exception as e:
            logger.warning(f'Ошибка PyPDF2: {e}')
        
        # Метод 3: pdfminer
        try:
            from pdfminer.high_level import extract_text
            text = extract_text(file_path)
            if text.strip():
                logger.info('Текст извлечён через pdfminer')
                return text
        except ImportError:
            logger.info('pdfminer не установлен')
        except Exception as e:
            logger.warning(f'Ошибка pdfminer: {e}')
        
        return text
    
    def _parse_sberbank_text(self, text: str) -> List[Dict[str, Any]]:
        """
        Парсинг текста выписки Сбербанка.
        
        Пример формата (Сбербанк Бизнес Онлайн):
        Дата операции | Сумма | Плательщик | Назначение платежа
        01.01.2024 | 5000.00 | Иванов И.И. | Членские взносы за 2024
        """
        transactions = []
        lines = text.split('\n')
        
        # Ищем строки с датами и суммами
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Паттерн: дата + сумма + текст (универсальный)
            # Ищем дату в формате ДД.ММ.ГГГГ
            date_pattern = r'(\d{2}\.\d{2}\.\d{4})'
            amount_pattern = r'([\d\s]+[.,]\d{2})'
            
            date_match = re.search(date_pattern, line)
            amount_match = re.search(amount_pattern, line)
            
            if date_match and amount_match:
                date_str = date_match.group(1)
                amount_str = amount_match.group(1).replace(' ', '').replace(',', '.')
                
                # Всё после даты и суммы — плательщик и назначение
                rest_of_line = line[amount_match.end():].strip()
                
                # Разделяем по разделителям Сбербанка
                parts = re.split(r'\s{2,}|\t|\|', rest_of_line)
                
                payer_name = parts[0] if parts else ''
                payment_purpose = ' '.join(parts[1:]) if len(parts) > 1 else ''
                
                try:
                    amount = Decimal(amount_str)
                    if amount > 0:  # Только поступления
                        transactions.append({
                            'transaction_date': datetime.strptime(date_str, '%d.%m.%Y').date(),
                            'amount': amount,
                            'payer_name': payer_name.strip(),
                            'payment_purpose': payment_purpose.strip(),
                        })
                except (ValueError, TypeError):
                    continue
        
        logger.info(f'Найдено {len(transactions)} транзакций в PDF')
        return transactions
    
    def _parse_tinkoff_text(self, text: str) -> List[Dict[str, Any]]:
        """Парсинг текста выписки Тинькофф"""
        transactions = []
        lines = text.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            date_pattern = r'(\d{2}\.\d{2}\.\d{4})'
            amount_pattern = r'([\d\s]+[.,]\d{2})\s*(?:₽|руб|RUB)?'
            
            date_match = re.search(date_pattern, line)
            amount_match = re.search(amount_pattern, line)
            
            if date_match and amount_match:
                try:
                    date_str = date_match.group(1)
                    amount_str = amount_match.group(1).replace(' ', '').replace(',', '.')
                    amount = Decimal(amount_str)
                    
                    if amount > 0:
                        # Ищем плательщика (обычно после суммы)
                        rest = line[amount_match.end():].strip()
                        
                        transactions.append({
                            'transaction_date': datetime.strptime(date_str, '%d.%m.%Y').date(),
                            'amount': amount,
                            'payer_name': rest[:100] if rest else '',
                            'payment_purpose': rest if rest else '',
                        })
                except (ValueError, TypeError):
                    continue
        
        return transactions
    
    def _parse_alfa_text(self, text: str) -> List[Dict[str, Any]]:
        """Парсинг текста выписки Альфа-Банк"""
        transactions = []
        lines = text.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            date_pattern = r'(\d{2}\.\d{2}\.\d{4})'
            amount_pattern = r'([\d\s]+[.,]\d{2})'
            
            date_match = re.search(date_pattern, line)
            amount_match = re.search(amount_pattern, line)
            
            if date_match and amount_match:
                try:
                    date_str = date_match.group(1)
                    amount_str = amount_match.group(1).replace(' ', '').replace(',', '.')
                    amount = Decimal(amount_str)
                    
                    if amount > 0:
                        rest = line[amount_match.end():].strip()
                        transactions.append({
                            'transaction_date': datetime.strptime(date_str, '%d.%m.%Y').date(),
                            'amount': amount,
                            'payer_name': rest[:100] if rest else '',
                            'payment_purpose': rest if rest else '',
                        })
                except (ValueError, TypeError):
                    continue
        
        return transactions
    
    def _parse_generic_text(self, text: str) -> List[Dict[str, Any]]:
        """
        Универсальный парсинг текста выписки.
        Ищет строки с датами и суммами.
        """
        transactions = []
        lines = text.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Ищем дату
            date_pattern = r'(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})'
            amount_pattern = r'([\d\s]+[.,]\d{2})'
            
            date_match = re.search(date_pattern, line)
            amount_match = re.search(amount_pattern, line)
            
            if date_match and amount_match:
                try:
                    date_str = date_match.group(1)
                    amount_str = amount_match.group(1).replace(' ', '').replace(',', '.')
                    amount = Decimal(amount_str)
                    
                    if amount > 0:  # Только поступления
                        # Извлекаем текст между датой и суммой (плательщик)
                        # и после суммы (назначение)
                        parts = re.split(r'\s{2,}|\t|\|', line)
                        
                        payer_name = ''
                        payment_purpose = ''
                        
                        if len(parts) >= 2:
                            # Первая часть с датой
                            # Ищем часть с именем (обычно после даты)
                            for part in parts:
                                if re.search(r'[а-яА-ЯёЁ]', part) and not re.search(r'^\d', part):
                                    payer_name = part.strip()
                                    break
                            
                            # Назначение — всё остальное
                            other_parts = [p for p in parts if p != payer_name and not re.search(date_pattern, p) and not re.search(amount_pattern.replace('([', '(['), p)]
                            payment_purpose = ' '.join(other_parts).strip()
                        
                        transactions.append({
                            'transaction_date': datetime.strptime(date_str, '%d.%m.%Y').date(),
                            'amount': amount,
                            'payer_name': payer_name,
                            'payment_purpose': payment_purpose,
                        })
                except (ValueError, TypeError):
                    continue
        
        logger.info(f'Универсальный парсер: найдено {len(transactions)} транзакций')
        return transactions
    
    def _parse_csv(self, file_path: str) -> List[Dict[str, Any]]:
        """Парсинг CSV выписки"""
        transactions = []
        
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            headers = {h.lower().strip(): h for h in reader.fieldnames if h}
            
            for row in reader:
                transaction = self._extract_transaction(row, headers)
                if transaction:
                    transactions.append(transaction)
        
        return transactions
    
    def _parse_excel(self, file_path: str) -> List[Dict[str, Any]]:
        """Парсинг Excel выписки"""
        try:
            import openpyxl
        except ImportError:
            raise ImportError('Установите openpyxl: pip install openpyxl')
        
        transactions = []
        wb = openpyxl.load_workbook(file_path, data_only=True)
        ws = wb.active
        
        headers = {}
        for col in range(1, ws.max_column + 1):
            header = ws.cell(1, col).value
            if header:
                headers[header.lower().strip()] = header
        
        for row in range(2, ws.max_row + 1):
            row_data = {}
            for col in range(1, ws.max_column + 1):
                header = ws.cell(1, col).value
                value = ws.cell(row, col).value
                if header:
                    row_data[header] = value
            
            transaction = self._extract_transaction(row_data, headers)
            if transaction:
                transactions.append(transaction)
        
        return transactions
    
    def _parse_json(self, file_path: str) -> List[Dict[str, Any]]:
        """Парсинг JSON выписки"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        transactions = []
        operations = data.get('operations', data.get('transactions', []))
        
        for op in operations:
            transactions.append({
                'transaction_date': self._parse_date(op.get('date', op.get('operationTime'))),
                'amount': Decimal(str(op.get('amount', 0))),
                'payer_name': op.get('description', op.get('counterparty', '')),
                'payer_account': op.get('account', op.get('payerAccount', '')),
                'payer_inn': op.get('inn', op.get('payerInn', '')),
                'payment_purpose': op.get('purpose', op.get('paymentPurpose', '')),
            })
        
        return transactions
    
    def _parse_1c(self, file_path: str) -> List[Dict[str, Any]]:
        """Парсинг выписки 1С (текстовый формат)"""
        transactions = []
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        pattern = r'(\d{2}\.\d{2}\.\d{4}).*?(\d+[\.,]\d{2}).*?Плательщик:\s*(.*?)(?:\n|$)'
        matches = re.findall(pattern, content, re.MULTILINE)
        
        for match in matches:
            date_str, amount_str, payer = match
            transactions.append({
                'transaction_date': datetime.strptime(date_str, '%d.%m.%Y').date(),
                'amount': Decimal(amount_str.replace(',', '.')),
                'payer_name': payer.strip(),
                'payment_purpose': '',
            })
        
        return transactions
    
    def _extract_transaction(self, row: Dict[str, Any], headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Извлечь данные транзакции из строки"""
        date_val = self._get_field(row, headers, 'date_col')
        amount_val = self._get_field(row, headers, 'amount_col')
        payer_val = self._get_field(row, headers, 'payer_col')
        
        if not date_val or not amount_val:
            return None
        
        try:
            amount = self._parse_amount(amount_val)
        except (ValueError, TypeError):
            return None
        
        if amount <= 0:
            return None
        
        return {
            'transaction_date': self._parse_date(date_val),
            'amount': amount,
            'payer_name': str(payer_val).strip() if payer_val else '',
            'payer_account': str(self._get_field(row, headers, 'account_col', '')).strip(),
            'payer_inn': str(self._get_field(row, headers, 'inn_col', '')).strip(),
            'payment_purpose': str(self._get_field(row, headers, 'purpose_col', '')).strip(),
        }
    
    def _get_field(self, row: Dict[str, Any], headers: Dict[str, str], field_type: str, default: Any = None) -> Any:
        """Получить значение поля по паттерну банка"""
        if not self.bank_pattern:
            for bank_pattern in self.BANK_PATTERNS.values():
                for pattern in bank_pattern.get(field_type, []):
                    for header, value in row.items():
                        if pattern.lower() in header.lower():
                            return value
        
        for pattern in self.bank_pattern.get(field_type, []):
            for header, value in row.items():
                if pattern.lower() in header.lower():
                    return value
        
        return default
    
    def _parse_date(self, value: Any) -> Optional[datetime.date]:
        """Парсинг даты из разных форматов"""
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            formats = [
                '%d.%m.%Y', '%d.%m.%y', '%Y-%m-%d',
                '%d/%m/%Y', '%m/%d/%Y', '%Y.%m.%d',
            ]
            for fmt in formats:
                try:
                    return datetime.strptime(value.strip(), fmt).date()
                except ValueError:
                    continue
        return None
    
    def _parse_amount(self, value: Any) -> Decimal:
        """Парсинг суммы из разных форматов"""
        if isinstance(value, (int, float)):
            return Decimal(str(value))
        if isinstance(value, str):
            cleaned = value.replace(' ', '').replace(',', '.')
            cleaned = re.sub(r'[^\d.-]', '', cleaned)
            return Decimal(cleaned)
        return Decimal('0')

    def _parse_pdf_table(self, file_path: str) -> List[Dict[str, Any]]:
        """
        Парсинг табличных данных из PDF (более точный метод).
        Использует pdfplumber для извлечения таблиц.
        """
        transactions = []

        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    # Извлекаем таблицы
                    tables = page.extract_tables()
                    for table in tables:
                        if not table:
                            continue
                        
                        # Ищем заголовки
                        headers = []
                        data_start_row = 0

                        for row_idx, row in enumerate(table):
                            if not row:
                                continue
                            
                            # Проверяем, является ли строка заголовком
                            row_text = ' '.join([str(c) for c in row if c])
                            if any(keyword in row_text.lower() for keyword in ['дата', 'сумма', 'плательщик', 'назначение']):
                                headers = [str(h).lower().strip() if h else '' for h in row]
                                data_start_row = row_idx + 1
                                break
                            
                        # Если заголовки не найдены, пробуем парсить все строки
                        if not headers:
                            for row in table[1:]:  # Пропускаем первую строку
                                transaction = self._parse_table_row(row, [])
                                if transaction:
                                    transactions.append(transaction)
                        else:
                            for row in table[data_start_row:]:
                                transaction = self._parse_table_row(row, headers)
                                if transaction:
                                    transactions.append(transaction)

                    # Если таблицы не найдены, пробуем извлечь текст
                    if not transactions:
                        text = page.extract_text()
                        if text:
                            logger.info(f'Текст со страницы: {text[:500]}')
        except ImportError:
            logger.warning('pdfplumber не установлен')
        except Exception as e:
            logger.error(f'Ошибка извлечения таблиц: {e}')

        return transactions


    def _parse_table_row(self, row: List, headers: List[str]) -> Optional[Dict[str, Any]]:
        """Парсинг одной строки таблицы"""
        if not row or all(c is None or str(c).strip() == '' for c in row):
            return None

        # Очищаем значения
        values = [str(c).strip() if c else '' for c in row]
        row_text = ' '.join(values)

        # Ищем дату
        date_pattern = r'(\d{2}\.\d{2}\.\d{4})'
        date_match = re.search(date_pattern, row_text)
        if not date_match:
            return None

        date_str = date_match.group(1)

        # Ищем сумму (число с точкой или запятой)
        amount_pattern = r'([\d\s]+[.,]\d{2})'
        amount_match = re.search(amount_pattern, row_text)
        if not amount_match:
            return None

        try:
            amount_str = amount_match.group(1).replace(' ', '').replace(',', '.')
            amount = Decimal(amount_str)
        except:
            return None

        if amount <= 0:
            return None

        # Определяем плательщика и назначение
        if headers:
            # Пытаемся найти по заголовкам
            payer_name = ''
            payment_purpose = ''

            for i, header in enumerate(headers):
                if i < len(values):
                    if 'плательщик' in header or 'контрагент' in header or 'отправитель' in header:
                        payer_name = values[i]
                    elif 'назначение' in header or 'purpose' in header:
                        payment_purpose = values[i]

            if not payer_name:
                # Берём текст между датой и суммой
                parts = re.split(r'\s{2,}|\t|\|', row_text)
                for part in parts:
                    if part != date_str and not re.match(r'^[\d\s.,]+$', part):
                        payer_name = part
                        break
        else:
            # Извлекаем плательщика из текста
            parts = re.split(r'\s{2,}|\t|\|', row_text)
            payer_name = ''
            payment_purpose = ''

            for part in parts:
                clean_part = part.strip()
                if clean_part and clean_part != date_str and not re.match(r'^[\d\s.,]+$', clean_part):
                    if not payer_name:
                        payer_name = clean_part
                    else:
                        payment_purpose += ' ' + clean_part

        try:
            return {
                'transaction_date': datetime.strptime(date_str, '%d.%m.%Y').date(),
                'amount': amount,
                'payer_name': payer_name.strip()[:200],
                'payment_purpose': payment_purpose.strip()[:500],
            }
        except ValueError:
            return None


    
    
    def match_assessment(self, owner: Any, amount: Decimal, payment_purpose: str) -> Optional[Any]:
        """Поиск подходящего начисления для платежа"""
        from .models import Assessment
        
        # Проверяем уникальный ID
        snt_id_pattern = r'SNT-(\d{6})'
        snt_match = re.search(snt_id_pattern, payment_purpose, re.IGNORECASE)
        if snt_match:
            assessment_id = int(snt_match.group(1))
            try:
                return Assessment.objects.get(
                    id=assessment_id,
                    owner=owner,
                    status__in=[Assessment.STATUS_PENDING, Assessment.STATUS_PARTIAL, Assessment.STATUS_OVERDUE]
                )
            except Assessment.DoesNotExist:
                pass
        
        # Ищем неоплаченные начисления
        assessments = Assessment.objects.filter(
            owner=owner,
            status__in=[Assessment.STATUS_PENDING, Assessment.STATUS_PARTIAL, Assessment.STATUS_OVERDUE]
        ).order_by('period__due_date')
        
        exact_match = assessments.filter(amount=amount).first()
        if exact_match:
            return exact_match
        
        for assessment in assessments:
            if assessment.debt >= amount:
                return assessment
        
        return assessments.first()
    
    
class PaymentMatcher:
    """Сопоставление банковских транзакций с владельцами и начислениями"""
    
    def __init__(self):
        from users.models import Owner, ContactInfo
        from land.models import LandPlot
        self.Owner = Owner
        self.ContactInfo = ContactInfo
        self.LandPlot = LandPlot
    
    def match_owner(self, transaction: Dict[str, Any]) -> Optional[Tuple[Any, float]]:
        """
        Поиск владельца по транзакции.
        Возвращает (owner, confidence) или None.
        """
        payer_name = transaction.get('payer_name', '').lower()
        payer_account = transaction.get('payer_account', '')
        payer_inn = transaction.get('payer_inn', '')
        payment_purpose = transaction.get('payment_purpose', '').lower()
        
        candidates: List[Tuple[Any, float]] = []
        
        # 1. Поиск по уникальному ID начисления (из QR-кода)
        snt_id_pattern = r'SNT-(\d{6})'
        snt_match = re.search(snt_id_pattern, payment_purpose, re.IGNORECASE)
        if snt_match:
            assessment_id = int(snt_match.group(1))
            from .models import Assessment
            try:
                assessment = Assessment.objects.select_related('owner').get(id=assessment_id)
                return (assessment.owner, 100.0)
            except Assessment.DoesNotExist:
                pass
        
        # 2. Поиск по ФИО в назначении платежа
        owners = self.Owner.objects.all()
        for owner in owners:
            confidence: float = 0.0
            owner_name_parts = owner.full_name.lower().split()
            
            # Проверяем ФИО в плательщике
            name_match = sum(1 for part in owner_name_parts if part in payer_name)
            if name_match >= 2:
                confidence += 50
            elif name_match >= 1:
                confidence += 20
            
            # Проверяем ФИО в назначении
            name_in_purpose = sum(1 for part in owner_name_parts if part in payment_purpose)
            if name_in_purpose >= 2:
                confidence += 60
            
            # Проверяем номер телефона в назначении
            contacts = self.ContactInfo.objects.filter(
                owner=owner, type='ph', is_active=True
            )
            for contact in contacts:
                clean_phone = ''.join(c for c in contact.value if c.isdigit())[-10:]
                if clean_phone and clean_phone in payment_purpose:
                    confidence += 90
                    break
            
            # Проверяем номер участка в назначении
            for plot in owner.land_plots.all():
                if plot.plot_number.lower() in payment_purpose:
                    confidence += 80
                    break
            
            # Проверяем кадастровый номер
            for plot in owner.land_plots.all():
                if plot.cadastral_number.lower() in payment_purpose:
                    confidence += 95
                    break
            
            if confidence > 0:
                candidates.append((owner, confidence))
        
        candidates.sort(key=lambda x: x[1], reverse=True)
        
        if candidates and candidates[0][1] >= 30:
            return candidates[0]
        
        return None
    
    def match_assessment(self, owner: Any, amount: Decimal, payment_purpose: str) -> Optional[Any]:
        """Поиск подходящего начисления для платежа"""
        from .models import Assessment
        
        # Проверяем уникальный ID
        snt_id_pattern = r'SNT-(\d{6})'
        snt_match = re.search(snt_id_pattern, payment_purpose, re.IGNORECASE)
        if snt_match:
            assessment_id = int(snt_match.group(1))
            try:
                return Assessment.objects.get(
                    id=assessment_id,
                    owner=owner,
                    status__in=[Assessment.STATUS_PENDING, Assessment.STATUS_PARTIAL, Assessment.STATUS_OVERDUE]
                )
            except Assessment.DoesNotExist:
                pass
        
        # Ищем неоплаченные начисления
        assessments = Assessment.objects.filter(
            owner=owner,
            status__in=[Assessment.STATUS_PENDING, Assessment.STATUS_PARTIAL, Assessment.STATUS_OVERDUE]
        ).order_by('period__due_date')
        
        exact_match = assessments.filter(amount=amount).first()
        if exact_match:
            return exact_match
        
        for assessment in assessments:
            if assessment.debt >= amount:
                return assessment
        
        return assessments.first()
    
    def process_and_update_payments(self, transaction: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Обработка транзакции с автоматическим обновлением статуса начисления.
        Возвращает информацию о сопоставленном платеже.
        """
        from .models import Assessment, Payment
        from decimal import Decimal
        
        amount = transaction.get('amount', Decimal('0'))
        payment_purpose = transaction.get('payment_purpose', '')
        payer_name = transaction.get('payer_name', '')
        
        result = {
            'matched': False,
            'payment_created': False,
            'assessment_updated': False,
            'message': ''
        }
        
        # 1. Поиск владельца
        owner_match = self.match_owner(transaction)
        if not owner_match:
            result['message'] = f'Не найден владелец для {payer_name}'
            return result
        
        owner, confidence = owner_match
        result['matched_owner'] = owner.full_name
        result['confidence'] = confidence
        
        # 2. Поиск начисления
        assessment = self.match_assessment(owner, amount, payment_purpose)
        if not assessment:
            result['message'] = f'Не найдено подходящее начисление для {owner.full_name}'
            return result
        
        result['matched_assessment_id'] = assessment.id
        result['matched_assessment_amount'] = str(assessment.amount)
        result['current_debt'] = str(assessment.debt)
        
        # 3. Создаём платёж
        payment = Payment.objects.create(
            assessment=assessment,
            amount=amount,
            payment_date=transaction.get('transaction_date'),
            payment_method='bank',
            bank_name=transaction.get('bank_name', ''),
            bank_account=transaction.get('payer_account', ''),
            transaction_id=transaction.get('transaction_id', ''),
            payment_purpose=payment_purpose[:500],
            status=Payment.STATUS_PROCESSED,
        )
        
        result['payment_created'] = True
        result['payment_id'] = payment.id
        result['payment_amount'] = str(amount)
        
        # 4. Проверяем статус начисления после оплаты
        assessment.refresh_from_db()
        result['new_debt'] = str(assessment.debt)
        result['assessment_status'] = assessment.get_status_display()
        
        if assessment.status == Assessment.STATUS_PAID:
            result['message'] = f'✅ Начисление полностью оплачено!'
        else:
            result['message'] = f'💰 Внесён платёж {amount} ₽. Остаток долга: {assessment.debt} ₽'
        
        result['matched'] = True
        return result