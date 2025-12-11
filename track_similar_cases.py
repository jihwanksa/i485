#!/usr/bin/env python3
"""
Track similar cases and collect only 3 key status types:
1. First chronological status (labeled as case_received, regardless of exact wording)
2. Interview cancelled status 
3. Last/most recent status (if different from interview_cancelled)
Maintains filtered history in one CSV file with status_type classification.

Updated to use MyCasesHub.com instead of the old CaseStatusExt.com
Uses Selenium for JavaScript-rendered content.
"""

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import pandas as pd
from datetime import datetime
import time
import os
import re

def setup_driver():
    """Set up Chrome driver in headless mode"""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def parse_date(date_text):
    """Parse date from various formats to YYYY-MM-DD"""
    date_text = date_text.strip()
    
    # Try different date formats
    formats = [
        "%b %d, %Y",      # Mar 17, 2025
        "%B %d, %Y",      # March 17, 2025
        "%Y-%m-%d",       # 2025-03-17
        "%m/%d/%Y",       # 03/17/2025
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    
    return date_text  # Return as-is if no format matches

def get_case_timeline(driver, receipt_number):
    """Get case timeline from MyCasesHub.com"""
    url = f"https://mycaseshub.com/analysis/{receipt_number}"
    
    try:
        driver.get(url)
        
        # Wait for page to load (wait for case number to appear)
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, f"//*[contains(text(), '{receipt_number}')]"))
        )
        
        # Give additional time for Vue.js to fully render
        time.sleep(2)
        
        timeline_entries = []
        
        # Get the page source after JavaScript rendering
        page_text = driver.find_element(By.TAG_NAME, "body").text
        
        # Status keywords to look for
        status_keywords = [
            'Interview Cancelled',
            'Interview Canceled', 
            'Interview Was Scheduled',
            'Card Was Delivered',
            'Card Was Produced',
            'Card Is Being Produced',
            'Case Was Approved',
            'Case Was Updated',
            'Request for Evidence',
            'Request For Initial Evidence Was Sent',
            'New Card Is Being Produced',
            'Case Was Received',
            'Case Was Received and A Receipt Notice Was Sent',
            'Fingerprint Fee Was Received',
            'Case Was Transferred',
            'Case Is Being Actively Reviewed By USCIS',
            'Biometrics Appointment Was Scheduled',
        ]
        
        # Look for "FILED DATE" pattern (uppercase in actual page)
        # Pattern: FILED DATE followed by date on next line or same line
        filed_date_match = re.search(r'FILED\s*DATE\s*\n?\s*([A-Za-z]+\s+\d+,?\s*\d{4})', page_text, re.IGNORECASE)
        if filed_date_match:
            filed_date = parse_date(filed_date_match.group(1))
            timeline_entries.append({
                'date': filed_date,
                'status': 'Case Was Received'
            })
        
        # Look for HISTORY section and extract ALL date-status pairs
        # The HISTORY section format is:
        # HISTORY
        # DATE1
        # STATUS1
        # DATE2 (optional)
        # STATUS2 (optional)
        # etc.
        # Ending with "CASE NUMBER PATTERN" or "Nearby Cases"
        
        history_section_match = re.search(r'HISTORY\s*\n(.*?)(?:CASE NUMBER PATTERN|Nearby Cases|$)', page_text, re.DOTALL | re.IGNORECASE)
        if history_section_match:
            history_text = history_section_match.group(1)
            lines = history_text.strip().split('\n')
            
            # Parse lines looking for date-status pairs
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                
                # Check if this line is a date (like "MAY 9, 2025" or "Oct 2, 2025")
                date_match = re.match(r'^([A-Za-z]+\s+\d+,?\s*\d{4})$', line)
                if date_match:
                    date_str = parse_date(date_match.group(1))
                    
                    # Look at the next line for the status
                    if i + 1 < len(lines):
                        status_line = lines[i + 1].strip()
                        
                        # Find which status keyword matches
                        found_status = None
                        for keyword in status_keywords:
                            if keyword.lower() in status_line.lower():
                                found_status = keyword
                                break
                        
                        # If no keyword match but the line looks like a status, use it
                        if not found_status and status_line and not re.match(r'^[A-Za-z]+\s+\d+,?\s*\d{4}$', status_line):
                            # Only use if it doesn't look like another date or noise
                            if len(status_line) > 3 and not status_line.startswith('Discover'):
                                found_status = status_line
                        
                        if found_status and date_str:
                            # Avoid duplicates
                            existing = [(e['date'], e['status']) for e in timeline_entries]
                            if (date_str, found_status) not in existing:
                                timeline_entries.append({
                                    'date': date_str,
                                    'status': found_status
                                })
                        i += 2  # Skip both date and status lines
                        continue
                
                i += 1
        
        # Fallback: Look for the current USCIS message if we still don't have enough entries
        if len(timeline_entries) < 2:
            for keyword in status_keywords:
                # Try to find date after keyword (e.g., "Interview Cancelled...On May 9, 2025")
                pattern = rf'{re.escape(keyword)}.*?(?:On\s+)?([A-Za-z]+\s+\d+,?\s*\d{4})'
                match = re.search(pattern, page_text, re.IGNORECASE | re.DOTALL)
                if match:
                    status_date = parse_date(match.group(1))
                    existing = [(e['date'], e['status']) for e in timeline_entries]
                    if (status_date, keyword) not in existing:
                        timeline_entries.append({
                            'date': status_date,
                            'status': keyword
                        })
        
        return timeline_entries
        
    except Exception as e:
        print(f"    ❌ Error getting timeline for {receipt_number}: {str(e)}")
        return []

