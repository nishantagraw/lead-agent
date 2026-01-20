"""
Lead Agent v2.0
===============
Google Maps Lead Collection with Multi-Country Support
+ Google Sheets Sync to Avoid Duplicates
"""

import json
import os
import re
import time
import csv
import requests
import urllib3
from datetime import datetime
from threading import Thread
from flask import Flask, render_template_string, jsonify, request
from flask_cors import CORS

# Suppress SSL warnings when scraping websites
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSHEET_AVAILABLE = True
except:
    GSHEET_AVAILABLE = False
    print("⚠️ gspread not installed - Google Sheets sync disabled")

# ============ CONFIGURATION ============
GOOGLE_PLACES_API_KEY = "AIzaSyDAPVhtndrtZikFZaUVOcrdVfeFy-OhTUc"
LEADS_CSV = "leads.csv"
PROGRESS_FILE = "progress.json"
PROCESSED_FILE = "processed_businesses.json"
SERVICE_ACCOUNT_FILE = "service-account.json"
SHEET_URL = "https://docs.google.com/spreadsheets/d/1shxmg8RxltChh1ymUsqUlX0atbnrEDq__5PpQtTNfWo"

# ============ LOCATIONS & BUSINESS TYPES ============

# India - 25 Cities
INDIA_CITIES = [
    "Mumbai", "Delhi", "Bangalore", "Hyderabad", "Chennai", "Kolkata", "Pune", 
    "Ahmedabad", "Jaipur", "Lucknow", "Chandigarh", "Indore", "Bhopal", "Surat", 
    "Nagpur", "Kochi", "Coimbatore", "Vadodara", "Gurgaon", "Noida", "Thane", 
    "Visakhapatnam", "Mysore", "Nashik", "Rajkot"
]

# USA - 15 Cities
USA_CITIES = [
    "New York, NY", "Los Angeles, CA", "Chicago, IL", "Houston, TX", "Phoenix, AZ",
    "San Francisco, CA", "Miami, FL", "Seattle, WA", "Denver, CO", "Austin, TX",
    "Boston, MA", "Atlanta, GA", "San Diego, CA", "Dallas, TX", "Las Vegas, NV"
]

# UK - 10 Cities
UK_CITIES = [
    "London", "Manchester", "Birmingham", "Leeds", "Liverpool", 
    "Bristol", "Edinburgh", "Glasgow", "Cardiff", "Newcastle"
]

# Canada - 8 Cities
CANADA_CITIES = [
    "Toronto", "Vancouver", "Montreal", "Calgary", "Ottawa", 
    "Edmonton", "Winnipeg", "Halifax"
]

# Australia - 6 Cities
AUSTRALIA_CITIES = [
    "Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide", "Gold Coast"
]

# UAE - 4 Cities
UAE_CITIES = ["Dubai", "Abu Dhabi", "Sharjah", "Ajman"]

# Singapore
SINGAPORE_CITIES = ["Singapore"]

# Business Types
INDIA_BUSINESS_TYPES = [
    "restaurant", "gym", "salon", "spa", "photography studio", "boutique",
    "real estate agency", "hotel", "beauty parlor", "jewellery shop",
    "interior designer", "wedding planner", "coaching institute"
]

OTHER_BUSINESS_TYPES = [
    "restaurant", "gym", "salon", "spa", "photography studio", "boutique"
]

def generate_all_queries():
    """Generate all search queries"""
    queries = []
    
    # India queries (13 types x 25 cities = 325)
    for city in INDIA_CITIES:
        for btype in INDIA_BUSINESS_TYPES:
            queries.append(f"{btype} in {city}, India")
    
    # USA queries (6 types x 15 cities = 90)
    for city in USA_CITIES:
        for btype in OTHER_BUSINESS_TYPES:
            queries.append(f"{btype} in {city}, USA")
    
    # UK queries (6 types x 10 cities = 60)
    for city in UK_CITIES:
        for btype in OTHER_BUSINESS_TYPES:
            queries.append(f"{btype} in {city}, UK")
    
    # Canada queries (6 types x 8 cities = 48)
    for city in CANADA_CITIES:
        for btype in OTHER_BUSINESS_TYPES:
            queries.append(f"{btype} in {city}, Canada")
    
    # Australia queries (6 types x 6 cities = 36)
    for city in AUSTRALIA_CITIES:
        for btype in OTHER_BUSINESS_TYPES:
            queries.append(f"{btype} in {city}, Australia")
    
    # UAE queries (6 types x 4 cities = 24)
    for city in UAE_CITIES:
        for btype in OTHER_BUSINESS_TYPES:
            queries.append(f"{btype} in {city}, UAE")
    
    # Singapore queries (6 types x 1 city = 6)
    for city in SINGAPORE_CITIES:
        for btype in OTHER_BUSINESS_TYPES:
            queries.append(f"{btype} in {city}")
    
    return queries

