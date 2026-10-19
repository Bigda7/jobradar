MATCH_EXPLANATIONS_RU = {
    "Candidate residence requirements could not be verified from the available metadata.": (
        "По доступным данным не удалось проверить требования к стране проживания кандидата."
    ),
    "Candidate residence restrictions are not verified yet; page metadata is pending.": (
        "Ограничения по стране проживания ещё не проверены: "
        "ожидаем дополнительные данные со страницы вакансии."
    ),
    "Candidate residence metadata may be stale after a vacancy update.": (
        "После обновления вакансии данные об ограничениях по стране проживания могли устареть."
    ),
    "The source does not specify permitted candidate residence; eligibility is uncertain.": (
        "Источник не указал допустимые страны проживания; "
        "пока неизвестно, можно ли откликаться из Чехии."
    ),
    "Office or hybrid work in Prague matches your location preference.": (
        "Офисный или гибридный формат в Праге соответствует вашим предпочтениям."
    ),
    "No demonstrated core stack match; tools or optional skills alone are insufficient.": (
        "Не найдено совпадений с вашим основным стеком; "
        "одних инструментов или необязательных навыков недостаточно."
    ),
    (
        "Stretch match: experience or mixed Junior/Middle level "
        "exceeds your demonstrated background."
    ): (
        "Вакансия на вырост: требуемый опыт или смешанный уровень Junior/Middle "
        "превышает ваш подтверждённый опыт."
    ),
    "The role requests about two years of experience; treat it as a stretch.": (
        "Требуется около двух лет опыта — рассматривайте вакансию как вариант на вырост."
    ),
    "The language of the advertisement alone does not establish a Czech requirement.": (
        "Сам по себе язык объявления не означает, что для работы требуется чешский."
    ),
    "Направление «Backend development» соответствует целевому профилю.": (
        "Направление «Бэкенд-разработка» соответствует целевому профилю."
    ),
    "Направление «API integration and automation» соответствует целевому профилю.": (
        "Направление «Интеграция API и автоматизация» соответствует целевому профилю."
    ),
}
OPTIONAL_TECHNOLOGY_PREFIX = "Optional technology outside your demonstrated stack: "


def localize_match_explanation(value: str) -> str:
    translated = MATCH_EXPLANATIONS_RU.get(value)
    if translated is not None:
        return translated
    if value.startswith(OPTIONAL_TECHNOLOGY_PREFIX):
        return "Необязательная технология вне вашего подтверждённого стека: " + value.removeprefix(
            OPTIONAL_TECHNOLOGY_PREFIX
        )
    return value
