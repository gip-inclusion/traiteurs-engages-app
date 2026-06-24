from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    IntegerField,
    SelectField,
    StringField,
    TextAreaField,
)
from wtforms.validators import Length, NumberRange, Optional

from models import CatererStructureType


class AdminCatererForm(FlaskForm):
    """Édition admin de l'identité + des coordonnées d'un traiteur.

    Distinct de CatererProfileForm (self-service) : couvre le SIRET et le
    type de structure que le traiteur ne peut pas modifier lui-même, et
    laisse de côté les prestations / tarifs / photos."""

    name = StringField(validators=[Optional(), Length(max=255)])
    siret = StringField(
        validators=[
            Optional(),
            Length(
                min=14,
                max=14,
                message="Le SIRET doit comporter exactement 14 caractères.",
            ),
        ]
    )
    structure_type = SelectField(
        choices=[(t.value, t.value) for t in CatererStructureType],
        validators=[Optional()],
    )
    address = StringField(validators=[Optional(), Length(max=500)])
    city = StringField(validators=[Optional(), Length(max=255)])
    zip_code = StringField(validators=[Optional(), Length(max=10)])
    description = TextAreaField(validators=[Optional(), Length(max=5000)])


class CatererProfileForm(FlaskForm):
    name = StringField(validators=[Optional(), Length(max=255)])
    description = TextAreaField(validators=[Optional(), Length(max=5000)])
    address = StringField(validators=[Optional(), Length(max=500)])
    city = StringField(validators=[Optional(), Length(max=255)])
    zip_code = StringField(validators=[Optional(), Length(max=10)])
    delivery_radius_km = IntegerField(
        validators=[Optional(), NumberRange(min=0, max=2000)]
    )
    dietary_vegetarian = BooleanField()
    dietary_bio = BooleanField()
    dietary_halal = BooleanField()
    dietary_gluten_free = BooleanField()
    dietary_lactose_free = BooleanField()
    service_config = TextAreaField(validators=[Optional(), Length(max=10000)])


class QuoteForm(FlaskForm):
    notes = TextAreaField(validators=[Optional(), Length(max=10000)])
    valid_until = DateField(format="%Y-%m-%d", validators=[Optional()])
    details = StringField(validators=[Optional(), Length(max=200000)])


class RejectionForm(FlaskForm):
    rejection_reason = TextAreaField(validators=[Optional(), Length(max=5000)])
