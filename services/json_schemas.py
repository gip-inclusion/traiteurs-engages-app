from pydantic import BaseModel, ConfigDict


class ServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    petit_dejeuner: bool = False
    pause_gourmande: bool = False
    plateaux_repas: bool = False
    cocktail_dinatoire: bool = False
    cocktail_dejeunatoire: bool = False
    aperitif: bool = False
