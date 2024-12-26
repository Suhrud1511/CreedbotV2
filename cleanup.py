import os
import pymongo
from datetime import datetime
from dotenv import load_dotenv
import logging

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def cleanup_database():
    try:
        # Connect to MongoDB
        client = pymongo.MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"))
        db = client["creed_management"]
        
        # Store current counter value
        current_counter = db.counters.find_one({"_id": "ride_id"})
        
        # Delete all documents from collections while preserving collections
        collections_to_clean = ["users", "rides"]
        
        for collection_name in collections_to_clean:
            result = db[collection_name].delete_many({})
            logger.info(f"Deleted {result.deleted_count} documents from {collection_name}")
        
        # Reset but preserve ride counter
        if current_counter:
            db.counters.update_one(
                {"_id": "ride_id"},
                {"$set": {"seq": 200}},  # Reset to initial value
                upsert=True
            )
            logger.info("Reset ride counter to initial value")
        
        logger.info("Database cleanup completed successfully")
        
    except Exception as e:
        logger.error(f"Error during database cleanup: {e}")
        raise
    finally:
        if 'client' in locals():
            client.close()
            logger.info("Database connection closed")

if __name__ == "__main__":
    confirm = input("This will delete ALL data from the database. Are you sure? (yes/no): ")
    if confirm.lower() == 'yes':
        cleanup_database()
        print("Database cleanup completed.")
    else:
        print("Operation cancelled.")