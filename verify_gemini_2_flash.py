import requests
import json
import sys

def test_gemini_2_0():
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {
        'Content-Type': 'application/json',
        'X-goog-api-key': '***GOOGLE_KEY_REDACTED***'
    }
    data = {
        "contents": [
            {
                "parts": [
                    {
                        "text": "Explain how AI works in a few words"
                    }
                ]
            }
        ]
    }

    print(f"POST {url}")
    try:
        response = requests.post(url, headers=headers, json=data)
        print(f"Status Code: {response.status_code}")
        print("Response Body:")
        print(response.text)
        
        if response.status_code == 200:
            print("\n[SUCCESS] API Key and Model are working.")
        else:
            print("\n[FAILURE] Request failed.")

    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    test_gemini_2_0()
