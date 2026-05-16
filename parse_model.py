from pydantic import Field, BaseModel

class ExportedData(BaseModel):
    so_giay_phep: str = Field(..., description="Số giấy phép của văn bản")
    loai_giay_phep: str = Field(..., description="Loại giấy phép của văn bản")
    hieuluc: str = Field(..., description="Hiệu lực của văn bản. Nếu văn bản ghi 'có giá trị x năm kể từ ngày ký' thì tìm ngày ký trên văn bản và tính ra khoảng thời gian cụ thể theo định dạng 'DD/MM/YYYY - DD/MM/YYYY'. Ví dụ: 'Giấy chứng nhận này có giá trị ba năm kể từ ngày ký' và ngày ký là 15/03/2022 thì hieuluc = '15/03/2022 - 14/03/2025'")
    coso: str = Field(..., description="Cơ sở mà văn bản nhắc đến")
    qlcm: str = Field(..., description="Thông tin người quản lý chuyên môn")