ALL_QUERIES = generate_all_queries()
print(f"Total queries: {len(ALL_QUERIES)}")

# ============ STATUS ============
live_status = {
    "running": False,
    "paused": False,
    "current_action": "Ready to start",
    "current_query": "",
    "progress": 0,
    "total_queries": len(ALL_QUERIES),
    "leads_found": 0,
    "leads_with_email": 0,
    "processed_today": 0,
    "skipped_duplicates": 0,
    "recent_leads": [],  # Last 20 leads for live display
    "sheet_synced": False
}

processed_place_ids = set()
leads_data = []

def load_progress():
    """Load progress from file"""
    try:
        if os.path.exists(PROGRESS_FILE):
            with open(PROGRESS_FILE, 'r') as f:
                data = json.load(f)
                live_status["progress"] = data.get("index", 0)
    except:
        live_status["progress"] = 0

def save_progress(index):
    """Save progress to file"""
    with open(PROGRESS_FILE, 'w') as f:
        json.dump({"index": index, "date": datetime.now().isoformat()}, f)

def load_processed():
    """Load processed place IDs"""
    global processed_place_ids
    try:
        if os.path.exists(PROCESSED_FILE):
            with open(PROCESSED_FILE, 'r') as f:
                processed_place_ids = set(json.load(f))
    except:
        processed_place_ids = set()

def save_processed():
    """Save processed place IDs"""
    with open(PROCESSED_FILE, 'w') as f:
        json.dump(list(processed_place_ids), f)

def count_existing_leads():
    """Count existing leads in CSV"""
    try:
        if os.path.exists(LEADS_CSV):
            with open(LEADS_CSV, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                live_status["leads_found"] = len(rows)
                live_status["leads_with_email"] = len([r for r in rows if r.get("Email") and "@" in r.get("Email", "")])
    except:
        pass

load_progress()
load_processed()
count_existing_leads()

# ============ GOOGLE SHEETS SYNC (with LOCAL CACHE) ============
existing_business_names = set()
CACHE_FILE = "synced_cache.json"

def load_local_cache():
    """Load business names from local cache (instant!)"""
    global existing_business_names
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r') as f:
                data = json.load(f)
                existing_business_names = set(data.get("names", []))
                live_status["leads_found"] = data.get("total", 0)
                live_status["leads_with_email"] = data.get("with_email", 0)
                print(f"⚡ Loaded {len(existing_business_names)} businesses from local cache (instant!)")
                return len(existing_business_names)
    except:
        pass
    return 0

def save_local_cache():
    """Save business names to local cache"""
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump({
                "names": list(existing_business_names),
                "total": live_status["leads_found"],
                "with_email": live_status["leads_with_email"],
                "last_sync": datetime.now().isoformat()
            }, f)
    except:
        pass

def sync_from_google_sheet():
    """Download existing business names from Google Sheet to avoid duplicates"""
    global existing_business_names, processed_place_ids
    
    if not GSHEET_AVAILABLE:
        print("⚠️ Google Sheets sync not available")
        return 0
    
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print("⚠️ service-account.json not found - sync skipped")
        return 0
    
    try:
        print("📊 Syncing from Google Sheet... (this may take a minute)")
        creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
        )
        client = gspread.authorize(creds)
        sheet = client.open_by_url(SHEET_URL).sheet1
        
        # Get all data
        all_data = sheet.get_all_records()
        
        # Extract business names to avoid duplicates
        for row in all_data:
            name = row.get("Business Name", "") or row.get("name", "")
            if name:
                existing_business_names.add(name.lower().strip())
        
        live_status["leads_found"] = len(all_data)
        live_status["leads_with_email"] = len([r for r in all_data if r.get("Email") and "@" in str(r.get("Email", ""))])
        live_status["sheet_synced"] = True
        
        # Save to local cache for instant loading next time
        save_local_cache()
        
        print(f"✅ Synced {len(existing_business_names)} existing businesses from Google Sheet")
        print(f"📧 {live_status['leads_with_email']} leads have email")
        print(f"💾 Saved to local cache - next restart will be instant!")
        return len(existing_business_names)
    except Exception as e:
        print(f"❌ Error syncing from sheet: {e}")
        return 0

