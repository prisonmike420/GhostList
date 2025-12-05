import os
import logging
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def verify_connection():
    # Load environment variables
    load_dotenv()
    
    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_KEY')
    
    print("-" * 50)
    print("SUPABASE CONNECTION VERIFICATION")
    print("-" * 50)
    
    if not url:
        print("❌ ERROR: SUPABASE_URL is missing from environment or .env file.")
    else:
        print(f"✅ SUPABASE_URL found: {url[:15]}...")
        
    if not key:
        print("❌ ERROR: SUPABASE_KEY is missing from environment or .env file.")
    else:
        print(f"✅ SUPABASE_KEY found: {key[:10]}...")
        
    if not url or not key:
        print("\nPlease create a .env file with your Supabase credentials.")
        return
        
    try:
        from supabase import create_client
        msg = "Attempting to connect to Supabase..."
        print(f"\n{msg}")
        
        client = create_client(url, key)
        
        # Try a simple query
        print("Executing simple query (selecting 1 row from 'subscribers')...")
        try:
            # We assume 'subscribers' table exists based on supabase_client.py
            result = client.table('subscribers').select('id', count='exact').limit(1).execute()
            print("✅ Connection successful!")
            print(f"✅ Query executed successfully. Found {len(result.data)} rows (limit 1).")
            if result.count is not None:
                print(f"ℹ️ Total count in table: {result.count}")
                
        except Exception as query_err:
            print(f"⚠️ Connection established, but query failed: {query_err}")
            print("Possible causes: Table 'subscribers' does not exist or RLS policies prevent access.")
            
    except Exception as e:
        print(f"❌ Failed to initialize Supabase client: {e}")

if __name__ == "__main__":
    verify_connection()
