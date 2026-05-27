# payments/email_service.py - исправленная версия

import logging
from decimal import Decimal
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from .models import Assessment

logger = logging.getLogger(__name__)


class EmailReceiptService:
    """Сервис для отправки квитанций по email"""
    
    def __init__(self):
        # Отложенная инициализация
        self._qr_gen = None
        self._snt_gen = None
        self._snt_details = None
        self._initialized = False
    
    def _ensure_initialized(self):
        """Ленивая инициализация - выполняется при первом обращении"""
        if self._initialized:
            return
        
        try:
            from .qr_generator import QRCodeGenerator, SNTDetailsGenerator
            self._qr_gen = QRCodeGenerator()
            self._snt_gen = SNTDetailsGenerator()
            self._snt_details = self._snt_gen.get_details()
            self._initialized = True
        except Exception as e:
            logger.error(f"Error initializing EmailReceiptService: {e}")
            # Создаем заглушки
            self._snt_details = {
                'name': 'СНТ',
                'inn': '',
                'kpp': '',
                'account': '',
                'bank_name': '',
                'bank_bik': '',
                'bank_corr': '',
                'chairman': 'Председатель',
            }
            self._initialized = True
    
    @property
    def qr_gen(self):
        self._ensure_initialized()
        return self._qr_gen
    
    @property
    def snt_gen(self):
        self._ensure_initialized()
        return self._snt_gen
    
    @property
    def snt_details(self):
        self._ensure_initialized()
        return self._snt_details
    
    def get_owner_email(self, assessment: Assessment) -> Optional[str]:
        """Получить email владельца из ContactInfo"""
        email_contact = assessment.owner.contacts.filter(
            type='em',
            is_active=True
        ).first()
        return email_contact.value if email_contact else None
    
    def get_all_owner_emails(self, assessment: Assessment) -> List[str]:
        """Получить все активные email владельца"""
        emails = assessment.owner.contacts.filter(
            type='em',
            is_active=True
        ).values_list('value', flat=True)
        return list(emails)
    
    def generate_receipt_html(self, assessment: Assessment) -> str:
        """Генерирует HTML квитанции для email"""
        self._ensure_initialized()
        
        qr_data = None
        qr_image = ""
        
        if self._qr_gen:
            try:
                qr_data = self._qr_gen.generate_qr_data(
                    owner_name=assessment.owner.full_name,
                    plot_number=assessment.land_plot.plot_number,
                    amount=assessment.debt,
                    assessment_id=assessment.id,
                    period=str(assessment.period),
                    category_name=assessment.category.name,
                )
                qr_image = self._qr_gen.get_qr_data_uri(qr_data)
            except Exception as e:
                logger.error(f"Error generating QR: {e}")
        
        purpose = (
            f"Оплата {assessment.category.name} за {assessment.period}. "
            f"Уч.№{assessment.land_plot.plot_number}, "
            f"Владелец: {assessment.owner.full_name}, "
            f"UID:{assessment.payment_uid}. Без НДС."
        )
        
        context = {
            'assessment': assessment,
            'assessment_id': assessment.id,
            'qr_code': qr_image,
            'snt_details': self.snt_details,
            'owner_name': assessment.owner.full_name,
            'plot_number': assessment.land_plot.plot_number,
            'amount': str(assessment.debt),
            'uid': assessment.payment_uid,
            'due_date': str(assessment.period.due_date) if assessment.period.due_date else '',
            'purpose': purpose,
            'year': timezone.now().year,
        }
        
        return render_to_string('payments/email_receipt.html', context)
    
    def generate_receipt_pdf(self, assessment: Assessment) -> Optional[bytes]:
        """Генерация PDF через xhtml2pdf"""
        try:
            from xhtml2pdf import pisa
            from io import BytesIO
            
            html_content = self.generate_receipt_html(assessment)
            pdf_buffer = BytesIO()
            
            pisa_status = pisa.CreatePDF(html_content, dest=pdf_buffer)
            
            if pisa_status.err:
                logger.error(f"xhtml2pdf error: {pisa_status.err}")
                return None
            
            pdf_buffer.seek(0)
            logger.info(f"PDF generated for assessment {assessment.id}, size: {len(pdf_buffer.getvalue())} bytes")
            return pdf_buffer.getvalue()
            
        except ImportError as e:
            logger.error(f"xhtml2pdf not installed: {e}")
            return None
        except Exception as e:
            logger.error(f"PDF generation error: {e}")
            return None
    
    def send_receipt_to_owner(
        self, 
        assessment: Assessment, 
        recipient_email: str = None,
        send_pdf_attachment: bool = False
    ) -> Dict[str, Any]:
        """Отправляет квитанцию одному владельцу"""
        result = {
            'success': False,
            'assessment_id': assessment.id,
            'owner_name': assessment.owner.full_name,
            'recipient': recipient_email or self.get_owner_email(assessment),
            'message': '',
            'error': None,
        }
        
        email = recipient_email or self.get_owner_email(assessment)
        
        if not email:
            result['message'] = 'У владельца не указан активный email'
            return result
        
        try:
            html_content = self.generate_receipt_html(assessment)
            pdf_content = self.generate_receipt_pdf(assessment) if send_pdf_attachment else None
            
            subject = f"Квитанция №{assessment.payment_uid} - {self.snt_details['name']}"
            
            text_body = f"""Уважаемый(ая) {assessment.owner.full_name}!

Вам направлена квитанция на оплату {assessment.category.name} за {assessment.period} на сумму {assessment.debt} ₽.

{"Квитанция во вложении (PDF) и в теле письма." if pdf_content else "Квитанция в теле письма."}

Сумма к оплате: {assessment.debt} ₽
Срок оплаты: {assessment.period.due_date if assessment.period.due_date else '—'}

Для оплаты через мобильное приложение банка отсканируйте QR-код в теле письма.

С уважением,
{self.snt_details['name']}

---
Это письмо сформировано автоматически. Пожалуйста, не отвечайте на него.
"""
            
            msg = EmailMultiAlternatives(
                subject=subject,
                body=text_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[email],
            )
            
            msg.attach_alternative(html_content, "text/html")
            
            if pdf_content:
                filename = f"квитанция_{assessment.payment_uid}.pdf"
                msg.attach(filename, pdf_content, 'application/pdf')
            
            msg.send()
            
            result['success'] = True
            result['message'] = f"Квитанция отправлена на {email}"
            
            logger.info(f"Email отправлен для начисления #{assessment.id} на {email}")
            
        except Exception as e:
            result['error'] = str(e)
            result['message'] = f"Ошибка отправки: {str(e)}"
            logger.error(f"Ошибка отправки email для начисления #{assessment.id}: {e}")
        
        return result
    
    def send_receipt_to_all_emails(
        self, 
        assessment: Assessment,
        send_pdf_attachment: bool = False
    ) -> List[Dict[str, Any]]:
        """Отправляет квитанцию на все email владельца"""
        results = []
        emails = self.get_all_owner_emails(assessment)
        
        for email in emails:
            result = self.send_receipt_to_owner(
                assessment=assessment,
                recipient_email=email,
                send_pdf_attachment=send_pdf_attachment
            )
            results.append(result)
        
        return results
    
    def send_bulk_receipts(
        self,
        assessments: List[Assessment],
        send_pdf_attachment: bool = False,
        max_workers: int = 5,
        on_progress: callable = None
    ) -> Dict[str, Any]:
        """Массовая рассылка квитанций"""
        results = {
            'total': len(assessments),
            'sent': 0,
            'failed': 0,
            'skipped_no_email': 0,
            'details': [],
        }
        
        valid_assessments = []
        for assessment in assessments:
            if self.get_owner_email(assessment):
                valid_assessments.append(assessment)
            else:
                results['skipped_no_email'] += 1
                results['details'].append({
                    'assessment_id': assessment.id,
                    'owner_name': assessment.owner.full_name,
                    'status': 'skipped',
                    'message': 'Нет активного email',
                })
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self.send_receipt_to_owner, 
                    assessment, 
                    send_pdf_attachment=send_pdf_attachment
                ): assessment 
                for assessment in valid_assessments
            }
            
            for i, future in enumerate(as_completed(futures)):
                assessment = futures[future]
                try:
                    result = future.result()
                    if result['success']:
                        results['sent'] += 1
                    else:
                        results['failed'] += 1
                    results['details'].append(result)
                except Exception as e:
                    results['failed'] += 1
                    results['details'].append({
                        'assessment_id': assessment.id,
                        'owner_name': assessment.owner.full_name,
                        'status': 'error',
                        'message': str(e),
                    })
                
                if on_progress:
                    on_progress(i + 1, len(valid_assessments))
        
        return results


