from datetime import date, datetime
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field, StringConstraints, computed_field, field_validator
from typing import Annotated, Any, Literal

# --- User Auth ---

# Signup profile options. Keep in sync with mvpfe/src/lib/profile.js
Gender = Literal["male", "female", "non_binary"]

INDIAN_STATES = (
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa",
    "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland",
    "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
    "Uttar Pradesh", "Uttarakhand", "West Bengal",
)
INDIAN_UNION_TERRITORIES = (
    "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu",
    "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
)
STATE_OPTIONS = INDIAN_STATES + INDIAN_UNION_TERRITORIES + ("NRI", "Non-Indian")

MIN_AGE = 18
MAX_AGE = 100
BIO_MIN_LENGTH = 20
BIO_MAX_LENGTH = 500
SINGLE_REASON_MIN_LENGTH = 10
SINGLE_REASON_MAX_LENGTH = 300

def age_from_date_of_birth(date_of_birth: date | str | None, today: date | None = None) -> int | None:
    """Whole years lived so far. Also takes the ISO date strings Supabase returns, and None for unknown."""
    if not date_of_birth:
        return None
    if isinstance(date_of_birth, str):
        date_of_birth = date.fromisoformat(date_of_birth)
    today = today or date.today()
    had_birthday_this_year = (today.month, today.day) >= (date_of_birth.month, date_of_birth.day)
    return today.year - date_of_birth.year - (0 if had_birthday_this_year else 1)

# The code emailed by /api/auth/send-otp. Spelled [0-9] because \d would also accept non-ASCII digits.
OtpCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{6}$")]

class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)
    otp_code: OtpCode
    quiz_answers: dict[str, str] = Field(default_factory=dict)
    date_of_birth: date
    gender: Gender
    interested_in: list[Gender] = Field(..., min_length=1)
    state: str
    bio: Annotated[str, StringConstraints(strip_whitespace=True, min_length=BIO_MIN_LENGTH, max_length=BIO_MAX_LENGTH)]
    single_reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=SINGLE_REASON_MIN_LENGTH, max_length=SINGLE_REASON_MAX_LENGTH)]

    @field_validator("date_of_birth")
    @classmethod
    def validate_age(cls, value: date) -> date:
        if not MIN_AGE <= age_from_date_of_birth(value) <= MAX_AGE:
            raise ValueError(f"Age must be between {MIN_AGE} and {MAX_AGE}")
        return value

    @field_validator("state")
    @classmethod
    def validate_state(cls, value: str) -> str:
        if value not in STATE_OPTIONS:
            raise ValueError("Must be an Indian state or union territory, 'NRI', or 'Non-Indian'")
        return value

    def profile_fields(self) -> dict[str, Any]:
        """Profile answers, each stored in its own column on the users row. JSON mode turns the date into an ISO string."""
        return self.model_dump(mode="json", exclude={"email", "password", "otp_code", "quiz_answers"})

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class SendOtpRequest(BaseModel):
    email: EmailStr

class VerifyOtpRequest(BaseModel):
    email: EmailStr
    otp_code: OtpCode

class UserResponse(BaseModel):
    id: UUID
    display_name: str
    avatar_seed: str
    status: str

    class Config:
        from_attributes = True

class AuthResponse(BaseModel):
    # The session itself travels in an httpOnly cookie, never in a response body
    user: UserResponse

# --- Profile and Chat ---

class StatusResponse(BaseModel):
    status: str
    room_id: UUID | None = None
    partner_display_name: str | None = None
    partner_avatar_seed: str | None = None

class MessageResponse(BaseModel):
    id: UUID
    sender_id: UUID
    content: str
    sent_at: datetime

    class Config:
        from_attributes = True

class MessagesListResponse(BaseModel):
    messages: list[MessageResponse]

# --- Admin Auth and Operations ---

class AdminLoginRequest(BaseModel):
    username: str
    password: str

class AdminSessionResponse(BaseModel):
    username: str

class AdminUserResponse(BaseModel):
    id: UUID
    email: str
    display_name: str
    avatar_seed: str
    status: str
    room_id: UUID | None = None
    quiz_answers: dict[str, Any]
    # Null for email-OTP auto-signups and accounts created before signup collected them
    date_of_birth: date | None = None
    gender: str | None = None
    interested_in: list[str] | None = None
    state: str | None = None
    bio: str | None = None
    single_reason: str | None = None
    created_at: datetime

    @computed_field
    @property
    def age(self) -> int | None:
        return age_from_date_of_birth(self.date_of_birth)

    class Config:
        from_attributes = True

class AdminUsersListResponse(BaseModel):
    users: list[AdminUserResponse]
    total: int
    page: int

class MatchRequest(BaseModel):
    user_a_id: UUID
    user_b_id: UUID
    notify_by_email: bool = False

# How the optional match emails went; "skipped" means the admin didn't ask for them
EmailStatus = Literal["sent", "partial", "failed", "not_configured", "skipped"]

class MatchResponse(BaseModel):
    room_id: UUID
    email_status: EmailStatus = "skipped"

class AdminEmailConfigResponse(BaseModel):
    enabled: bool

class AdminUserBrief(BaseModel):
    id: UUID
    display_name: str
    avatar_seed: str

    class Config:
        from_attributes = True

class AdminRoomResponse(BaseModel):
    id: UUID
    created_at: datetime
    is_active: bool
    user_a: AdminUserBrief
    user_b: AdminUserBrief

    class Config:
        from_attributes = True

class AdminRoomsListResponse(BaseModel):
    rooms: list[AdminRoomResponse]
