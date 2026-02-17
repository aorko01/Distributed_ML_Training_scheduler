from pydantic import BaseModel
from typing import Optional,Dict

class JobCreate(BaseModel):
    script_path: str
    dataset_path: str
    config: Optional[Dict] = None
    
class JobResponse(BaseModel):
    id: str
    status: str

    class Config:
        orm_mode = True
