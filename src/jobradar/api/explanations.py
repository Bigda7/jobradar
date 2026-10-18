"""English presentation of persisted matching explanations without rescoring."""

import re

_MESSAGES = {
    "Отклонено: военный рекрутинг, мобилизация или служба в армии.": (
        "Rejected: military recruiting, mobilization or military service."
    ),
    "Отклонено: в вакансии явно указана нулевая оплата.": (
        "Rejected: the vacancy explicitly offers no pay."
    ),
    "Удалённый формат соответствует обязательному требованию.": (
        "Remote work matches your required work mode."
    ),
    "Специализация вакансии не соответствует профилю разработчика.": (
        "The role does not match your developer profile."
    ),
    "Вакансия рассчитана на уровень Senior, Lead или Architect.": (
        "The role targets Senior, Lead or Architect level."
    ),
    "Вакансия явно рассчитана на Junior или начинающего специалиста.": (
        "The role explicitly targets Junior or entry-level candidates."
    ),
    "Не найдены ключевые слова целевого технологического стека.": (
        "No target stack keywords were found."
    ),
    "Полная занятость соответствует предпочтительному формату работы.": (
        "Full-time employment matches your preferred work arrangement."
    ),
    "Формат занятости подходит как альтернативный вариант для старта.": (
        "The employment arrangement is a possible alternative for starting out."
    ),
    (
        "Технология вне профиля указана как основная или обязательная; итоговый "
        "балл ограничен ниже порога уведомления."
    ): (
        "An unfamiliar technology is core or required; the score is capped below "
        "the main recommendation threshold."
    ),
    "Требуемый опыт не превышает одного года.": (
        "The required experience does not exceed one year."
    ),
    "Вакансия требует от одного до двух лет опыта.": (
        "The role requires between one and two years of experience."
    ),
    "Junior-вакансия требует около двух лет опыта.": (
        "The Junior role requires about two years of experience."
    ),
    "Требуемый опыт значительно превышает уровень Junior.": (
        "The required experience significantly exceeds Junior level."
    ),
    "Структурированный уровень вакансии рассчитан на Senior или выше.": (
        "The source's structured seniority is Senior or above."
    ),
    "Структурированный уровень вакансии указан как Middle.": (
        "The source's structured seniority is Middle."
    ),
    "Структурированный уровень вакансии подходит начинающему специалисту.": (
        "The source's structured seniority suits an entry-level candidate."
    ),
    "Указанная зарплата достигает предпочтительного месячного уровня.": (
        "The stated salary meets your preferred monthly level."
    ),
    "Указанная зарплата ниже предпочтительного месячного уровня.": (
        "The stated salary is below your preferred monthly level."
    ),
    "Ссылка отклика может вести на платную регистрацию или подписку.": (
        "The application link may lead to paid registration or a subscription."
    ),
    "Отклонено: волонтёрская или неоплачиваемая работа.": ("Rejected: volunteer or unpaid work."),
    "Отклонено: вакансия рассчитана на уровень Middle, Senior или выше.": (
        "Rejected: the role targets Middle, Senior or higher levels."
    ),
    "Отклонено: специализация вакансии не соответствует профилю разработчика.": (
        "Rejected: the role does not match your developer profile."
    ),
    "Отклонено: вакансия требует не менее трёх лет опыта.": (
        "Rejected: the role requires at least three years of experience."
    ),
    "Отклонено: удалённая работа доступна только кандидатам из США.": (
        "Rejected: remote work is restricted to US-based candidates."
    ),
    "Отклонено: предлагается работа за долю или идею без базовой зарплаты.": (
        "Rejected: equity-only work without a base salary."
    ),
    "В описании найден токсичный маркер ожиданий: rockstar, ninja или 10x engineer.": (
        "The description contains unrealistic expectation markers: rockstar, ninja or 10x engineer."
    ),
    "Зарплата за полную занятость ниже USD 400 в месяц.": (
        "The full-time salary is below USD 400 per month."
    ),
    "Отклонено: проект требует предоплату или депозит.": (
        "Rejected: the project requires an advance payment or deposit."
    ),
    "Отклонено: проект требует доступ к финансовому аккаунту или аккаунту платформы.": (
        "Rejected: the project requires access to a financial or platform account."
    ),
    "Отклонено: проект требует пароль, одноразовый код или приватный ключ.": (
        "Rejected: the project requests a password, one-time code or private key."
    ),
    "Отклонено: проект предлагает перенести общение или оплату за пределы платформы.": (
        "Rejected: the project requests off-platform communication or payment."
    ),
    "Отклонено: проект предлагает неоплачиваемую или незащищённую работу.": (
        "Rejected: the project offers unpaid or unprotected work."
    ),
    "Отклонено: проект не обозначен как удалённый.": (
        "Rejected: the project is not marked as remote."
    ),
    "Отклонено: основной язык проекта не поддерживается.": (
        "Rejected: the project's primary language is not supported."
    ),
    "Удалённый онлайн-проект соответствует обязательному требованию.": (
        "The remote online project matches your required work mode."
    ),
    "Источник показывает незалогиненному пользователю только публичный фрагмент описания.": (
        "The source shows only a public description excerpt to signed-out visitors."
    ),
    "Специализация проекта не соответствует профилю разработчика.": (
        "The project does not match your developer profile."
    ),
    "Проект выглядит сфокусированным и выполнимым по объёму.": (
        "The project scope appears focused and manageable."
    ),
    "Запрошенный объём слишком велик для первого фриланс-проекта.": (
        "The requested scope is too large for a first freelance project."
    ),
    "Срок выполнения выглядит необычно коротким.": ("The deadline appears unusually short."),
    "Проект отмечен как срочный.": ("The project is marked urgent."),
    "Проект рассчитан на специалиста уровня Senior или Lead.": (
        "The project targets Senior or Lead specialists."
    ),
    "Проект явно требует значительно большего опыта.": (
        "The project explicitly requires significantly more experience."
    ),
    "Бюджет проекта не удалось конвертировать в USD.": (
        "The project budget could not be converted to USD."
    ),
    "Заказчик подтвердил платёжные данные.": ("The client has verified payment details."),
    "Заказчик не подтвердил платёжные данные.": ("The client has not verified payment details."),
}

