from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict

class JobCreate(BaseModel):
    script_path: str
    dataset_path: str
    config: Optional[Dict] = None
    
class JobResponse(BaseModel):
    id: str
    status: str

    model_config = ConfigDict(from_attributes=True)
