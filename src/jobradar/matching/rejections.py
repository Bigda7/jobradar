import re

from jobradar.domain.normalization import normalize_text
from jobradar.matching.models import MatchCandidate

VOLUNTEER_PATTERN = re.compile(r"(?<!\w)(?:волонтер\w*|волонтёр\w*|volunteer\w*)(?!\w)")
FREE_WORK_PATTERN = re.compile(r"(?<!\w)(?:бесплатн\w*|безкоштовн\w*|unpaid)(?!\w)")
VOLUNTEERING_BENEFIT_PATTERN = re.compile(
    r"(?<!\w)(?:"
    r"(?:paid\s+)?volunteer(?:ing)?\s+days?|"
    r"days?\s+off\s+for\s+volunteer(?:ing)?|"
    r"волонт[её]рск\w*\s+д(?:ень|ня)|"
    r"день\s+(?:для\s+)?волонт[её]рств\w*"
    r")(?!\w)"
)
MILITARY_EQUAL_OPPORTUNITY_PATTERN = re.compile(
    r"(?<!\w)(?:"
    r"military\s+or\s+veteran\s+status|military\s+status|protected\s+veteran|"
    r"veteran\s+of\s+the\s+armed\s+forces(?:\s+of\s+the\s+united\s+states)?"
    r")(?!\w)"
)
MILITARY_PATTERN = re.compile(
    r"(?<!\w)(?:"
    r"зсу|мобилизац\w*|мобілізац\w*|тцк|"
    r"сили\s+оборони|збройн\w*\s+сил\w*|військов\w*|"
    r"defen[cs]e\s+forces|armed\s+forces|military|miltech|deftech|"
    r"defen[cs]e[-\s]+(?:tech(?:nology|nologies)?|industry|sector|systems?)|"
    r"оборонн\w*|армі\w*|арми\w*"
    r")(?!\w)"
)


def hard_rejection_concern(candidate: MatchCandidate) -> str | None:
    title_and_company = normalize_text(
        " ".join(value for value in (candidate.title, candidate.company) if value)
    )
    description = normalize_text(candidate.description or "")
    description_without_benefits = VOLUNTEERING_BENEFIT_PATTERN.sub(" ", description)
    description_without_equal_opportunity = MILITARY_EQUAL_OPPORTUNITY_PATTERN.sub(" ", description)
    searchable_text = f"{title_and_company} {description}"
    if FREE_WORK_PATTERN.search(searchable_text) or VOLUNTEER_PATTERN.search(
        f"{title_and_company} {description_without_benefits}"
    ):
        return "Отклонено: волонтёрская или неоплачиваемая работа."
    if MILITARY_PATTERN.search(f"{title_and_company} {description_without_equal_opportunity}"):
        return "Отклонено: военный рекрутинг, мобилизация или служба в армии."
    return None
