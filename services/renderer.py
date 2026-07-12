import os
import io
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

# Попробуем найти шрифт Arial в системе, если нет — используем дефолтный шрифт
FONT_PATH = "C:\\Windows\\Fonts\\arial.ttf"
if not os.path.exists(FONT_PATH):
    # Фолбек на случай, если запуск идет не на Windows
    FONT_PATH = "arial.ttf"

def generate_schedule_image(date_str, appointments):
    """
    Генерирует изображение расписания на основе списка приемов.
    Возвращает байтовый поток (BytesIO) с PNG-изображением.
    """
    # Размеры изображения
    # Ширина фиксированная, высота зависит от количества приемов
    width = 750
    header_height = 100
    card_height = 130
    card_gap = 15
    padding = 25
    footer_height = 60
    
    num_appts = len(appointments)
    if num_appts == 0:
        height = 300
    else:
        height = header_height + (card_height + card_gap) * num_appts + padding + footer_height

    # Цветовая палитра
    bg_color = (15, 23, 42)         # Slate 900
    card_bg_color = (30, 41, 59)    # Slate 800
    text_main_color = (248, 250, 252) # Slate 50
    text_muted_color = (148, 163, 184) # Slate 400
    accent_blue = (14, 118, 168)    # Accent Teal/Blue
    anesthesia_alert_bg = (244, 63, 94) # Rose 500 (яркий красный)
    anesthesia_alert_fg = (255, 255, 255)
    doctor_bg = (51, 65, 85)        # Slate 700
    doctor_fg = (226, 232, 240)
    
    # Создаем холст
    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    # Инициализация шрифтов
    try:
        font_title = ImageFont.truetype(FONT_PATH, 28)
        font_sub = ImageFont.truetype(FONT_PATH, 16)
        font_time = ImageFont.truetype(FONT_PATH, 24)
        font_name = ImageFont.truetype(FONT_PATH, 20)
        font_service = ImageFont.truetype(FONT_PATH, 16)
        font_tag = ImageFont.truetype(FONT_PATH, 12)
    except IOError:
        # Если шрифты не загрузились, Pillow будет использовать ImageFont.load_default()
        font_title = ImageFont.load_default()
        font_sub = ImageFont.load_default()
        font_time = ImageFont.load_default()
        font_name = ImageFont.load_default()
        font_service = ImageFont.load_default()
        font_tag = ImageFont.load_default()

    # 1. Отрисовка шапки
    # Декоративная полоса сверху
    draw.rectangle([0, 0, width, 8], fill=accent_blue)
    
    # Заголовок
    draw.text((padding, 25), "РОЗКЛАД ПРИЙОМІВ", font=font_title, fill=text_main_color)
    draw.text((padding, 63), f"Дата: {date_str}  •  Всього записів: {num_appts}", font=font_sub, fill=text_muted_color)
    
    # Разделитель шапки
    draw.line([padding, 90, width - padding, 90], fill=(51, 65, 85), width=1)

    # 2. Отрисовка списка приемов
    y_offset = header_height + card_gap
    
    if num_appts == 0:
        # Если записей нет
        draw.rounded_rectangle(
            [padding, y_offset, width - padding, y_offset + 120],
            radius=8,
            fill=card_bg_color
        )
        draw.text(
            (width // 2 - 120, y_offset + 45),
            "Сьогодні прийомів немає. День вільний!",
            font=font_name,
            fill=text_muted_color
        )
    else:
        for i, appt in enumerate(appointments):
            # Координаты карточки
            x0, y0 = padding, y_offset
            x1, y1 = width - padding, y_offset + card_height
            
            # Определяем, нужен ли наркоз
            has_anesthesia = False
            anesthesia_text = appt.get("anesthesia", "").strip()
            if anesthesia_text and "Наркоз" in anesthesia_text:
                has_anesthesia = True
                
            # Рисуем подложку карточки
            draw.rounded_rectangle([x0, y0, x1, y1], radius=8, fill=card_bg_color)
            
            # Левая цветная полоса на карточке для выделения приемов
            strip_color = anesthesia_alert_bg if has_anesthesia else accent_blue
            draw.rounded_rectangle([x0, y0, x0 + 8, y1], radius=8, fill=strip_color)
            # Закрасим правые углы полосы, чтобы она была ровной слева
            draw.rectangle([x0 + 4, y0, x0 + 8, y1], fill=strip_color)

            # Время приема
            time_str = appt.get("time", "--:--")
            draw.text((x0 + 25, y0 + 22), time_str, font=font_time, fill=text_main_color)
            
            # Детали пациента (Имя и телефон)
            patient_str = appt.get("patient", "Невідомий пацієнт")
            draw.text((x0 + 130, y0 + 22), patient_str, font=font_name, fill=text_main_color)

            # Услуга / Исследование
            service_str = f"🩺 {appt.get('service', 'Консультація')}"
            draw.text((x0 + 130, y0 + 58), service_str, font=font_service, fill=text_muted_color)
            
            # Врач
            doctor_str = appt.get("doctor", "Тетернік")
            doc_label = f"👨‍⚕️ {doctor_str}"
            
            # Добавим плашку врача
            doc_len = len(doc_label) * 8 + 20
            doc_x0 = x0 + 130
            doc_y0 = y0 + 90
            draw.rounded_rectangle([doc_x0, doc_y0, doc_x0 + doc_len, doc_y0 + 22], radius=4, fill=doctor_bg)
            draw.text((doc_x0 + 8, doc_y0 + 2), doc_label, font=font_tag, fill=doctor_fg)

            # Если есть наркоз, добавим яркую плашку
            if has_anesthesia:
                tag_x0 = doc_x0 + doc_len + 12
                tag_y0 = doc_y0
                tag_text = f"💉 {anesthesia_text}"
                tag_len = len(tag_text) * 8 + 20
                draw.rounded_rectangle([tag_x0, tag_y0, tag_x0 + tag_len, tag_y0 + 22], radius=4, fill=anesthesia_alert_bg)
                draw.text((tag_x0 + 8, tag_y0 + 2), tag_text, font=font_tag, fill=anesthesia_alert_fg)
            else:
                # Если анестезия указана, но без наркоза (местная/без нее)
                if anesthesia_text:
                    tag_x0 = doc_x0 + doc_len + 12
                    tag_y0 = doc_y0
                    tag_text = f"🧬 {anesthesia_text}"
                    tag_len = len(tag_text) * 8 + 20
                    draw.rounded_rectangle([tag_x0, tag_y0, tag_x0 + tag_len, tag_y0 + 22], radius=4, fill=(71, 85, 105))
                    draw.text((tag_x0 + 8, tag_y0 + 2), tag_text, font=font_tag, fill=text_main_color)

            # Смещение для следующей карточки
            y_offset += card_height + card_gap

    # 3. Отрисовка подвала
    draw.line([padding, height - footer_height, width - padding, height - footer_height], fill=(51, 65, 85), width=1)
    
    current_time_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    draw.text(
        (padding, height - 35),
        f"Згенеровано ботом автоматичного запису • {current_time_str}",
        font=font_sub,
        fill=text_muted_color
    )

    # Сохраняем в байты
    bio = io.BytesIO()
    img.save(bio, "PNG")
    bio.seek(0)
    return bio
