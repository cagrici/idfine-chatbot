from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class UserDetailResponse(UserResponse):
    odoo_user_id: int | None = None
    updated_at: datetime | None = None


class UserListResponse(BaseModel):
    users: list[UserResponse]
    total: int


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    full_name: str = Field(min_length=1)
    role: str = "viewer"


class UpdateUserRequest(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = None
    is_active: bool | None = None


class ChangeRoleRequest(BaseModel):
    role: str


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=6)


class RoleResponse(BaseModel):
    id: str
    name: str
    display_name: str
    description: str | None = None
    permissions: dict = {}
    is_system: bool = False
    level: int = 0

    model_config = {"from_attributes": True}


class ActivityLogResponse(BaseModel):
    id: int
    user_id: str
    action: str
    target_type: str | None = None
    target_id: str | None = None
    details: dict | None = None
    ip_address: str | None = None
    created_at: datetime
    user_email: str | None = None
    user_full_name: str | None = None

    model_config = {"from_attributes": True}
