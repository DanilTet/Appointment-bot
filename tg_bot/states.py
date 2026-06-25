from aiogram.fsm.state import State, StatesGroup

class Appointment(StatesGroup):
    name = State()       # ПІБ пацієнта
    service = State()    # послуга
    anesthesia = State() # Анастезія
    doctor = State()     # лікар
    date = State()       # дата
    time = State()       # час
    phone = State()      # номер телефону