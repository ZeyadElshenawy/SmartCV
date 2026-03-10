import requests
from bs4 import BeautifulSoup
import sys

URL = "https://www.linkedin.com/jobs/view/3784158444" # Example URL (might be dead, but structure is valid)

# Use the same headers as the app
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

print(f"Testing scraper on: {URL}")

try:
    response = requests.get(URL, headers=headers, timeout=10)
    print(f"Status Code: {response.status_code}")
    
    if response.status_code == 200:
        soup = BeautifulSoup(response.content, 'html.parser')
        title = soup.find('h1', class_='top-card-layout__title') or soup.find('h1', class_='topcard__title')
        print(f"Title found: {title.text.strip() if title else 'None'}")
        
        description = soup.find('div', class_='description__text') or soup.find('div', class_='show-more-less-html__markup')
        desc_text = description.get_text(separator=' ', strip=True) if description else ""
        
        print(f"Description length: {len(desc_text)}")
        print("Description preview:", desc_text[:200])
        
        if "Sign in to apply" in desc_text or "Join LinkedIn" in desc_text:
            print("WARNING: It looks like we hit a login wall!")
    else:
        print("Request failed.")

except Exception as e:
    print(f"Scraper Error: {e}")