def parse_timeline_entries(timeline_entries, receipt_number, check_timestamp):
    """Convert timeline entries to exactly 3 status types max per case"""
    history_records = []
    
    if not timeline_entries:
        return history_records
    
    # Filter out entries with invalid dates (must be YYYY-MM-DD format)
    valid_entries = []
    for entry in timeline_entries:
        date_str = entry.get('date', '')
        # Valid date format check (YYYY-MM-DD)
        if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
            valid_entries.append(entry)
    
    if not valid_entries:
        return history_records
    
    # Sort timeline entries by date to ensure proper order
    sorted_entries = sorted(valid_entries, key=lambda x: x['date'])
    
    # Identify key entries
    case_received = None
    interview_cancelled = None
    last_status = None
    
    # Find interview_cancelled entry
    for entry in sorted_entries:
        status_lower = entry['status'].lower()
        if ('interview' in status_lower and 
            ('cancelled' in status_lower or 'canceled' in status_lower)):
            interview_cancelled = entry
            break  # Take the first one found (in chronological order)
    
    # Determine case_received: First entry that is NOT interview_cancelled
    for entry in sorted_entries:
        status_lower = entry['status'].lower()
        if not ('interview' in status_lower and 
                ('cancelled' in status_lower or 'canceled' in status_lower)):
            case_received = entry
            break
    
    # If no non-interview-cancelled entry found, don't report case_received
    # (because the first entry would be interview_cancelled, which is weird)
    
    # Determine last_status: Last entry in the timeline
    last_status = sorted_entries[-1] if sorted_entries else None
    
    # Build records, avoiding duplicates
    added_entries = set()  # Track (date, status) pairs already added
    
    # 1. Case received (if found and is not interview_cancelled)
    if case_received:
        key = (case_received['date'], case_received['status'])
        added_entries.add(key)
        history_records.append({
            'receipt_number': receipt_number,
            'status': case_received['status'],
            'status_date': case_received['date'],
            'status_type': 'case_received',
            'scraped_at': check_timestamp
        })
    
    # 2. Interview cancelled (if found)
    if interview_cancelled:
        key = (interview_cancelled['date'], interview_cancelled['status'])
        if key not in added_entries:
            added_entries.add(key)
            history_records.append({
                'receipt_number': receipt_number,
                'status': interview_cancelled['status'],
                'status_date': interview_cancelled['date'],
                'status_type': 'interview_cancelled',
                'scraped_at': check_timestamp
            })
    
    # 3. Last status (only if different from both case_received AND interview_cancelled)
    if last_status:
        key = (last_status['date'], last_status['status'])
        if key not in added_entries:
            history_records.append({
                'receipt_number': receipt_number,
                'status': last_status['status'],
                'status_date': last_status['date'],
                'status_type': 'last_status',
                'scraped_at': check_timestamp
            })
    
    return history_records

