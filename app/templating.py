from pathlib import Path

from fastapi.templating import Jinja2Templates

from app import presentation
from app.config import get_settings

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

templates.env.globals["lthub_oauth_enabled"] = get_settings().lthub_oauth_enabled

templates.env.filters["status_label"] = presentation.achievement_status
templates.env.filters["completion_status_label"] = presentation.completion_status
templates.env.filters["report_reason_label"] = presentation.report_reason
templates.env.filters["report_status_label"] = presentation.report_status
templates.env.filters["proof_type_label"] = presentation.proof_type
templates.env.filters["decision_label"] = presentation.review_decision
templates.env.filters["ranking_status_label"] = presentation.ranking_status
templates.env.filters["role_label"] = presentation.role
templates.env.filters["datetime_local"] = presentation.datetime_local
templates.env.filters["date_local"] = presentation.date_local
templates.env.filters["plural_ru"] = presentation.plural_ru
