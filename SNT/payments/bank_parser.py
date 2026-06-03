import re
import csv
import json
import logging
from datetime import date, datetime
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
        logger.info(f"Начинаем парсинг файла: {file_path}")

        try:
            if file_path.endswith('.pdf'):
                transactions = self._parse_pdf(file_path)
                # Гарантируем, что возвращается список
                if transactions is None:
                    transactions = []
                logger.info(f"PDF парсинг завершён, найдено {len(transactions)} транзакций")

                # Выводим каждую транзакцию для отладки
                for i, trans in enumerate(transactions):
                    logger.info(f"Транзакция {i+1}: дата={trans.get('transaction_date')}, "
                               f"сумма={trans.get('amount')}, плательщик={trans.get('payer_name')}, "
                               f"UID={trans.get('matched_uid')}")

                return transactions

            elif file_path.endswith('.csv'):
                return self._parse_csv(file_path)
            elif file_path.endswith('.xlsx') or file_path.endswith('.xls'):
                return self._parse_excel(file_path)
            elif file_path.endswith('.json'):
                return self._parse_json(file_path)
            elif file_path.endswith('.txt'):
                return self._parse_1c(file_path)
            else:
                raise ValueError(f'Неподдерживаемый формат файла: {file_path}')
        except Exception as e:
            logger.error(f"Ошибка парсинга: {e}", exc_info=True)
            return []  # Всегда возвращаем список, даже при ошибке
    
    def _parse_alfa_receipt(self, text: str) -> List[Dict[str, Any]]:
        """
        Специальный парсер для квитанций Альфа-Банка
        """
        transactions = []
        
        logger.info("Парсинг квитанции Альфа-Банка...")
        
        # Нормализуем текст - разбиваем на строки для лучшего парсинга
        lines = text.split('\n')
        
        # Извлекаем сумму (ищем "Сумма перевода" и число)
        amount_patterns = [
            r'Сумма\s+перевода\s+([\d\s]+,\d{2})\s*RUR',
            r'Сумма\s+перевода.*?([\d\s]+,\d{2})\s*RUR',
        ]
        
        amount = None
        for pattern in amount_patterns:
            amount_match = re.search(pattern, text, re.DOTALL)
            if amount_match:
                amount_str = amount_match.group(1).replace(' ', '').replace(',', '.')
                amount = Decimal(amount_str)
                logger.info(f"Найдена сумма: {amount}")
                break
            
        if amount is None:
            logger.error("Не найдена сумма в квитанции")
            return []
        
        # Извлекаем дату
        date_patterns = [
            r'Дата\s+и\s+время\s+перевода\s+(\d{2}\.\d{2}\.\d{4})',
            r'(\d{2}\.\d{2}\.\d{4})\s+\d{2}:\d{2}:\d{2}',
        ]
        
        transaction_date = date.today()
        for pattern in date_patterns:
            date_match = re.search(pattern, text)
            if date_match:
                date_str = date_match.group(1)
                try:
                    transaction_date = datetime.strptime(date_str, '%d.%m.%Y').date()
                    logger.info(f"Найдена дата: {transaction_date}")
                    break
                except:
                    pass
                
        # Извлекаем плательщика (улучшенный поиск)
        payer_name = ''
        
        # Ищем "Плательщик" и следующую строку
        for i, line in enumerate(lines):
            if 'Плательщик' in line and i + 1 < len(lines):
                payer_name = lines[i + 1].strip()
                if payer_name and len(payer_name) > 5:
                    logger.info(f"Найден плательщик (по строке): {payer_name}")
                    break
                
        # Если не нашли, ищем по шаблону ФИО
        if not payer_name:
            name_patterns = [
                r'Плательщик\s+([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+)',
                r'Плательщик\s+([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+)',
                r'([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+)\s+Счёт\s+списания',
            ]
            
            for pattern in name_patterns:
                name_match = re.search(pattern, text)
                if name_match:
                    payer_name = name_match.group(1).strip()
                    logger.info(f"Найден плательщик (по шаблону): {payer_name}")
                    break
                
        # Если всё ещё не нашли, ищем в конце текста после "Назначение перевода"
        if not payer_name:
            # Ищем ФИО в формате "Фамилия Имя Отчество"
            name_pattern = r'([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+)'
            names = re.findall(name_pattern, text)
            # Берём имя, которое не является получателем
            for name in names:
                if name != 'Строитель-43' and 'Строитель' not in name:
                    payer_name = name
                    logger.info(f"Найден плательщик (по ФИО): {payer_name}")
                    break
                
        # Извлекаем назначение платежа
        purpose_patterns = [
            r'Назначение\s+перевода\s+(.+?)(?:\s+[А-Я][а-я]+:|$)',
            r'Назначение\s+перевода\s+(.+?)$',
        ]
        
        payment_purpose = ''
        for pattern in purpose_patterns:
            purpose_match = re.search(pattern, text, re.DOTALL)
            if purpose_match:
                payment_purpose = purpose_match.group(1).strip()
                payment_purpose = ' '.join(payment_purpose.split())
                logger.info(f"Найдено назначение: {payment_purpose[:100]}...")
                break
            
        # Извлекаем номер операции
        operation_match = re.search(r'Номер\s+операции\s+(\w+)', text)
        transaction_id = operation_match.group(1) if operation_match else ''
        
        # Извлекаем UID начисления
        snt_id_pattern = r'SNT-(\d{6})'
        snt_match = re.search(snt_id_pattern, payment_purpose)
        matched_uid = f"SNT-{snt_match.group(1)}" if snt_match else ''
        
        # Если не нашли в назначении, ищем во всём тексте
        if not matched_uid:
            snt_match = re.search(r'ID:?(SNT-\d{6})', text)
            if snt_match:
                matched_uid = snt_match.group(1)
        
        # Извлекаем номер участка
        plot_match = re.search(r'Уч\.№(\d+)', payment_purpose)
        plot_number = plot_match.group(1) if plot_match else ''
        
        transaction = {
            'transaction_date': transaction_date,
            'amount': amount,
            'payer_name': payer_name,
            'payment_purpose': payment_purpose,
            'transaction_id': transaction_id,
            'matched_uid': matched_uid,
            'plot_number': plot_number,
        }
        
        logger.info(f"✅ Распознан платёж: {amount} ₽ от {payer_name or 'неизвестного'}, UID: {matched_uid}")
        transactions.append(transaction)
        
        return transactions

    
    def _parse_pdf(self, file_path: str) -> List[Dict[str, Any]]:
        """
        Парсинг PDF выписки или квитанции
        """
        # Сначала пробуем извлечь таблицы
        transactions = self._parse_pdf_table(file_path)
        if transactions and len(transactions) > 0:
            logger.info(f'Извлечено {len(transactions)} транзакций из таблиц PDF')
            return transactions

        # Если таблиц нет - извлекаем текст
        text = self._extract_text_from_pdf(file_path)

        # Проверяем, что текст не пустой
        if not text or not text.strip():
            logger.error("Не удалось извлечь текст из PDF - файл пустой или защищён")
            return []

        logger.info(f'Извлечён текст из PDF ({len(text)} символов)')
        logger.debug(f'Первые 500 символов: {text[:500]}')

        # Пробуем разные форматы

        # 1. Квитанция Альфа-Банка
        if 'Квитанция о переводе' in text or 'АО "АЛЬФА-БАНК"' in text:
            result = self._parse_alfa_receipt(text)
            if result:
                return result
            else:
                logger.warning("Не удалось распарсить как квитанцию Альфа-Банка, пробуем другие форматы")

        # 2. Квитанция Сбербанка
        if 'Сбербанк' in text and 'Квитанция' in text:
            result = self._parse_sberbank_receipt(text)
            if result:
                return result

        # 3. Выписка Сбербанка
        text_lower = text.lower()
        if 'сбербанк' in text_lower or 'sberbank' in text_lower:
            return self._parse_sberbank_text(text)
        elif 'тинькофф' in text_lower or 'tinkoff' in text_lower:
            return self._parse_tinkoff_text(text)
        elif 'альфа' in text_lower or 'alfa' in text_lower:
            return self._parse_alfa_text(text)
        else:
            return self._parse_generic_text(text)
    
    def _parse_sberbank_receipt(self, text: str) -> List[Dict[str, Any]]:
        """
        Парсер для квитанций Сбербанка
        """
        transactions = []
        
        logger.info("Парсинг квитанции Сбербанка...")
        
        # Извлекаем сумму
        amount_patterns = [
            r'Сумма\s+перевода[:\s]*([\d\s]+,\d{2})\s*₽',
            r'Сумма[:\s]*([\d\s]+,\d{2})\s*₽',
        ]
        
        amount = None
        for pattern in amount_patterns:
            amount_match = re.search(pattern, text)
            if amount_match:
                amount_str = amount_match.group(1).replace(' ', '').replace(',', '.')
                amount = Decimal(amount_str)
                break
        
        if amount is None:
            logger.error("Не найдена сумма в квитанции Сбербанка")
            return []
        
        # Извлекаем дату
        date_pattern = r'Дата\s+(\d{2}\.\d{2}\.\d{4})'
        date_match = re.search(date_pattern, text)
        if date_match:
            date_str = date_match.group(1)
            transaction_date = datetime.strptime(date_str, '%d.%m.%Y').date()
        else:
            transaction_date = date.today()
        
        # Извлекаем плательщика
        payer_pattern = r'Плательщик[:\s]+([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+)'
        payer_match = re.search(payer_pattern, text)
        payer_name = payer_match.group(1) if payer_match else ''
        
        # Извлекаем назначение
        purpose_pattern = r'Назначение\s+платежа[:\s]+(.+?)(?:\n|$)'
        purpose_match = re.search(purpose_pattern, text, re.DOTALL)
        payment_purpose = purpose_match.group(1).strip() if purpose_match else ''
        
        # Номер операции
        operation_pattern = r'Номер\s+операции[:\s]+(\w+)'
        operation_match = re.search(operation_pattern, text)
        transaction_id = operation_match.group(1) if operation_match else ''
        
        # UID начисления
        snt_id_pattern = r'SNT-(\d{6})'
        snt_match = re.search(snt_id_pattern, payment_purpose)
        matched_uid = f"SNT-{snt_match.group(1)}" if snt_match else ''
        
        transaction = {
            'transaction_date': transaction_date,
            'amount': amount,
            'payer_name': payer_name,
            'payment_purpose': payment_purpose,
            'transaction_id': transaction_id,
            'matched_uid': matched_uid,
        }
        
        transactions.append(transaction)
        return transactions

    def _extract_text_from_pdf(self, file_path: str) -> str:
        """
        Извлечение текста из PDF с использованием нескольких методов
        Всегда возвращает строку (пустую в случае ошибки)
        """
        text = ""

        # Метод 1: pdfplumber
        try:
            import pdfplumber
            logger.info("Пробуем pdfplumber...")
            with pdfplumber.open(file_path) as pdf:
                for page_num, page in enumerate(pdf.pages, 1):
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                        logger.info(f"Страница {page_num}: извлечено {len(page_text)} символов")
                    else:
                        # Пробуем extract_text с другими параметрами
                        page_text = page.extract_text(layout=True)
                        if page_text:
                            text += page_text + "\n"
                            logger.info(f"Страница {page_num} (layout): {len(page_text)} символов")

            if text and text.strip():
                logger.info(f"pdfplumber извлёк {len(text)} символов")
                return text
        except ImportError:
            logger.warning('pdfplumber не установлен')
        except Exception as e:
            logger.error(f'Ошибка pdfplumber: {e}')

        # Метод 2: PyPDF2
        try:
            from PyPDF2 import PdfReader
            logger.info("Пробуем PyPDF2...")
            reader = PdfReader(file_path)
            for page_num, page in enumerate(reader.pages, 1):
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
                    logger.info(f"Страница {page_num}: {len(page_text)} символов")

            if text and text.strip():
                logger.info(f"PyPDF2 извлёк {len(text)} символов")
                return text
        except ImportError:
            logger.warning('PyPDF2 не установлен')
        except Exception as e:
            logger.error(f'Ошибка PyPDF2: {e}')

        # Метод 3: pdfminer
        try:
            from pdfminer.high_level import extract_text
            logger.info("Пробуем pdfminer...")
            extracted = extract_text(file_path)
            if extracted and extracted.strip():
                text = extracted
                logger.info(f"pdfminer извлёк {len(text)} символов")
                return text
        except ImportError:
            logger.warning('pdfminer не установлен')
        except Exception as e:
            logger.error(f'Ошибка pdfminer: {e}')

        # Метод 4: пробуем прочитать как бинарный файл
        try:
            with open(file_path, 'rb') as f:
                raw_data = f.read()
                # Пробуем декодировать как utf-8
                try:
                    decoded = raw_data.decode('utf-8', errors='ignore')
                    if decoded and decoded.strip():
                        logger.info(f"RAW текст извлечён: {len(decoded)} символов")
                        return decoded
                except:
                    pass
        except Exception as e:
            logger.error(f'Ошибка чтения RAW: {e}')

        # Всегда возвращаем строку, даже пустую
        logger.warning("Не удалось извлечь текст, возвращаем пустую строку")
        return ""  # Никогда не возвращаем None
    
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
        matched_uid = transaction.get('matched_uid', '')  # Добавляем UID из квитанции

        candidates: List[Tuple[Any, float]] = []

        # 1. Поиск по UID из квитанции (самый точный метод)
        if matched_uid:
            from .models import Assessment
            try:
                assessment = Assessment.objects.select_related('owner').get(payment_uid=matched_uid)
                return (assessment.owner, 100.0)
            except Assessment.DoesNotExist:
                logger.info(f'Начисление с UID {matched_uid} не найдено')

        # 2. Поиск по номеру участка из назначения
        plot_pattern = r'уч\.?№?(\d+)'
        plot_match = re.search(plot_pattern, payment_purpose, re.IGNORECASE)
        if plot_match:
            plot_number = plot_match.group(1)
            from land.models import LandPlot
            try:
                plot = LandPlot.objects.select_related('owners').filter(plot_number=plot_number).first()
                if plot and plot.owners.exists():
                    owner = plot.owners.first()
                    return (owner, 95.0)
            except Exception as e:
                logger.warning(f'Ошибка поиска по участку: {e}')

        # 3. Поиск по уникальному ID начисления в тексте
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
            
        # 4. Поиск по ФИО в назначении платежа
        owners = self.Owner.objects.all()
        for owner in owners:
            confidence: float = 0.0
            owner_name_parts = owner.full_name.lower().split()

            # Проверяем ФИО в плательщике
            name_match = sum(1 for part in owner_name_parts if part and part in payer_name)
            if name_match >= 2:
                confidence += 50
            elif name_match >= 1:
                confidence += 20

            # Проверяем ФИО в назначении
            name_in_purpose = sum(1 for part in owner_name_parts if part and part in payment_purpose)
            if name_in_purpose >= 2:
                confidence += 60

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

        # 3. Создаём уникальный transaction_id, если его нет
        transaction_id = transaction.get('transaction_id', '')
        if transaction_id:
            existing_payment = Payment.objects.filter(transaction_id=transaction_id).first()
            if existing_payment:
                result['matched'] = True
                result['payment_exists'] = True
                result['payment_id'] = existing_payment.id
                result['message'] = f'Платёж уже обработан (ID: {existing_payment.id})'
                return result

        # Также проверяем по UID и сумме
        matched_uid = transaction.get('matched_uid', '')
        if matched_uid:
            amount = transaction.get('amount')
            existing_payment = Payment.objects.filter(
                matched_uid=matched_uid,
                amount=amount
            ).first()
            if existing_payment:
                result['matched'] = True
                result['payment_exists'] = True
                result['payment_id'] = existing_payment.id
                result['message'] = f'Платёж с UID {matched_uid} уже обработан'
                return result
        
        # Проверяем, не существует ли уже платеж с таким transaction_id
        from .models import Payment
        existing_payment = Payment.objects.filter(transaction_id=transaction_id).first()
        if existing_payment:
            result['message'] = f'Платёж с таким transaction_id уже существует'
            result['payment_exists'] = True
            result['payment_id'] = existing_payment.id
            return result

        # Создаём платёж
        payment = Payment.objects.create(
            assessment=assessment,
            amount=amount,
            payment_date=transaction.get('transaction_date'),
            payment_method='bank',
            bank_name=transaction.get('bank_name', ''),
            bank_account=transaction.get('payer_account', ''),
            transaction_id=transaction_id,  # Теперь всегда уникальный
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