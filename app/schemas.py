from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field, StringConstraints, field_validator
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

BIO_MIN_LENGTH = 20
BIO_MAX_LENGTH = 500
SINGLE_REASON_MIN_LENGTH = 10
SINGLE_REASON_MAX_LENGTH = 300

class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)
    quiz_answers: dict[str, str] = Field(default_factory=dict)
    gender: Gender
    interested_in: list[Gender] = Field(..., min_length=1)
    state: str
    bio: Annotated[str, StringConstraints(strip_whitespace=True, min_length=BIO_MIN_LENGTH, max_length=BIO_MAX_LENGTH)]
    single_reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=SINGLE_REASON_MIN_LENGTH, max_length=SINGLE_REASON_MAX_LENGTH)]

    @field_validator("state")
    @classmethod
    def validate_state(cls, value: str) -> str:
        if value not in STATE_OPTIONS:
            raise ValueError("Must be an Indian state or union territory, 'NRI', or 'Non-Indian'")
        return value

    def profile_fields(self) -> dict[str, Any]:
        """Profile answers, each stored in its own column on the users row."""
        return self.model_dump(exclude={"email", "password", "quiz_answers"})

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class SendOtpRequest(BaseModel):
    email: EmailStr

class VerifyOtpRequest(BaseModel):
    email: EmailStr
    otp_code: str = Field(..., min_length=6, max_length=6)

class UserResponse(BaseModel):
    id: UUID
    display_name: str
    avatar_seed: str
    status: str

    class Config:
        from_attributes = True

class AuthResponse(BaseModel):
    token: str
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

class AdminTokenResponse(BaseModel):
    token: str

class AdminUserResponse(BaseModel):
    id: UUID
    email: str
    display_name: str
    avatar_seed: str
    status: str
    room_id: UUID | None = None
    quiz_answers: dict[str, Any]
    # Null for email-OTP auto-signups and accounts created before signup collected them
    gender: str | None = None
    interested_in: list[str] | None = None
    state: str | None = None
    bio: str | None = None
    single_reason: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True

class AdminUsersListResponse(BaseModel):
    users: list[AdminUserResponse]
    total: int
    page: int

class MatchRequest(BaseModel):
    user_a_id: UUID
    user_b_id: UUID

class MatchResponse(BaseModel):
    room_id: UUID

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