# Load from local cache first (instant!), only sync from sheet if no cache
cache_count = load_local_cache()
if cache_count == 0:
    print("📥 No local cache found, syncing from Google Sheet...")
    synced_count = sync_from_google_sheet()
else:
    synced_count = cache_count
    print("💡 Tip: Click 'Sync Sheet' button to update from Google Sheet")

# ============ GOOGLE PLACES API ============
def search_places(query):
    """Search Google Places API"""
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {"query": query, "key": GOOGLE_PLACES_API_KEY}
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        return data.get("results", [])
    except Exception as e:
        print(f"Error searching: {e}")
        return []

def get_place_details(place_id):
    """Get detailed info for a place"""
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "key": GOOGLE_PLACES_API_KEY,
        "fields": "name,formatted_address,formatted_phone_number,website,rating,user_ratings_total"
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        return data.get("result", {})
    except Exception as e:
        print(f"Error getting details: {e}")
        return {}

def extract_email_from_website(url):
    """Try to extract email from website"""
    if not url:
        return None
    try:
        live_status["current_action"] = f"📧 Extracting email from: {url[:40]}..."
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=3, verify=False)
        # Find emails
        emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', response.text[:50000])
        # Filter out common non-business emails
        bad_patterns = ['example', 'test', 'noreply', 'support@google', 'support@facebook', 'wix.com', 'godaddy']
        for email in emails[:5]:  # Limit to first 5 found
            if not any(bad in email.lower() for bad in bad_patterns):
                return email
    except requests.exceptions.Timeout:
        pass  # Silently skip timeouts
    except requests.exceptions.SSLError:
        pass  # Skip SSL errors
    except Exception as e:
        pass
    return None

def extract_instagram_from_website(url):
    """Try to extract Instagram handle from website"""
    if not url:
        return None
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=3, verify=False)
        # Find Instagram handles
        patterns = [
            r'instagram\.com/([a-zA-Z0-9_.]+)',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, response.text[:50000])
            for match in matches[:3]:  # Limit matches
                if len(match) > 2 and match not in ['instagram', 'facebook', 'twitter', 'p', 'reel']:
                    return f"@{match}"
    except:
        pass
    return None

