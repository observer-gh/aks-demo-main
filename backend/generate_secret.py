import secrets
import base64

secret_key = secrets.token_hex(32)
encoded_key = base64.b64encode(secret_key.encode()).decode()
print(encoded_key) 