_VALUES = {
    "Full-stack разработка": "Full-stack development",
    "Front-end разработка": "Front-end development",
    "Веб-разработка": "Web development",
    "немецкий": "German",
    "чешский": "Czech",
    "французский": "French",
    "испанский": "Spanish",
    "польский": "Polish",
    "США": "the US",
    "Канадой": "Canada",
    "Латинской Америкой": "Latin America",
    "Бразилией": "Brazil",
    "Аргентиной": "Argentina",
    "Колумбией": "Colombia",
    "Украиной": "Ukraine",
    "неизвестная сумма": "unknown amount",
}

_PATTERNS = (
    (
        r"Отклонено: вакансия требует рабочее владение языком: (.+)\.",
        "Rejected: the role requires working proficiency in {0}.",
    ),
    (
        r"Направление «(.+)» соответствует целевому профилю\.",
        "The {0} role matches your target profile.",
    ),
    (r"Совпавшие навыки: (.+)\.", "Matched skills: {0}."),
    (
        r"Вакансия включает технологии вне профиля: (.+)\. Применён штраф (\d+) баллов\.",
        "The role includes technologies outside your profile: {0}. A {1}-point penalty applies.",
    ),
    (
        r"Требуемый уровень английского выше текущего уровня (.+)\.",
        "The required English level exceeds your current level ({0}).",
    ),
    (
        r"Описание преимущественно на чешском языке; текущий уровень — (.+)\.",
        "The description is mostly in Czech; your current level is {0}.",
    ),
    (
        r"Отклонено: удалённая работа явно ограничена регионом: (.+)\.",
        "Rejected: remote work is explicitly restricted to {0}.",
    ),
    (
        r"Базовая зарплата выше USD ([\d,]+) в месяц и может указывать "
        r"на скрытый уровень Middle или Senior\.",
        "The base salary exceeds USD {0} per month and may indicate an unstated "
        "Middle or Senior level.",
    ),
    (
        r"Проект включает технологии вне основного профиля: (.+)\.",
        "The project includes technologies outside your core profile: {0}.",
    ),
    (
        r"Почасовой бюджет достигает предпочтительного диапазона: (.+)\.",
        "The hourly budget reaches your preferred range: {0}.",
    ),
    (
        r"Почасовой бюджет находится в рабочем диапазоне: (.+)\.",
        "The hourly budget is within a workable range: {0}.",
    ),
    (
        r"Малый почасовой бюджет принят для набора репутации: (.+)\.",
        "A small hourly budget is accepted for building reputation: {0}.",
    ),
    (
        r"Малый фиксированный проект подходит для набора репутации: (.+), "
        r"платёж заказчика подтверждён\.",
        "A small fixed-price project may help build reputation: {0}; "
        "the client's payment is verified.",
    ),
    (
        r"Фиксированный бюджет достигает предпочтительного диапазона: (.+)\.",
        "The fixed-price budget reaches your preferred range: {0}.",
    ),
    (
        r"Фиксированный бюджет находится в рабочем диапазоне: (.+)\.",
        "The fixed-price budget is within a workable range: {0}.",
    ),
    (
        r"Малый фиксированный бюджет принят для набора репутации: (.+)\.",
        "A small fixed-price budget is accepted for building reputation: {0}.",
    ),
    (r"Низкая конкуренция: (\d+) ставок\.", "Low competition: {0} bids."),
    (r"Приемлемая конкуренция: (\d+) ставок\.", "Acceptable competition: {0} bids."),
    (r"Умеренная конкуренция: (\d+) ставок\.", "Moderate competition: {0} bids."),
    (r"Высокая конкуренция: (\d+) ставок\.", "High competition: {0} bids."),
    (r"Очень высокая конкуренция: (\d+) ставок\.", "Very high competition: {0} bids."),
    (r"Экстремально высокая конкуренция: (\d+) ставок\.", "Extremely high competition: {0} bids."),
    (
        r"Рейтинг заказчика (.+)/5 на основе (\d+) отзывов\.",
        "Client rating: {0}/5 from {1} reviews.",
    ),
    (r"Низкий рейтинг заказчика: (.+)/5\.", "Low client rating: {0}/5."),
)
_COMPILED_PATTERNS = tuple((re.compile(pattern), template) for pattern, template in _PATTERNS)


def english_explanation(value: str) -> str:
    if value in _MESSAGES:
        return _MESSAGES[value]
    for pattern, template in _COMPILED_PATTERNS:
        match = pattern.fullmatch(value)
        if match:
            return template.format(*(_VALUES.get(part, part) for part in match.groups()))
    return value