def clean_existing_history(df_history):
    """Clean existing history to ensure max 3 entries per case with no duplicates"""
    if df_history.empty:
        return df_history
    
    print("🧹 Cleaning existing history to ensure max 3 entries per case...")
    original_count = len(df_history)
    
    # Remove old entries with "unknown" status_type (from previous script versions)
    df_clean = df_history[df_history['status_type'] != 'unknown'].copy()
    
    cleaned_history = []
    
    for case in df_clean['receipt_number'].unique():
        case_entries = df_clean[df_clean['receipt_number'] == case].sort_values('status_date')
        
        # Get one entry of each type (prioritizing most recent scraped_at)
        case_received = case_entries[case_entries['status_type'] == 'case_received'].tail(1)
        interview_cancelled = case_entries[case_entries['status_type'] == 'interview_cancelled'].tail(1)
        last_status = case_entries[case_entries['status_type'] == 'last_status'].tail(1)
        
        # Add them in order
        for df_subset in [case_received, interview_cancelled, last_status]:
            if not df_subset.empty:
                cleaned_history.append(df_subset.iloc[0])
    
    if cleaned_history:
        df_result = pd.DataFrame(cleaned_history)
        # Sort by receipt number and status date
        df_result = df_result.sort_values(['receipt_number', 'status_date']).reset_index(drop=True)
    else:
        df_result = pd.DataFrame(columns=['receipt_number', 'status', 'status_date', 'status_type', 'scraped_at'])
    
    removed_count = original_count - len(df_result)
    if removed_count > 0:
        print(f"🧹 Removed {removed_count} duplicate/old entries, kept {len(df_result)} clean entries")
    
    return df_result

