"""调试 Kimi API 连接"""
import requests
import base64

API_KEY = "sk-kimi-5doecx7Y7h4klZME1MnOEDTRJUmzu8x58cdVeEu9RxCtSgxo06rANKSU4UIkqmUb"
URL = "https://api.kimi.com/coding/v1/chat/completions"

# 用一张小图测试
with open("./test_outputs/bg_p20_kimi.png", "rb") as f:
    b64 = base64.b64encode(f.read()).decode()

resp = requests.post(
    URL,
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    },
    json={
        "model": "kimi-k2.6",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "描述这张图片"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
                ]
            }
        ],
        "max_tokens": 100,
    },
    timeout=30,
)

print(f"Status: {resp.status_code}")
print(f"Response: {resp.text[:500]}")
