
import os
import sys
import django
import pandas as pd
import time
import requests

# Setup Django
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

from jobs.services.linkedin_scraper import scrape_linkedin_job

# Test different URL formats
URLS = [
    "https://www.linkedin.com/jobs/view/4313714617/",
    "https://www.linkedin.com/jobs/view/4349858111/",
    "https://www.linkedin.com/jobs/view/4351664968/",
    "https://www.linkedin.com/jobs/view/4281944321/",
    "https://www.linkedin.com/jobs/view/4302023020/",
]

def run_benchmark():
    results = []
    print("🚀 Starting Web Scraper Benchmark...")
    print(f"Testing {len(URLS)} live URLs (Note: Likelihood of 429/Auth Wall is high)...\n")
    
    start_time_all = time.time()
    
    for i, url in enumerate(URLS):
        print(f"[{i+1}/{len(URLS)}] Scraping: {url}...")
        start_time = time.time()
        
        status = "Failed"
        title = None
        company = None
        desc_len = 0
        
        try:
            # Our service returns a dict or raises
            job_data = scrape_linkedin_job(url)
            duration = time.time() - start_time
            
            if job_data:
                status = "Success"
                title = job_data.get('title')
                company = job_data.get('company')
                desc_len = len(job_data.get('description', ''))
                
        except Exception as e:
            duration = time.time() - start_time
            print(f"  ❌ Error: {e}")
            status = "Error"

        results.append({
            'URL_Index': i+1,
            'Status': status,
            'Time_Sec': round(duration, 2),
            'Found_Title': bool(title),
            'Found_Company': bool(company),
            'Desc_Length': desc_len
        })

    print("\n" + "="*60)
    
    df = pd.DataFrame(results)
    print(df.to_string(index=False))
    
    success_rate = (len(df[df['Status'] == 'Success']) / len(df)) * 100
    avg_time = df['Time_Sec'].mean()
    
    print("\n" + "="*30)
    print("📊 SCRAPER BENCHMARK SUMMARY")
    print("="*30)
    print(f"✅ Success Rate: {success_rate:.1f}%")
    print(f"⚡ Average Latency: {avg_time:.2f} s")
    print("="*30)
    
    if success_rate < 50:
         print("⚠️ Note: Low success rate is expected for LinkedIn without rotating proxies.")

    df.to_csv('benchmark_scraper_results.csv', index=False)

if __name__ == "__main__":
    run_benchmark()