def save_lead(lead):
    """Save lead to CSV and optionally to Google Sheet"""
    global leads_data, existing_business_names
    file_exists = os.path.exists(LEADS_CSV)
    fieldnames = ["Business Name", "Email", "Phone", "Website", "Instagram", "Location", "Rating", "Reviews", "Business Type", "Has_Website"]
    
    with open(LEADS_CSV, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(lead)
    
    # Add to existing names to prevent duplicates in same session
    name = lead.get("Business Name", "")
    if name:
        existing_business_names.add(name.lower().strip())
    
    leads_data.append(lead)
    live_status["leads_found"] += 1
    if lead.get("Email") and "@" in lead.get("Email", ""):
        live_status["leads_with_email"] += 1
    
    # Add to recent leads for live display (keep last 20)
    live_status["recent_leads"].insert(0, {
        "name": lead.get("Business Name", "Unknown"),
        "email": lead.get("Email", "-"),
        "phone": lead.get("Phone", "-"),
        "website": lead.get("Website", "-")[:30] + "..." if len(lead.get("Website", "")) > 30 else lead.get("Website", "-"),
        "instagram": lead.get("Instagram", "-"),
        "location": lead.get("Location", "")[:40],
        "rating": lead.get("Rating", 0),
        "type": lead.get("Business Type", ""),
        "has_website": lead.get("Has_Website", "FALSE"),
        "time": datetime.now().strftime("%H:%M:%S")
    })
    live_status["recent_leads"] = live_status["recent_leads"][:20]
    
    # Auto-sync to Google Sheet and update local cache every 10 leads
    if live_status["leads_found"] % 10 == 0:
        sync_lead_to_sheet(lead)
        save_local_cache()  # Update cache so new leads are remembered for duplicate prevention

def sync_lead_to_sheet(lead):
    """Sync a single lead to Google Sheet"""
    if not GSHEET_AVAILABLE:
        return
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        return
    try:
        creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        client = gspread.authorize(creds)
        sheet = client.open_by_url(SHEET_URL).sheet1
        
        # Append the lead as a new row
        row = [
            lead.get("Business Name", ""),
            lead.get("Email", ""),
            lead.get("Phone", ""),
            lead.get("Website", ""),
            lead.get("Instagram", ""),
            lead.get("Location", ""),
            lead.get("Rating", ""),
            lead.get("Reviews", ""),
            lead.get("Business Type", ""),
            lead.get("Has_Website", "")
        ]
        sheet.append_row(row)
        live_status["sheet_synced"] = True
    except Exception as e:
        print(f"Sheet sync error: {e}")


# ============ MAIN AGENT ============
def run_lead_agent():
    live_status["running"] = True
    live_status["current_action"] = "🚀 Starting lead collection..."
    
    start_index = live_status["progress"]
    
    for i, query in enumerate(ALL_QUERIES[start_index:], start=start_index):
        if not live_status["running"]:
            break
        
        while live_status["paused"]:
            live_status["current_action"] = "⏸️ Paused"
            time.sleep(1)
            if not live_status["running"]:
                break
        
        if not live_status["running"]:
            break
        
        live_status["current_query"] = query
        live_status["progress"] = i
        live_status["current_action"] = f"🔍 Searching: {query}"
        
        # Search places
        places = search_places(query)
        
        for place in places:
            if not live_status["running"]:
                break
            
            place_id = place.get("place_id")
            if not place_id or place_id in processed_place_ids:
                continue
            
            processed_place_ids.add(place_id)
            
            # Get details
            details = get_place_details(place_id)
            if not details:
                continue
            
            name = details.get("name", "Unknown")
            
            # Skip if business name already exists in Google Sheet
            if name.lower().strip() in existing_business_names:
                continue
            
            address = details.get("formatted_address", "")
            phone = details.get("formatted_phone_number", "")
            website = details.get("website", "")
            rating = details.get("rating", 0)
            reviews = details.get("user_ratings_total", 0)
            
            # Extract business type from query
            btype = query.split(" in ")[0] if " in " in query else "Unknown"
            
            # Try to get email and Instagram
            email = extract_email_from_website(website) if website else None
            instagram = extract_instagram_from_website(website) if website else None
            
            lead = {
                "Business Name": name,
                "Email": email or "-",
                "Phone": phone or "-",
                "Website": website or "-",
                "Instagram": instagram or "-",
                "Location": address,
                "Rating": rating,
                "Reviews": reviews,
                "Business Type": btype.title(),
                "Has_Website": "TRUE" if website else "FALSE"
            }
            
            save_lead(lead)
            live_status["processed_today"] += 1
            live_status["current_action"] = f"✅ Found: {name[:30]}"
            
            time.sleep(0.5)  # Rate limiting
        
        save_progress(i + 1)
        save_processed()
        time.sleep(2)  # Between queries
    
    if live_status["running"]:
        live_status["current_action"] = f"🎉 Complete! Found {live_status['leads_found']} leads"
    else:
        live_status["current_action"] = f"⏹️ Stopped at query {live_status['progress']}"
    
    live_status["running"] = False
    live_status["paused"] = False

# ============ FLASK APP ============
app = Flask(__name__)
CORS(app)

DASHBOARD_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>Lead Agent v3.0 - Live Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        :root { --bg: #0a0a0f; --bg2: #12121a; --card: #1a1a25; --accent: #00d4ff; --green: #00ff88; --pink: #ff4d8d; --orange: #ff9f43; --purple: #9c27b0; --text: #fff; --text2: #8e8e9a; --border: #2a2a3a; }
        body { font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
        .header { background: linear-gradient(135deg, rgba(0,212,255,0.1) 0%, rgba(0,255,136,0.05) 100%); padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); flex-wrap: wrap; gap: 12px; }
        .logo { font-size: 1.4rem; font-weight: 700; display: flex; align-items: center; gap: 10px; }
        .logo-text { background: linear-gradient(135deg, var(--accent), var(--green)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .controls { display: flex; gap: 8px; flex-wrap: wrap; }
        .btn { padding: 10px 16px; border: none; border-radius: 8px; font-weight: 600; cursor: pointer; font-size: 0.85rem; transition: all 0.3s; box-shadow: 0 2px 8px rgba(0,0,0,0.3); }
        .btn:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.4); }
        .btn-start { background: linear-gradient(135deg, #00ff88, #00d4ff); color: #000; }
        .btn-pause { background: linear-gradient(135deg, #ffc107, #ffdb4d); color: #000; }
        .btn-stop { background: linear-gradient(135deg, #ff4d8d, #ff6b9d); color: #fff; }
        .btn-sync { background: linear-gradient(135deg, #9c27b0, #ba68c8); color: #fff; }
        .btn-sheet { background: linear-gradient(135deg, #4caf50, #81c784); color: #fff; }
        .btn-reset { background: var(--card); border: 1px solid var(--border); color: var(--text); }
        .status-bar { background: var(--bg2); padding: 16px 24px; border-bottom: 1px solid var(--border); }
        .status-row { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }
        .action-box { display: flex; align-items: center; gap: 12px; }
        .spinner { width: 20px; height: 20px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 1s linear infinite; display: none; }
        .spinner.active { display: block; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .action-text { color: var(--accent); font-weight: 600; font-size: 1rem; }
        .progress-section { margin-top: 12px; }
        .progress-bar { width: 100%; height: 8px; background: var(--card); border-radius: 4px; overflow: hidden; }
        .progress-fill { height: 100%; background: linear-gradient(90deg, var(--accent), var(--green)); transition: width 0.3s; box-shadow: 0 0 10px var(--accent); }
        .progress-info { display: flex; justify-content: space-between; margin-top: 8px; font-size: 0.8rem; color: var(--text2); }
        .stats-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; padding: 16px 24px; }
        .stat-card { background: linear-gradient(135deg, var(--card) 0%, rgba(26,26,37,0.8) 100%); border: 1px solid var(--border); border-radius: 12px; padding: 20px; text-align: center; transition: all 0.3s; }
        .stat-card:hover { transform: translateY(-2px); border-color: var(--accent); }
        .stat-value { font-size: 2rem; font-weight: 700; }
        .stat-label { color: var(--text2); font-size: 0.8rem; margin-top: 6px; }
        .main-content { padding: 0 24px 24px; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 20px; margin-top: 16px; }
        .card-title { font-size: 1rem; font-weight: 600; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; }
        .leads-table { width: 100%; border-collapse: collapse; font-size: 0.75rem; }
        .leads-table th { text-align: left; padding: 10px 8px; color: var(--accent); border-bottom: 2px solid var(--border); font-weight: 600; text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.5px; background: var(--bg2); position: sticky; top: 0; }
        .leads-table td { padding: 10px 8px; border-bottom: 1px solid var(--border); }
        .leads-table tr:hover { background: rgba(0,212,255,0.05); }
        .leads-table tr:nth-child(even) { background: rgba(0,0,0,0.2); }
        .email-found { color: var(--green); font-weight: 500; }
        .no-email { color: var(--text2); }
        .has-site { color: var(--accent); }
        .no-site { color: var(--orange); }
        .insta { color: var(--pink); }
        .rating { color: #ffc107; }
        .badge { display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 0.65rem; font-weight: 600; }
        .badge-email { background: rgba(0,255,136,0.15); color: var(--green); }
        .badge-nomail { background: rgba(142,142,154,0.15); color: var(--text2); }
        .badge-site { background: rgba(0,212,255,0.15); color: var(--accent); }
        .badge-nosite { background: rgba(255,159,67,0.15); color: var(--orange); }
        .country-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 8px; margin-top: 12px; }
        .country-card { background: var(--bg2); padding: 12px; border-radius: 8px; text-align: center; }
        .country-flag { font-size: 1.5rem; }
        .country-name { font-size: 0.75rem; color: var(--text2); margin-top: 4px; }
        .country-count { font-size: 1.1rem; font-weight: 700; color: var(--accent); }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.6; } }
        .running { animation: pulse 1.5s infinite; }
        .synced { color: var(--green); }
        .not-synced { color: var(--orange); }
        .table-scroll { max-height: 450px; overflow-y: auto; overflow-x: auto; }
    </style>
</head>
<body>
    <div class="header">
        <div class="logo"><span style="font-size:1.8rem;">🎯</span><span class="logo-text">Lead Agent v3.0</span></div>
        <div class="controls">
            <button class="btn btn-start" onclick="fetch('/api/start').then(refresh)">▶ Start</button>
            <button class="btn btn-pause" onclick="fetch('/api/pause').then(refresh)">⏸ Pause</button>
            <button class="btn btn-stop" onclick="fetch('/api/stop').then(refresh)">⏹ Stop</button>
            <button class="btn btn-sync" onclick="fetch('/api/sync').then(r=>r.json()).then(d=>alert('Synced: '+d.existing_businesses+' businesses'))">🔄 Sync</button>
            <button class="btn btn-sheet" onclick="window.open('https://docs.google.com/spreadsheets/d/1shxmg8RxltChh1ymUsqUlX0atbnrEDq__5PpQtTNfWo','_blank')">📊 Sheet</button>
            <button class="btn btn-reset" onclick="if(confirm('Reset progress?')) fetch('/api/reset').then(refresh)">↩ Reset</button>
        </div>
    </div>
    <div class="status-bar">
        <div class="status-row">
            <div class="action-box">
                <div class="spinner" id="spinner"></div>
                <div class="action-text" id="action">Ready to start</div>
            </div>
            <div style="font-size:0.85rem;"><span id="sync-status" class="not-synced">●</span> Cache: <span id="existing">0</span> businesses</div>
        </div>
        <div class="progress-section">
            <div class="progress-bar"><div class="progress-fill" id="progress-fill" style="width:0%"></div></div>
            <div class="progress-info">
                <span>Query <strong id="progress-text">0</strong> / ''' + str(len(ALL_QUERIES)) + '''</span>
                <span id="query">-</span>
                <span id="progress-pct">0%</span>
            </div>
        </div>
    </div>
    <div class="stats-row">
        <div class="stat-card"><div class="stat-value" id="total-leads" style="color:var(--accent)">0</div><div class="stat-label">📊 Total Leads</div></div>
        <div class="stat-card"><div class="stat-value" id="leads-email" style="color:var(--green)">0</div><div class="stat-label">✉️ With Email</div></div>
        <div class="stat-card"><div class="stat-value" id="processed-today" style="color:var(--pink)">0</div><div class="stat-label">🆕 New Today</div></div>
        <div class="stat-card"><div class="stat-value" id="skipped" style="color:var(--orange)">0</div><div class="stat-label">⏭️ Skipped</div></div>
    </div>
    <div class="main-content">
        <div class="card">
            <div class="card-title"><span>📋 Live Leads Feed</span><span style="font-size:0.75rem;color:var(--text2);">🔴 LIVE - Updates every 2s</span></div>
            <div class="table-scroll">
                <table class="leads-table">
                    <thead><tr><th>Time</th><th>Business Name</th><th>Email</th><th>Phone</th><th>Instagram</th><th>Location</th><th>Type</th><th>⭐</th><th>Site</th></tr></thead>
                    <tbody id="leads-body"><tr><td colspan="9" style="text-align:center;color:var(--text2);padding:40px;">🎯 Click <strong>Start</strong> to begin collecting leads!</td></tr></tbody>
                </table>
            </div>
        </div>
        <div class="card">
            <div class="card-title">🌍 Global Coverage (''' + str(len(ALL_QUERIES)) + ''' Queries)</div>
            <div class="country-grid">
                <div class="country-card"><div class="country-flag">🇮🇳</div><div class="country-count">325</div><div class="country-name">India</div></div>
                <div class="country-card"><div class="country-flag">🇺🇸</div><div class="country-count">90</div><div class="country-name">USA</div></div>
                <div class="country-card"><div class="country-flag">🇬🇧</div><div class="country-count">60</div><div class="country-name">UK</div></div>
                <div class="country-card"><div class="country-flag">🇨🇦</div><div class="country-count">48</div><div class="country-name">Canada</div></div>
                <div class="country-card"><div class="country-flag">🇦🇺</div><div class="country-count">36</div><div class="country-name">Australia</div></div>
                <div class="country-card"><div class="country-flag">🇦🇪</div><div class="country-count">24</div><div class="country-name">UAE</div></div>
                <div class="country-card"><div class="country-flag">🇸🇬</div><div class="country-count">6</div><div class="country-name">Singapore</div></div>
            </div>
        </div>
    </div>
    <script>
        function refresh() { fetchStatus(); }
        function fetchStatus() {
            fetch('/api/status').then(r => r.json()).then(data => {
                document.getElementById('action').textContent = data.current_action;
                document.getElementById('action').className = data.running ? 'action-text running' : 'action-text';
                document.getElementById('spinner').className = data.running ? 'spinner active' : 'spinner';
                document.getElementById('query').textContent = data.current_query ? data.current_query.substring(0,50) : '-';
                document.getElementById('total-leads').textContent = data.leads_found.toLocaleString();
                document.getElementById('leads-email').textContent = data.leads_with_email.toLocaleString();
                document.getElementById('processed-today').textContent = data.processed_today;
                document.getElementById('skipped').textContent = data.skipped_duplicates || 0;
                document.getElementById('progress-text').textContent = data.progress;
                document.getElementById('sync-status').textContent = data.sheet_synced ? '🟢' : '🟡';
                document.getElementById('sync-status').className = data.sheet_synced ? 'synced' : 'not-synced';
                const pct = Math.round((data.progress / data.total_queries) * 100);
                document.getElementById('progress-fill').style.width = pct + '%';
                document.getElementById('progress-pct').textContent = pct + '%';
                if (data.recent_leads && data.recent_leads.length > 0) {
                    let html = '';
                    data.recent_leads.forEach(l => {
                        const emailBadge = l.email && l.email !== '-' && l.email.includes('@') ? '<span class="badge badge-email">✓</span> ' + l.email : '<span class="badge badge-nomail">—</span>';
                        const siteBadge = l.has_website === 'TRUE' ? '<span class="badge badge-site">YES</span>' : '<span class="badge badge-nosite">NO</span>';
                        const insta = l.instagram && l.instagram !== '-' ? '<span class="insta">' + l.instagram + '</span>' : '-';
                        const rating = l.rating ? '<span class="rating">★ ' + l.rating + '</span>' : '-';
                        html += '<tr><td style="white-space:nowrap;">' + l.time + '</td><td><strong>' + l.name + '</strong></td><td style="font-size:0.7rem;">' + emailBadge + '</td><td style="font-size:0.7rem;">' + (l.phone || '-') + '</td><td>' + insta + '</td><td style="font-size:0.7rem;max-width:150px;overflow:hidden;text-overflow:ellipsis;">' + (l.location || '-') + '</td><td>' + (l.type || '-') + '</td><td>' + rating + '</td><td>' + siteBadge + '</td></tr>';
                    });
                    document.getElementById('leads-body').innerHTML = html;
                }
            });
        }
        setInterval(fetchStatus, 2000);
        fetchStatus();
        fetch('/api/existing-count').then(r => r.json()).then(d => {
            document.getElementById('existing').textContent = d.count ? d.count.toLocaleString() : 0;
        });
    </script>
</body>
</html>
'''

@app.route('/')
def dashboard():
    return render_template_string(DASHBOARD_HTML)

@app.route('/api/status')
def get_status():
    return jsonify(live_status)

@app.route('/api/start')
def start_agent():
    if not live_status["running"]:
        Thread(target=run_lead_agent, daemon=True).start()
    return jsonify({"status": "started"})

@app.route('/api/stop')
def stop_agent():
    live_status["running"] = False
    return jsonify({"status": "stopped"})

@app.route('/api/pause')
def pause_agent():
    live_status["paused"] = not live_status["paused"]
    return jsonify({"paused": live_status["paused"]})

@app.route('/api/reset')
def reset_agent():
    live_status["progress"] = 0
    live_status["processed_today"] = 0
    save_progress(0)
    return jsonify({"status": "reset"})

@app.route('/api/sync')
def sync_sheet():
    count = sync_from_google_sheet()
    return jsonify({"status": "synced", "existing_businesses": count})

@app.route('/api/existing-count')
def existing_count():
    return jsonify({"count": len(existing_business_names)})

if __name__ == '__main__':
    print("=" * 50)
    print("🎯 Lead Agent v3.0 - Live Dashboard")
    print("=" * 50)
    print(f"📊 Total queries: {len(ALL_QUERIES)}")
    print(f"📈 Current progress: {live_status['progress']}/{len(ALL_QUERIES)}")
    print(f"📧 Leads found: {live_status['leads_found']}")
    print(f"✉️ Leads with email: {live_status['leads_with_email']}")
    print(f"🔄 Existing businesses (no duplicates): {len(existing_business_names)}")
    print("🌐 Dashboard: http://127.0.0.1:5000")
    print("=" * 50)
    app.run(host='127.0.0.1', port=5000, debug=False)


