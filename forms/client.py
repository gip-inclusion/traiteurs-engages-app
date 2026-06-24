from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    DecimalField,
    FloatField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    TextAreaField,
    TimeField,
)
from wtforms.validators import (
    Email,
    InputRequired,
    Length,
    NumberRange,
    Optional,
    ValidationError,
)

from models import MEAL_TYPE_LABELS

MEAL_TYPES = [(m.value, label) for m, label in MEAL_TYPE_LABELS.items()]


class QuoteRequestForm(FlaskForm):
    target_offerings: set | None = None

    company_service_id = StringField(validators=[Optional(), Length(max=36)])
    service_type = StringField(validators=[Optional(), Length(max=100)])
    meal_type = SelectField(choices=[("", "—")] + MEAL_TYPES, validators=[Optional()])
    event_date = DateField(format="%Y-%m-%d", validators=[Optional()])
    event_start_time = TimeField(format="%H:%M", validators=[Optional()])
    event_end_time = TimeField(format="%H:%M", validators=[Optional()])
    guest_count = IntegerField(validators=[Optional(), NumberRange(min=1, max=10000)])
    event_address = StringField(validators=[Optional(), Length(max=500)])
    event_city = StringField(validators=[Optional(), Length(max=255)])
    event_zip_code = StringField(validators=[Optional(), Length(max=10)])
    event_latitude = FloatField(validators=[Optional(), NumberRange(min=-90, max=90)])
    event_longitude = FloatField(
        validators=[Optional(), NumberRange(min=-180, max=180)]
    )
    budget_global = DecimalField(places=2, validators=[Optional(), NumberRange(min=0)])
    budget_per_person = DecimalField(
        places=2, validators=[Optional(), NumberRange(min=0)]
    )

    dietary_vegetarian = BooleanField()
    dietary_halal = BooleanField()
    dietary_gluten_free = BooleanField()
    dietary_lactose_free = BooleanField()

    vegetarian_count = IntegerField(
        validators=[Optional(), NumberRange(min=0, max=10000)]
    )
    halal_count = IntegerField(validators=[Optional(), NumberRange(min=0, max=10000)])
    gluten_free_count = IntegerField(
        validators=[Optional(), NumberRange(min=0, max=10000)]
    )
    lactose_free_count = IntegerField(
        validators=[Optional(), NumberRange(min=0, max=10000)]
    )

    drinks_details = TextAreaField(validators=[Optional(), Length(max=5000)])

    wants_waitstaff = BooleanField()
    service_waitstaff_details = TextAreaField(validators=[Optional(), Length(max=5000)])
    wants_equipment = BooleanField()
    wants_decoration = BooleanField()
    wants_nappes = BooleanField()
    wants_livraison = BooleanField()
    wants_setup = BooleanField()
    service_setup_time = TimeField(format="%H:%M", validators=[Optional()])
    service_setup_details = TextAreaField(validators=[Optional(), Length(max=5000)])
    wants_cleanup = BooleanField()

    is_compare_mode = BooleanField()
    message_to_caterer = TextAreaField(validators=[Optional(), Length(max=5000)])
    target_caterer_id = StringField(validators=[Optional(), Length(max=36)])

    def validate_meal_type(self, field):
        if self.target_offerings is None or not field.data:
            return
        if field.data not in self.target_offerings:
            raise ValidationError(
                "Le type de prestation choisi n'est pas propose par ce traiteur."
            )


class ServiceForm(FlaskForm):
    name = StringField(validators=[InputRequired(), Length(min=1, max=255)])
    description = TextAreaField(validators=[Optional(), Length(max=5000)])
    annual_budget = DecimalField(places=2, validators=[Optional(), NumberRange(min=0)])


class EmployeeForm(FlaskForm):
    first_name = StringField(validators=[InputRequired(), Length(min=1, max=255)])
    last_name = StringField(validators=[InputRequired(), Length(min=1, max=255)])
    email = StringField(validators=[InputRequired(), Email(), Length(max=255)])
    position = StringField(validators=[Optional(), Length(max=255)])
    service_id = StringField(validators=[Optional(), Length(max=36)])


class UserProfileForm(FlaskForm):
    first_name = StringField(validators=[Optional(), Length(max=255)])
    last_name = StringField(validators=[Optional(), Length(max=255)])
    email = StringField(validators=[Optional(), Length(max=255)])
    current_password = PasswordField(validators=[Optional()])


class CompanySettingsForm(FlaskForm):
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
    address = StringField(validators=[Optional(), Length(max=500)])
    city = StringField(validators=[Optional(), Length(max=255)])
    zip_code = StringField(validators=[Optional(), Length(max=10)])


class QuoteAcceptForm(FlaskForm):
    quote_id = StringField(validators=[InputRequired(), Length(max=36)])


class QuoteRefuseForm(FlaskForm):
    quote_id = StringField(validators=[InputRequired(), Length(max=36)])
    refusal_reason = TextAreaField(validators=[Optional(), Length(max=5000)])
