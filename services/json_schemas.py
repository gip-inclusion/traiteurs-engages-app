# VULN-25: validate JSON columns at the write boundary so a stuffed form
# can't bloat storage or surprise downstream readers.
from pydantic import BaseModel, ConfigDict


class ServiceConfig(BaseModel):
    # Keys mirror MealType; `extra="forbid"` blocks typos and tampering.
    model_config = ConfigDict(extra="forbid")

    petit_dejeuner: bool = False
    pause_gourmande: bool = False
    plateaux_repas: bool = False
    cocktail_dinatoire: bool = False
    cocktail_dejeunatoire: bool = False
    aperitif: bool = False
