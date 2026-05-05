from pydantic import Field, BaseModel

class ExportedData(BaseModel):
    so_giay_phep: str = Field(..., description="Số giấy phép của văn bản")
    loai_giay_phep: str = Field(..., description="Loại giấy phép của văn bản")
    hieuluc: str = Field(..., description="Hiệu lực của văn bản")
    coso: str = Field(..., description="Cơ sở mà văn bản nhắc đến")