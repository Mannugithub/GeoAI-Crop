import os
from google import genai
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

print("Listing all models:")
try:
    for m in client.models.list():
        print(f"Name: {m.name}, Supported Actions: {m.supported_actions}")
except Exception as e:
    print(f"Error: {e}")