def main():
    """Main function to track cases and maintain complete timeline history"""
    
    # Load similar cases
    try:
        with open('similar.txt', 'r') as f:
            cases = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print("similar.txt not found!")
        return
    
    # Sort case numbers for consistent processing order
    cases = sorted(cases)
    print(f"Tracking {len(cases)} similar cases (sorted alphabetically)...")
    print("📋 Collecting only 3 key status types: case_received, interview_cancelled, last_status")
    print("🌐 Using MyCasesHub.com as data source")
    
    # Load existing history or create new with updated columns
    history_file = 'similar_cases_history.csv'
    if os.path.exists(history_file):
        df_history = pd.read_csv(history_file)
        print(f"📚 Loaded existing history with {len(df_history)} entries")
        # Add status_type column if it doesn't exist (for backward compatibility)
        if 'status_type' not in df_history.columns:
            df_history['status_type'] = 'unknown'
        # Clean up duplicates and ensure max 3 entries per case
        df_history = clean_existing_history(df_history)
    else:
        df_history = pd.DataFrame(columns=['receipt_number', 'status', 'status_date', 'status_type', 'scraped_at'])
        print("📚 Starting new history file")
    
    # Set up the Selenium driver
    print("🚀 Starting browser (headless mode)...")
    driver = setup_driver()
    
    try:
        # Check timeline for each case
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        all_new_entries = []
        cases_with_changes = []
        
        for i, case in enumerate(cases, 1):
            print(f"Checking {i}/{len(cases)}: {case}")
            
            # Get complete timeline from the website
            timeline_entries = get_case_timeline(driver, case)
            
            if not timeline_entries:
                print(f"  ❌ Could not get timeline for {case}")
                time.sleep(1)
                continue
            
            print(f"  📊 Found {len(timeline_entries)} timeline entries")
            for entry in timeline_entries:
                print(f"      → {entry['date']}: {entry['status']}")
            
            # Convert timeline to history records
            new_records = parse_timeline_entries(timeline_entries, case, current_time)
            
            # Check if we have any existing records for this case
            existing_case_records = df_history[df_history['receipt_number'] == case]
            
            if existing_case_records.empty:
                # First time seeing this case - add all timeline entries
                all_new_entries.extend(new_records)
                status_types = [r['status_type'] for r in new_records]
                cases_with_changes.append(f"{case}: NEW case with {len(new_records)} key status entries ({', '.join(status_types)})")
                print(f"  🆕 NEW: Added {len(new_records)} key status entries: {', '.join(status_types)}")
            else:
                # For existing cases, replace entries by status_type to avoid duplicates
                new_entries_for_case = []
                
                for record in new_records:
                    # Check if we already have this status_type for this case
                    existing_type_match = existing_case_records[
                        existing_case_records['status_type'] == record['status_type']
                    ]
                    
                    # If we don't have this status_type, or if the status/date is different, add it
                    if existing_type_match.empty:
                        new_entries_for_case.append(record)
                    else:
                        # Check if the status or date has changed
                        existing_entry = existing_type_match.iloc[0]
                        if (existing_entry['status_date'] != record['status_date'] or 
                            existing_entry['status'] != record['status']):
                            # Remove old entry of this type and add new one
                            df_history.drop(existing_type_match.index, inplace=True)
                            new_entries_for_case.append(record)
                
                if new_entries_for_case:
                    all_new_entries.extend(new_entries_for_case)
                    status_types = [r['status_type'] for r in new_entries_for_case]
                    cases_with_changes.append(f"{case}: {len(new_entries_for_case)} key status entries updated ({', '.join(status_types)})")
                    print(f"  🔄 UPDATED: {len(new_entries_for_case)} key status entries: {', '.join(status_types)}")
                else:
                    print(f"  ✅ No changes detected")
            
            # Be respectful with delays
            if i < len(cases):
                time.sleep(1.5)  # Slightly longer delay for the new site
        
    finally:
        # Always close the browser
        print("🔒 Closing browser...")
        driver.quit()
    
    # Add new entries to history and save (even if no new entries, save cleaned data)
    if all_new_entries:
        df_new = pd.DataFrame(all_new_entries)
        df_history = pd.concat([df_history, df_new], ignore_index=True)
        # Sort by receipt number and status date for better organization
        df_history = df_history.sort_values(['receipt_number', 'status_date']).reset_index(drop=True)
        print(f"\n📝 Added {len(all_new_entries)} new key status entries to history")
    else:
        print(f"\n📝 No new key status entries to add")
    
    # Always save the current state (including any cleaned data)
    df_history = df_history.sort_values(['receipt_number', 'status_date']).reset_index(drop=True)
    df_history.to_csv(history_file, index=False)
    
    # Print summary
    print(f"\n📊 Summary ({current_time}):")
    total_cases = df_history['receipt_number'].nunique()
    total_entries = len(df_history)
    print(f"  Total cases tracked: {total_cases}")
    print(f"  Total key status entries: {total_entries}")
    if total_cases > 0:
        print(f"  Average key status entries per case: {total_entries/total_cases:.1f}")
    else:
        print(f"  Average key status entries per case: 0")
    
    # Print cases with changes
    if cases_with_changes:
        print(f"\n🔄 Cases with new key status entries:")
        for change in cases_with_changes:
            print(f"  {change}")
    else:
        print("\n✅ No new key status entries detected")
    
    # Print timeline summary for each case
    print(f"\n📈 Key Status Summary:")
    for case in cases:
        case_history = df_history[df_history['receipt_number'] == case].sort_values('status_date')
        if not case_history.empty:
            print(f"  {case}: {len(case_history)} key status entries")
            # Show entries by type
            for _, entry in case_history.iterrows():
                status_type = entry.get('status_type', 'unknown')
                print(f"    {status_type}: {entry['status_date']} - {entry['status']}")
        else:
            print(f"  {case}: No key status entries found")
    
    print(f"\n💾 Key status history saved to: {history_file}")

if __name__ == "__main__":
    main()
