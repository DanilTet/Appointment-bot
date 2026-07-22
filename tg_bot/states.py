from aiogram.fsm.state import State, StatesGroup

class Appointment(StatesGroup):
    name = State()       # ПІБ пацієнта
    service = State()    # послуга
    anesthesia = State() # Анастезія
    doctor = State()     # лікар
    date = State()       # дата
    time = State()       # час
    phone = State()      # номер телефону

# --- ДЛЯ ОТЗЫВОВ ---
class Review(StatesGroup):
    text = State()       # Текст відгуку

# --- ДЛЯ РОЗСИЛОК ---
class BroadcastState(StatesGroup):
    waiting_for_content = State()  # Очікування тексту/фото розсилки
    waiting_for_button = State()   # Очікування назви та посилання кнопки (формат: Назва | посилання)
    waiting_for_time = State()     # Очікування дати та часу розсилки