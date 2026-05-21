from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field
from typing import Any

# --- User Auth ---

class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)
    quiz_answers: dict[str, str] = Field(default_factory=dict)

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
