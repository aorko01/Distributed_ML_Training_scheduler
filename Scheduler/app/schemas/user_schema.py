from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime
from pydantic.config import ConfigDict

class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str
    name: Optional[str] = None

class UserLogin(BaseModel):
    username: str
    password: str

class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None

class UserResponse(BaseModel):
    user_id: str
    username: str
    email: str
    name: Optional[str] = None
    is_active: bool
    is_superuser: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class TokenData(BaseModel):
    user_id: Optional[str] = None
    username: Optional[str] = None