class BulkEmailSender:
    """Класс для массовой рассылки с прогрессом"""
    
    def __init__(self):
        self._email_service = None
        self.is_running = False
    
    @property
    def email_service(self):
        if self._email_service is None:
            self._email_service = EmailReceiptService()
        return self._email_service
    
    def send_to_owners_without_email(self, period_id=None, category_id=None):
        """Получение списка владельцев без email"""
        from .models import Assessment
        from users.models import Owner, ContactInfo
        
        assessments = Assessment.objects.filter(
            status__in=['pending', 'partial', 'overdue']
        )
        
        if period_id:
            assessments = assessments.filter(period_id=period_id)
        if category_id:
            assessments = assessments.filter(category_id=category_id)
        
        owners_without_email = []
        owner_ids_seen = set()
        
        for assessment in assessments:
            owner = assessment.owner
            if owner.id in owner_ids_seen:
                continue
            owner_ids_seen.add(owner.id)
            
            has_email = owner.contacts.filter(type='em', is_active=True).exists()
            if not has_email:
                owners_without_email.append({
                    'owner_id': owner.id,
                    'owner_name': owner.full_name,
                    'assessment_id': assessment.id,
                    'amount': str(assessment.amount),
                    'debt': str(assessment.debt),
                })
        
        return owners_without_email
    
    def send_to_debtors(
        self,
        category_id: int = None,
        period_id: int = None,
        min_debt: Decimal = Decimal('0'),
        send_pdf_attachment: bool = False,
        on_progress: callable = None,
        on_complete: callable = None
    ) -> Dict[str, Any]:
        """Отправка квитанций должникам"""
        from .models import Assessment
        
        assessments = Assessment.objects.filter(
            status__in=['pending', 'partial', 'overdue']
        ).select_related('owner', 'category', 'period')
        
        if category_id:
            assessments = assessments.filter(category_id=category_id)
        if period_id:
            assessments = assessments.filter(period_id=period_id)
        if min_debt > 0:
            assessments = assessments.filter(amount__gte=min_debt)
        
        assessments = assessments.order_by('-amount')
        
        self.is_running = True
        try:
            result = self.email_service.send_bulk_receipts(
                assessments=list(assessments),
                send_pdf_attachment=send_pdf_attachment,
                on_progress=on_progress
            )
            
            if on_complete:
                on_complete(result)
            
            return result
        finally:
            self.is_running = False
    
    def send_to_specific_owners(
        self,
        owner_ids: List[int],
        period_id: int = None,
        category_id: int = None,
        send_pdf_attachment: bool = False,
        on_progress: callable = None
    ) -> Dict[str, Any]:
        """Отправка квитанций конкретным владельцам"""
        from .models import Assessment
        
        assessments = Assessment.objects.filter(
            owner_id__in=owner_ids,
            status__in=['pending', 'partial', 'overdue']
        ).select_related('owner', 'category', 'period')
        
        if period_id:
            assessments = assessments.filter(period_id=period_id)
        if category_id:
            assessments = assessments.filter(category_id=category_id)
        
        return self.email_service.send_bulk_receipts(
            assessments=list(assessments),
            send_pdf_attachment=send_pdf_attachment,
            on_progress=on_progress
        )


# ВАЖНО: НЕ создаем глобальный экземпляр при импорте!
# email_sender = BulkEmailSender()  # ← ЗАКОММЕНТИРУЙТЕ ЭТУ СТРОКУ!

# Вместо этого используйте функцию-фабрику
def get_email_sender() -> BulkEmailSender:
    """Фабрика для создания отправителя email"""
    return BulkEmailSender()