from pydantic import BaseModel, EmailStr
from typing import Optional

class UserCreate(BaseModel):
    name: str
    email: str

class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None

class UserRead(BaseModel):
    id: int
    name: str
    email: str
    
    class Config:
        from_attributes = True
