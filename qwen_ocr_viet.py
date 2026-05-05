import base64
import sys
from openai import OpenAI
import instructor
from parse_model import ExportedData
def encode_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def ocr_vietnamese(image_path: str, api_url: str = "http://88.2.0.63:8000/v1"):
    client = OpenAI(api_key="EMPTY", base_url=api_url, timeout=120.0)
    instructor_client = instructor.from_openai(
        client, 
        mode=instructor.Mode.JSON # Use JSON mode for better compatibility
    )
    response = instructor_client.chat.completions.create(
        model="QuantTrio/Qwen3.5-35B-A3B-AWQ",
        response_model=ExportedData,
        extra_body={"enable_thinking": False},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": encode_image(image_path)},
                    },
                    {
                        "type": "text",
                        "text": (
                            "Hãy trích xuất toàn bộ văn bản tiếng Việt có trong ảnh này. "
                            "Giữ nguyên cấu trúc, xuống dòng, bảng biểu nếu có. "
                            "Chỉ trả về nội dung văn bản, không thêm giải thích."
                        ),
                    },
                ],
            }
        ],
        max_tokens=4096,
        temperature=0.1,
    )

    content = response
    return content


def _strip_thinking(text: str) -> str:
    import re
    return re.sub(r'<think>.*?<\/think>', '', text, flags=re.DOTALL).strip()


# if __name__ == "__main__":
#     if len(sys.argv) < 2:
#         print("Usage: python qwen_ocr_viet.py <image_path>")
#         sys.exit(1)

#     result = ocr_vietnamese(sys.argv[1])
#     print(result)
