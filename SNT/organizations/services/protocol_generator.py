from django.utils import timezonetimezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from io import BytesIO

class ProtocolGenerator:
    """Генератор протокола собрания СНТ"""
    
    @staticmethod
    def generate_voting_protocol(voting_session):
        """Сгенерировать PDF протокола по ст. 17 217-ФЗ"""
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2*cm, bottomMargin=2*cm)
        styles = getSampleStyleSheet()
        
        # Русский шрифт
        pdfmetrics.registerFont(TTFont('DejaVu', 'DejaVuSans.ttf'))
        styles['Normal'].fontName = 'DejaVu'
        styles['Title'].fontName = 'DejaVu'
        
        story = []
        
        # Заголовок
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Title'],
            fontSize=16,
            alignment=1,  # center
            spaceAfter=20
        )
        story.append(Paragraph(f"ПРОТОКОЛ № {voting_session.protocol_number or 'б/н'}", title_style))
        story.append(Paragraph(f"общего собрания членов {voting_session.organization.short_name}", title_style))
        story.append(Spacer(1, 20))
        
        # Дата и место
        story.append(Paragraph(f"Дата проведения: {voting_session.protocol_date or voting_session.end_date.strftime('%d.%m.%Y')}", styles['Normal']))
        story.append(Paragraph(f"Место проведения: {voting_session.meeting_place or 'заочное голосование'}", styles['Normal']))
        story.append(Spacer(1, 20))
        
        # Повестка
        story.append(Paragraph("<b>Повестка дня:</b>", styles['Normal']))
        for i, question in enumerate(voting_session.questions.all(), 1):
            story.append(Paragraph(f"{i}. {question.title}", styles['Normal']))
            if question.description:
                story.append(Paragraph(f"<i>{question.description}</i>", styles['Normal']))
        
        story.append(Spacer(1, 20))
        
        # Результаты голосования
        story.append(Paragraph("<b>Результаты голосования:</b>", styles['Normal']))
        
        for question in voting_session.questions.all():
            story.append(Spacer(1, 10))
            story.append(Paragraph(f"<b>По вопросу {question.order + 1}: {question.title}</b>", styles['Normal']))
            
            # Таблица результатов
            data = [['Вариант', 'Голосов', 'Процент']]
            for option in question.options.all():
                data.append([
                    option.text,
                    str(option.votes_count),
                    f"{option.percentage:.2f}%"
                ])
            data.append(['Всего голосов', str(question.total_votes), '100%'])
            
            table = Table(data, colWidths=[250, 100, 100])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'DejaVu'),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ]))
            story.append(table)
        
        # Подписи
        story.append(Spacer(1, 30))
        story.append(Paragraph("<b>Председатель собрания:</b> ___________________", styles['Normal']))
        story.append(Paragraph("<b>Секретарь собрания:</b> ___________________", styles['Normal']))
        story.append(Paragraph(f"<i>Документ сформирован автоматически {timezone.now().strftime('%d.%m.%Y %H:%M')}</i>", styles['Normal']))
        
        doc.build(story)
        buffer.seek(0)
        return buffer