import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import requests
import zipfile
import io
from datetime import datetime, timedelta
import os
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 1. Credentials Setup
creds_json = os.environ.get('GCP_CREDENTIALS')

if not creds_json:
    logger.error("CRITICAL: GCP_CREDENTIALS secret missing!")
    exit(1)

try:
    creds_dict = json.loads(creds_json)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    logger.info("Successfully authenticated with Google Sheets")
except Exception as e:
    logger.error(f"Authentication failed: {str(e)}")
    exit(1)

# Google Sheet Configuration
spreadsheet_id = "1qtM0PU5NQCrOJo_9buWfppWyTEtvHgtY9ZngPyUjrXI"
try:
    worksheet = client.open_by_key(spreadsheet_id).worksheet("Top 250 Stocks")
    logger.info("Successfully connected to worksheet")
except Exception as e:
    logger.error(f"Failed to connect to worksheet: {str(e)}")
    exit(1)

# 2. NSE Data Fetcher
def fetch_bhavcopy_for_date(date_obj):
    """
    Fetch NSE BhavCopy data for a given date.
    
    Args:
        date_obj: datetime object
        
    Returns:
        List of lists containing [Symbol, Turnover, Close Price] or None if failed
    """
    date_str = date_obj.strftime("%Y%m%d")
    url = f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{date_str}_F_0000.csv.zip"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }
    
    try:
        logger.info(f"Fetching BhavCopy for date: {date_str}")
        response = requests.get(url, headers=headers, timeout=20)
        
        if response.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                csv_filename = z.namelist()[0]
                with z.open(csv_filename) as f:
                    df = pd.read_csv(f)
                    
                    # Remove extra spaces from column names
                    df.columns = [c.strip() for c in df.columns]
                    
                    # Identify relevant columns with fallback options
                    sym_col = next((c for c in ['TckrSymb', 'SYMBOL'] if c in df.columns), None)
                    close_col = next((c for c in ['ClsPric', 'CLOSE'] if c in df.columns), None)
                    series_col = next((c for c in ['SctySrs', 'SERIES'] if c in df.columns), None)
                    turnover_col = next((c for c in ['TtlTrfVal', 'TtlTrdVal', 'TURNOVER_LACS', 'TURNOVER'] if c in df.columns), None)
                    
                    # Validate required columns exist
                    if not all([sym_col, close_col, turnover_col]):
                        logger.warning(f"Missing required columns. Found: sym={sym_col}, close={close_col}, turnover={turnover_col}")
                        return None
                    
                    # Filter for equity series only
                    if series_col:
                        df = df[df[series_col].astype(str).str.strip() == 'EQ']
                    
                    # Remove ETFs, BEES, and other non-stock instruments
                    filter_keywords = 'BEES|ETF|GOLD|LIQUID'
                    df = df[~df[sym_col].astype(str).str.contains(filter_keywords, case=False, na=False)]
                    
                    # Convert turnover to numeric and drop NaN values
                    df[turnover_col] = pd.to_numeric(df[turnover_col], errors='coerce')
                    df = df.dropna(subset=[turnover_col])
                    
                    # Sort by turnover and get top 250
                    df_top = df.sort_values(by=turnover_col, ascending=False).head(250)
                    result = df_top[[sym_col, turnover_col, close_col]].values.tolist()
                    
                    logger.info(f"Successfully fetched {len(result)} records for {date_str}")
                    return result
        else:
            logger.warning(f"HTTP {response.status_code} for date {date_str}")
            return None
            
    except Exception as e:
        logger.error(f"Error fetching data for {date_str}: {str(e)}")
        return None

# 3. Execution Logic
def main():
    """Main execution function"""
    date = datetime.now()
    data_to_insert = None
    fetched_date_str = ""
    
    # Try to fetch data for last 7 days (skip weekends)
    for i in range(7):
        test_date = date - timedelta(days=i)
        
        # Skip weekends (5=Saturday, 6=Sunday)
        if test_date.weekday() >= 5:
            continue
        
        data_to_insert = fetch_bhavcopy_for_date(test_date)
        
        if data_to_insert:
            fetched_date_str = test_date.strftime('%d-%b-%Y')
            logger.info(f"Data fetched successfully for {fetched_date_str}")
            break
    
    # 4. Update Google Sheet
    if data_to_insert:
        try:
            # Clear existing data
            worksheet.batch_clear(['A2:C251'])
            logger.info("Cleared existing data from sheet")
            
            # Insert new data
            worksheet.update('A2', data_to_insert)
            logger.info("Inserted new data to sheet")
            
            # Update status message with IST timestamp
            ist_now = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime('%d-%b %H:%M')
            status_msg = f"Data Date: {fetched_date_str} | Last Update: {ist_now} (IST)"
            worksheet.update('K2', [[status_msg]])
            
            logger.info(f"SUCCESS: Sheet updated with {len(data_to_insert)} records for {fetched_date_str}!")
            print(f"✓ Sheet updated successfully with Turnover Data for {fetched_date_str}!")
            
        except Exception as e:
            logger.error(f"Google Sheet Error: {str(e)}")
            print(f"✗ Failed to update sheet: {str(e)}")
    else:
        logger.warning("No data available for the last 7 days")
        print("✗ No data could be fetched for the last 7 days. Check your internet connection and NSE availability.")

if __name__ == "__main__":
    main()
